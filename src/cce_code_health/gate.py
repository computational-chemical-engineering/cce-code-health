"""Behaviour gate: is base..head a behaviour-preserving change?

Configured in the base's `.codehealth/config.toml` (a change cannot loosen its own gate):

    [gate]
    frozen = ["src/pkg/identity/*"]   # byte change fails (fnmatch, '*' matches '/')
    frozen_cmd = "python scripts/list_frozen.py"   # optional: prints more frozen paths
    forbid = ["tests/*", ".codehealth/*"]          # a refactor step may not touch these
    literals = true                   # string literals of touched files unchanged (multiset)
    public_api = true                 # public names and signatures unchanged
    [gate.env]                        # applied to every command (determinism pins)
    PYTHONHASHSEED = "0"
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

Placeholders in cmd: {repo} {base} {out} {files} (touched head paths, shell-quoted).
Reference tiers run at base twice (determinism check) in a temporary worktree, then at head.
"""
from __future__ import annotations

import ast
import os
import shlex
import statistics
import subprocess
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


def _public_api(source: str) -> dict[str, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"<unparsable>": ""}
    api = {}

    def sig(fn):
        return f"({ast.unparse(fn.args)})" + (f" -> {ast.unparse(fn.returns)}" if fn.returns else "")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            api[node.name] = sig(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            api[node.name] = "class(" + ", ".join(ast.unparse(b) for b in node.bases) + ")"
            for m in node.body:
                if (isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and (not m.name.startswith("_") or m.name == "__init__")):
                    api[f"{node.name}.{m.name}"] = sig(m)
    return api


def static_checks(repo: Path, base: str, cfg: config.Config, changes) -> list[Check]:
    g = cfg.gate
    checks = []
    touched = {p for _, bp, hp in changes for p in (bp, hp) if p}
    frozen = list(g.get("frozen", []))
    if g.get("frozen_cmd"):
        r = subprocess.run(g["frozen_cmd"], shell=True, cwd=repo, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"frozen_cmd failed ({r.returncode}): {r.stderr.strip()[:300]}")
        frozen += [line.strip() for line in r.stdout.splitlines() if line.strip()]
    hit = sorted(p for p in touched if _match(p, frozen))
    checks.append(Check("frozen", not hit, ", ".join(hit[:10])))
    hit = sorted(p for p in touched if _match(p, g.get("forbid", [])))
    checks.append(Check("forbid", not hit, ", ".join(hit[:10])))

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
            ab = _public_api(tb[bp]) if bp in tb else {}
            ah = _public_api(th[hp]) if hp in th else {}
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


def _base_tree(repo: Path, base: str, holder: list) -> Path:
    if not holder:
        holder.append(Path(tempfile.mkdtemp(prefix="codehealth-base-")))
        gitio.git(repo, "worktree", "add", "--detach", "-f", str(holder[0]), base)
    return holder[0]


def _reference(t, repo, base, files, env, log, mode, tree) -> tuple[bool, str]:
    names, timeout, runs = t["outputs"], t.get("timeout"), []
    for cwd in (tree, tree, repo):
        out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
        rc, _ = _run(_fill(t["cmd"], repo=str(cwd), base=base, files=files, out=str(out)),
                     cwd, env, log, timeout)
        if rc != 0:
            return False, f"exit {rc} at {'head' if cwd == repo else 'base'}"
        runs.append(_outputs(out, names))
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


def _benchmark(t, repo, base, files, env, log, mode, tree) -> tuple[bool, str]:
    """Alternate base and head runs; compare median wall times."""
    repeat, timeout = int(t.get("repeat", 5)), t.get("timeout")
    times = {"base": [], "head": []}
    for _ in range(repeat):
        for side, cwd in (("base", tree), ("head", repo)):
            rc, sec = _run(_fill(t["cmd"], repo=str(cwd), base=base, files=files, out=""),
                           cwd, env, log, timeout)
            if rc != 0:
                return False, f"exit {rc} at {side}"
            times[side].append(sec)
    med = {k: statistics.median(v) for k, v in times.items()}
    spread = {k: (max(v) - min(v)) / med[k] if med[k] else 0.0 for k, v in times.items()}
    speedup = med["base"] / med["head"] if med["head"] else float("inf")
    detail = (f"speedup {speedup:.3f} (base {med['base']:.3g} s ±{100 * spread['base']:.0f}%, "
              f"head {med['head']:.3g} s ±{100 * spread['head']:.0f}%, n={repeat})")
    if mode == "optimize":
        need = float(t.get("min_speedup", 1.0))
        return speedup >= need, detail + f", need >= {need}"
    allowed = float(t.get("max_slowdown", 0.03))
    return speedup >= 1 / (1 + allowed), detail + f", max slowdown {allowed:.0%}"


def run_tiers(repo: Path, base: str, cfg: config.Config, changes, cadence: str, log,
              mode: str = "refactor") -> list[Check]:
    g = cfg.gate
    env = {k: str(v) for k, v in g.get("env", {}).items()}
    files = " ".join(shlex.quote(hp) for _, _, hp in changes if hp)
    tiers = [t for t in g.get("tier", [])
             if (cadence == "campaign" or t.get("cadence", "step") == "step")
             and mode in t.get("modes", ["refactor", "optimize"])]
    checks, holder = [], []
    try:
        for t in tiers:
            name, kind, t0 = t["name"], t.get("kind", "command"), time.monotonic()
            if kind == "command":
                rc, _ = _run(_fill(t["cmd"], repo=str(repo), base=base, files=files, out=""),
                             repo, env, log, t.get("timeout"))
                ok, detail = rc == 0, (f"exit {rc}" if rc else "")
            elif kind in ("reference", "benchmark"):
                fn = _reference if kind == "reference" else _benchmark
                ok, detail = fn(t, repo, base, files, env, log, mode, _base_tree(repo, base, holder))
            else:
                raise ValueError(f"tier {name}: unknown kind {kind!r}")
            checks.append(Check(name, ok, detail, time.monotonic() - t0))
            log.write(f"--- tier {name}: {'PASS' if ok else 'FAIL'} {detail}\n")
            if not ok and not g.get("keep_going", False):
                break
    finally:
        for tree in holder:
            gitio.git(repo, "worktree", "remove", "--force", str(tree))
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
    mode "optimize": references within each tier's declared `tolerance`, benchmark must
    reach min_speedup. Debt may not rise in either mode.
    """
    if mode not in ("refactor", "optimize"):
        raise ValueError(f"unknown mode {mode!r}")
    base = gitio.merge_base(repo, base_ref, "HEAD")
    cfg = (config.parse(config_override.read_text(), f"override:{config_override}")
           if config_override else config.load(repo, base))
    changes = gitio.changes(repo, base, None)
    checks = static_checks(repo, base, cfg, changes) + [debt_check(repo, base, cfg, changes)]
    if all(c.ok for c in checks):
        with open(log_path, "w") as log:
            checks += run_tiers(repo, base, cfg, changes, cadence, log, mode)
    return checks, base
