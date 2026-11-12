"""Behaviour gate: is base..head a behaviour-preserving change?

Configured in the base's `.codehealth/config.toml` (a change cannot loosen its own gate):

    [gate]
    frozen = ["src/pkg/identity/*"]   # byte change fails (fnmatch, '*' matches '/')
    frozen_cmd = "python scripts/list_frozen.py"   # optional: prints more frozen paths (runs in the base tree)
    forbid = ["tests/*"]              # a step may not touch these; ".codehealth/*" is always forbidden
    literals = true                   # string literals of touched files unchanged (multiset)
    public_api = true                 # public names, decorators, signatures, constants, __all__,
                                      # re-exports and class fields unchanged
    [gate.env]                        # applied to every command (determinism pins)
    PYTHONHASHSEED = "0"
    OMP_NUM_THREADS = "1"             # benchmark tiers require these three pins
    OPENBLAS_NUM_THREADS = "1"
    MKL_NUM_THREADS = "1"
    [[gate.tier]]
    name = "identity"
    cadence = "step"                  # step (every step) | campaign (only with --cadence campaign)
    kind = "command"                  # command: exit code 0 passes
    cmd = "python scripts/identity_check.py"
    [[gate.tier]]
    name = "golden"
    kind = "reference"                # outputs at head must equal base, byte for byte
    cmd = "python run_case.py --out {out}"
    outputs = ["result.npy"]
    [[gate.tier]]
    name = "speed"
    kind = "benchmark"
    cmd = "python bench.py"
    primary = true                    # optimize mode: only the primary tier must reach min_speedup
    min_speedup = 1.2
    max_slowdown = 0.03               # every other benchmark tier: may not slow down beyond this

Placeholders in cmd: {repo} {base} {out} {files} (touched head paths, shell-quoted).
Every touched path (base or head side) must be in scope. Reference tiers run at base twice
(determinism check) in a worktree under `<git-common-dir>/codehealth/base-<sha>`, then at head.
Benchmarks: one untimed warm-up per side, then ABBA rounds; speedup is the median of the
paired ratios; each run gets a fresh TMPDIR and XDG_CACHE_HOME and may not write into the tree.

Empty gates fail: an empty diff at cadence step; optimize mode without a reference tier or
without a primary benchmark tier with min_speedup; refactor mode without a reference or
command tier. With `--config` (a trial) a passing gate exits 3, not 0.

Self-test: `codehealth gate --self-test [--mutate path:line]` mutates one numeric literal in an
in-scope file of a temporary worktree at HEAD and runs the reference and command tiers there
against HEAD. It passes (exit 0) only if every reference tier detects the mutation, which
shows the tiers exercise the checkout under test and not some installed copy.
"""
from __future__ import annotations

import ast
import os
import shlex
import statistics
import subprocess
import resource
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from . import config, gitio
from .compare import compare


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


def _match(path: str, patterns) -> bool:
    return any(fnmatchcase(path, p) for p in patterns)


# -- static rules ---------------------------------------------------------------------------

def _string_literals(source: str) -> Counter:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Counter({"<unparsable>": 1})
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                docstrings.add(id(b[0].value))
    return Counter(repr(n.value) for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes))
                   and id(n) not in docstrings)


def _decorators(node) -> str:
    return "".join(f"@{ast.unparse(d)} " for d in node.decorator_list)


def _public_api(source: str, path: str = "") -> dict[str, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"<unparsable>": ""}
    api = {}

    def sig(fn):
        return (_decorators(fn) + f"({ast.unparse(fn.args)})"
                + (f" -> {ast.unparse(fn.returns)}" if fn.returns else ""))

    def dunder(name):
        return name.startswith("__") and name.endswith("__")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            api[node.name] = sig(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            api[node.name] = _decorators(node) + "class(" + ", ".join(ast.unparse(b) for b in node.bases) + ")"
            for m in node.body:
                if (isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and (not m.name.startswith("_") or dunder(m.name))):
                    api[f"{node.name}.{m.name}"] = sig(m)
                elif (isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name)
                      and not m.target.id.startswith("_")):
                    api[f"{node.name}.{m.target.id}"] = f"field: {ast.unparse(m.annotation)}"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            for name in names:
                if name == "__all__" and node.value is not None:
                    try:
                        api["__all__"] = repr(sorted(ast.literal_eval(node.value)))
                    except (ValueError, TypeError):
                        api["__all__"] = ast.unparse(node.value)
                elif not name.startswith("_"):
                    api[name] = "const"
        elif (isinstance(node, ast.ImportFrom) and Path(path).name == "__init__.py"
              and node.module != "__future__"):
            for a in node.names:
                name = a.asname or a.name
                if name != "*" and not name.startswith("_"):
                    api[name] = "re-export"
    return api


def _common_dir(repo: Path) -> Path:
    out = gitio.git(repo, "rev-parse", "--git-common-dir").decode().strip()
    return (repo / out).resolve() / "codehealth"


def _remove_tree(repo: Path, tree: Path) -> None:
    try:
        gitio.git(repo, "worktree", "remove", "--force", str(tree))
    except gitio.GitError:
        pass
    shutil.rmtree(tree, ignore_errors=True)
    try:
        gitio.git(repo, "worktree", "prune")
    except gitio.GitError:
        pass


def base_tree(repo: Path, sha: str) -> Path:
    """Worktree of `sha` under the git common dir; reused for the same sha, others removed."""
    root = _common_dir(repo)
    tree = root / f"base-{sha}"
    if (tree / ".git").exists():
        gitio.git(tree, "reset", "--hard", "-q", sha)
        gitio.git(tree, "clean", "-fdxq")
        return tree
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob("base-*"):
        _remove_tree(repo, old)
    gitio.git(repo, "worktree", "add", "--detach", "-f", str(tree), sha)
    return tree


def static_checks(repo: Path, base: str, cfg: config.Config, changes) -> list[Check]:
    g = cfg.gate
    checks = []
    touched = {p for _, bp, hp in changes for p in (bp, hp) if p}
    frozen = list(g.get("frozen", []))
    if g.get("frozen_cmd"):
        r = subprocess.run(g["frozen_cmd"], shell=True, cwd=base_tree(repo, base),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"frozen_cmd failed ({r.returncode}): {r.stderr.strip()[:300]}")
        frozen += [line.strip() for line in r.stdout.splitlines() if line.strip()]
    hit = sorted(p for p in touched if _match(p, frozen))
    checks.append(Check("frozen", not hit, ", ".join(hit[:10])))
    hit = sorted(p for p in touched if _match(p, list(g.get("forbid", [])) + [".codehealth/*"]))
    checks.append(Check("forbid", not hit, ", ".join(hit[:10])))
    hit = sorted(p for p in touched if not cfg.in_scope(p))
    checks.append(Check("scope", not hit, "out of scope: " + ", ".join(hit[:10]) if hit else ""))

    py = [(bp, hp) for _, bp, hp in changes
          if (bp and cfg.in_scope(bp)) or (hp and cfg.in_scope(hp))]
    tb = gitio.read_files(repo, base, [bp for bp, _ in py if bp])
    th = gitio.read_files(repo, None, [hp for _, hp in py if hp])
    if g.get("literals", False):
        lb, lh = Counter(), Counter()
        for t in tb.values():
            lb += _string_literals(t)
        for t in th.values():
            lh += _string_literals(t)
        gone, new = lb - lh, lh - lb
        detail = "; ".join([f"removed {k}" for k in list(gone)[:3]] + [f"added {k}" for k in list(new)[:3]])
        checks.append(Check("literals", not gone and not new, detail[:400]))
    if g.get("public_api", False):
        problems = []
        for bp, hp in py:
            ab = _public_api(tb[bp], bp) if bp in tb else {}
            ah = _public_api(th[hp], hp) if hp in th else {}
            where = hp or bp
            if bp and hp and bp != hp and ab:
                problems.append(f"{bp}: module moved to {hp}")
            for name, s in ab.items():
                if name not in ah:
                    problems.append(f"{where}: {name} removed")
                elif ah[name] != s:
                    problems.append(f"{where}: {name}{s} -> {ah[name]}")
            problems += [f"{where}: {n} added (make it private?)" for n in ah if n not in ab]
        checks.append(Check("public_api", not problems, "; ".join(problems[:5])))
    return checks


def _selected(cfg: config.Config, cadence: str, mode: str) -> list[dict]:
    return [t for t in cfg.gate.get("tier", [])
            if (cadence == "campaign" or t.get("cadence", "step") == "step")
            and mode in t.get("modes", ["refactor", "optimize"])]


THREAD_PINS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def config_checks(cfg: config.Config, changes, cadence: str, mode: str) -> list[Check]:
    """Fail gates that would pass vacuously."""
    tiers = _selected(cfg, cadence, mode)
    kinds = [t.get("kind", "command") for t in tiers]
    problems = []
    if cadence == "step" and not changes:
        problems.append("nothing to gate (empty diff)")
    if mode == "optimize":
        if "reference" not in kinds:
            problems.append("optimize mode needs a reference tier")
        if not any(t.get("kind") == "benchmark" and t.get("primary") and "min_speedup" in t for t in tiers):
            problems.append("optimize mode needs a benchmark tier with primary = true and min_speedup")
    elif not ({"reference", "command"} & set(kinds)):
        problems.append("refactor mode needs a reference or command tier")
    if "benchmark" in kinds:
        env = cfg.gate.get("env", {})
        unpinned = [k for k in THREAD_PINS if k not in env]
        if unpinned:
            problems.append("config error: benchmark tiers need [gate.env] to set " + ", ".join(unpinned))
    return [Check("config", not problems, "; ".join(problems))]


# -- command and reference tiers --------------------------------------------------------------

def _run(cmd: str, cwd: Path, env: dict, log, timeout: float | None) -> tuple[int, float]:
    t0 = time.monotonic()
    log.write(f"\n$ (cwd {cwd}) {cmd}\n")
    log.flush()
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, env={**os.environ, **env},
                           stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        log.write(f"TIMEOUT after {timeout} s\n")
        rc = 124
    log.write(f"exit {rc}\n")
    return rc, time.monotonic() - t0


def _fill(cmd: str, **kw) -> str:
    for k, v in kw.items():
        cmd = cmd.replace("{" + k + "}", v)
    return cmd


def _outputs(out: Path, names) -> dict[str, bytes | None]:
    return {n: (out / n).read_bytes() if (out / n).is_file() else None for n in names}


def _reference(t, repo, base, files, env, log, mode, tree) -> tuple[bool, str]:
    names, timeout, runs = t["outputs"], t.get("timeout"), []
    for cwd in (tree, tree, repo):
        out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
        try:
            rc, _ = _run(_fill(t["cmd"], repo=str(cwd), base=base, files=files, out=str(out)),
                         cwd, env, log, timeout)
            if rc != 0:
                return False, f"exit {rc} at {'head' if cwd == repo else 'base'}"
            runs.append(_outputs(out, names))
        finally:
            shutil.rmtree(out, ignore_errors=True)
    b1, b2, h = runs
    missing = [n for n in names if b1[n] is None or h[n] is None]
    if missing:
        return False, "missing output: " + ", ".join(missing)
    nondet = [n for n in names if b1[n] != b2[n]]
    if nondet:
        return False, "not deterministic at base: " + ", ".join(nondet)
    # Refactor mode is always bitwise; optimize mode uses the human-declared tolerance.
    tol = t.get("tolerance") if mode == "optimize" else None
    ok, details = True, []
    for n in names:
        good, detail = compare(b1[n], h[n], n, tol)
        ok &= good
        if detail != "bitwise equal":
            details.append(f"{n}: {detail}")
    return ok, "; ".join(details) or "bitwise equal"


def _status(cwd: Path) -> bytes:
    return gitio.git(cwd, "status", "--porcelain", "-uall")


def _bench_run(t, cwd, base, files, env, log) -> tuple[int, float, float, str]:
    """One run with fresh TMPDIR and XDG_CACHE_HOME: (rc, seconds, children CPU s, note)."""
    tmp, cache = tempfile.mkdtemp(prefix="codehealth-tmp-"), tempfile.mkdtemp(prefix="codehealth-cache-")
    try:
        before = _status(cwd)
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        rc, sec = _run(_fill(t["cmd"], repo=str(cwd), base=base, files=files, out=""), cwd,
                       {**env, "TMPDIR": tmp, "XDG_CACHE_HOME": cache}, log, t.get("timeout"))
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime)
        note = "benchmark wrote into the tree" if _status(cwd) != before else ""
        return rc, sec, cpu, note
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(cache, ignore_errors=True)


def _benchmark(t, repo, base, files, env, log, mode, tree) -> tuple[bool, str]:
    """Warm-up, then ABBA-ordered paired runs; speedup is the median of base_i / head_i."""
    repeat = int(t.get("repeat", 5))
    sides = {"base": tree, "head": repo}
    for side, cwd in sides.items():
        rc, _, _, _ = _bench_run(t, cwd, base, files, env, log)
        if rc != 0:
            return False, f"exit {rc} at {side} (warm-up)"
    times, cpu = {"base": [], "head": []}, {"base": 0.0, "head": 0.0}
    for i in range(repeat):
        for side in (("base", "head") if i % 2 == 0 else ("head", "base")):
            rc, sec, c, note = _bench_run(t, sides[side], base, files, env, log)
            if rc != 0:
                return False, f"exit {rc} at {side}"
            if note:
                return False, f"{note} ({side})"
            times[side].append(sec)
            cpu[side] += c
    ratios = [b / h if h else float("inf") for b, h in zip(times["base"], times["head"])]
    speedup = statistics.median(ratios)
    detail = (f"speedup {speedup:.3f} (paired min {min(ratios):.3f}, max {max(ratios):.3f}, n={repeat}; "
              f"base median {statistics.median(times['base']):.3g} s, head median "
              f"{statistics.median(times['head']):.3g} s; load1 {os.getloadavg()[0]:.2f}; "
              f"children cpu base {cpu['base']:.3g} s, head {cpu['head']:.3g} s)")
    if mode == "optimize" and t.get("primary", False):
        need = float(t.get("min_speedup", 1.0))
        return speedup >= need, detail + f", need >= {need}"
    allowed = float(t.get("max_slowdown", 0.03))
    return speedup >= 1 / (1 + allowed), detail + f", max slowdown {allowed:.0%}"


def run_tiers(repo: Path, base: str, cfg: config.Config, changes, cadence: str, log,
              mode: str = "refactor", keep_going: bool | None = None,
              kinds: tuple | None = None) -> list[Check]:
    g = cfg.gate
    env = {k: str(v) for k, v in g.get("env", {}).items()}
    files = " ".join(shlex.quote(hp) for _, _, hp in changes if hp)
    keep_going = g.get("keep_going", False) if keep_going is None else keep_going
    checks = []
    for t in _selected(cfg, cadence, mode):
        name, kind, t0 = t["name"], t.get("kind", "command"), time.monotonic()
        if kinds and kind not in kinds:
            continue
        if kind == "command":
            rc, _ = _run(_fill(t["cmd"], repo=str(repo), base=base, files=files, out=""),
                         repo, env, log, t.get("timeout"))
            ok, detail = rc == 0, (f"exit {rc}" if rc else "")
        elif kind in ("reference", "benchmark"):
            fn = _reference if kind == "reference" else _benchmark
            ok, detail = fn(t, repo, base, files, env, log, mode, base_tree(repo, base))
        else:
            raise ValueError(f"tier {name}: unknown kind {kind!r}")
        checks.append(Check(name, ok, detail, time.monotonic() - t0))
        log.write(f"--- tier {name}: {'PASS' if ok else 'FAIL'} {detail}\n")
        if not ok and not keep_going:
            break
    return checks


def debt_check(repo: Path, base: str, cfg: config.Config, changes) -> Check:
    """Debt over the touched in-scope files must not rise (both modes)."""
    from .cli import analyze_ref  # noqa: PLC0415 (cli imports this module)
    bp = {b for _, b, _ in changes if b and cfg.in_scope(b)}
    hp = {h for _, _, h in changes if h and cfg.in_scope(h)}
    db = sum(r["debt"] for r in analyze_ref(repo, base, cfg, bp).values())
    dh = sum(r["debt"] for r in analyze_ref(repo, None, cfg, hp).values())
    return Check("debt", dh <= db + 1e-9, f"{db:.1f} -> {dh:.1f}")


def gate(repo: Path, base_ref: str, cadence: str, log_path: Path,
         config_override: Path | None = None, mode: str = "refactor") -> tuple[list[Check], str]:
    """config_override is for local trials only; CI must use the base's committed config.

    mode "refactor": references bitwise, benchmark may not slow down beyond max_slowdown.
    mode "optimize": references within each tier's declared `tolerance`, the primary benchmark
    must reach min_speedup. Debt may not rise in either mode.
    """
    if mode not in ("refactor", "optimize"):
        raise ValueError(f"unknown mode {mode!r}")
    base = gitio.merge_base(repo, base_ref, "HEAD")
    cfg = (config.parse(config_override.read_text(), f"override:{config_override}")
           if config_override else config.load(repo, base))
    changes = gitio.changes(repo, base, None)
    checks = (config_checks(cfg, changes, cadence, mode) + static_checks(repo, base, cfg, changes)
              + [debt_check(repo, base, cfg, changes)])
    if all(c.ok for c in checks):
        with open(log_path, "w") as log:
            checks += run_tiers(repo, base, cfg, changes, cadence, log, mode)
    return checks, base


# -- self-test ----------------------------------------------------------------------------------

def _literal_sites(source: str, line: int | None = None) -> list[tuple[int, int, int, object]]:
    """(lineno, col, end_col, value) of numeric literals in function bodies, in source order."""
    tree = ast.parse(source)
    sites = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in fn.body:
                for n in ast.walk(stmt):
                    if (isinstance(n, ast.Constant) and type(n.value) in (int, float)
                            and n.lineno == n.end_lineno):
                        sites[(n.lineno, n.col_offset)] = (n.lineno, n.col_offset, n.end_col_offset, n.value)
    out = sorted(sites.values())
    if line is not None:
        return [s for s in out if s[0] == line]
    return [s for s in out if s[3] not in (0, 1)]


def mutate_source(source: str, site) -> tuple[str, str]:
    lineno, col, end, v = site
    new = repr(v + 1) if isinstance(v, int) else repr(v * 1.5 + 1.0)
    lines = source.split("\n")
    raw = lines[lineno - 1].encode()
    lines[lineno - 1] = (raw[:col] + new.encode() + raw[end:]).decode()
    return "\n".join(lines), f"line {lineno}: {v!r} -> {new}"


def self_test(repo: Path, cfg: config.Config, log_path: Path,
              mutate: str | None = None) -> tuple[bool, list[Check], str]:
    """Mutate one literal in a temporary HEAD worktree. With reference tiers, every one must
    detect it; without, at least one command tier not marked `behaviour = false` must."""
    head = gitio.rev(repo, "HEAD")
    root = _common_dir(repo)
    tree = root / "selftest"
    root.mkdir(parents=True, exist_ok=True)
    _remove_tree(repo, tree)
    gitio.git(repo, "worktree", "add", "--detach", "-f", str(tree), head)
    try:
        if mutate:
            path, _, line = mutate.rpartition(":")
            if not path or not line.isdigit():
                raise ValueError(f"--mutate expects path:line, got {mutate!r}")
            candidates = [path]
        else:
            path, line = None, None
            candidates = sorted(p for p in gitio.ls_files(repo, head) if cfg.in_scope(p))
        target = None
        for p in candidates:
            sites = _literal_sites((tree / p).read_text(), int(line) if mutate else None)
            if sites:
                target = (p, sites[0])
                break
        if target is None:
            raise RuntimeError("self-test: no numeric literal found to mutate")
        p, site = target
        text, what = mutate_source((tree / p).read_text(), site)
        (tree / p).write_text(text)
        what = f"{p} {what}"
        changes = gitio.changes(tree, head, None)
        with open(log_path, "w") as log:
            checks = run_tiers(tree, head, cfg, changes, "campaign", log, "refactor",
                               keep_going=True, kinds=("reference", "command"))
        tiers = _selected(cfg, "campaign", "refactor")
        refs = {t["name"] for t in tiers if t.get("kind") == "reference"}
        if refs:
            # Every reference tier must detect the mutation.
            missed = [c for c in checks if c.name in refs and c.ok]
            ok = not missed and len([c for c in checks if c.name in refs]) == len(refs)
            out = [Check(c.name, not c.ok, c.detail or "detected" if not c.ok else "missed", c.seconds)
                   for c in checks if c.name in refs]
        else:
            # No reference tier: at least one behaviour command tier must detect it.
            # Tiers marked `behaviour = false` (lint, format) do not count.
            beh = {t["name"] for t in tiers if t.get("kind", "command") == "command"
                   and t.get("behaviour", True)}
            out = [Check(c.name, not c.ok, "detected" if not c.ok else "missed", c.seconds)
                   for c in checks if c.name in beh]
            ok = any(c.ok for c in out)
            if not beh:
                out.append(Check("self-test", False, "no reference or behaviour command tier"))
        return ok, out, what
    finally:
        _remove_tree(repo, tree)
