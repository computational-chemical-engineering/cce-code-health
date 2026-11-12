"""Behaviour gate: is base..head a behaviour-preserving change?

Configured in the base's `.codehealth/config.toml` (a change cannot loosen its own gate):

    [gate]
    frozen = ["src/pkg/identity/*"]   # byte change fails (fnmatch, '*' matches '/')
    frozen_cmd = "python scripts/list_frozen.py"   # optional: prints more frozen paths (runs in the base tree)
    forbid = ["tests/*"]              # a step may not touch these; ".codehealth/*" is always forbidden
    keep_going = false                # true: run all tiers even after one fails (default: stop at the first)
    prefix = "PATH=.venv-codehealth/bin:$PATH"   # shell prefix prepended to every tier command
    literals = true                   # string literals of touched files unchanged (multiset)
    public_api = true                 # public names, decorators, signatures, constants, __all__,
                                      # re-exports and class fields unchanged
    [gate.env]                        # applied to every command (determinism pins)
    PYTHONHASHSEED = "0"
    # values (and prefix) may use {gitdir} (git common dir, absolute) and {repo} (the tree the command runs in)
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
    coverage = true                   # optional: the first head run is measured with coverage.py
    [[gate.tier]]
    name = "speed"
    kind = "benchmark"
    cmd = "python bench.py"
    primary = true                    # optimize mode: only the primary tier must reach min_speedup
    repeat = 5                        # ABBA pairs (default 5)
    max_repeat = 20                   # most pairs when extending (default 4 * repeat)
    min_speedup = 1.2
    max_slowdown = 0.03               # every other benchmark tier: may not slow down beyond this

Placeholders in cmd: {repo} {base} {out} {files} (touched head paths, shell-quoted).
Every touched path (base or head side) must be in scope. Patterns are fnmatch with '*' matching
'/', and "/**/" also matching a single "/" (one matcher for scope, frozen and forbid).

Two bases (`--base` and `--ref-base`): static checks, debt and numeric-literals use `--base`
(the last accepted step); reference and benchmark tiers compare head with `--ref-base` (the
campaign base; default: the same as `--base`), so tolerances and slowdowns cannot compound over
steps. Both are reduced to their merge-base with HEAD; the GATE line prints `ref-base=<sha10>`.

Reference tiers: the ref-base outputs come from a worktree under
`<git-common-dir>/codehealth/base-<sha>`, run twice (determinism check at base), and are cached
in `<git-common-dir>/codehealth/cache/<sha>/<tier>/` with a JSON manifest (cmd, outputs, env,
determinism result); the cache is reused when sha, cmd, outputs and env match. Head runs twice;
if the two differ the tier fails with "not deterministic at head". With `coverage = true` the
first head run is measured by coverage.py (a temporary .coveragerc with parallel data, and a
sitecustomize.py on PYTHONPATH with COVERAGE_PROCESS_START so every Python subprocess counts);
each in-scope function whose span overlaps an added or changed line of head (git diff -U0 against
`--base`) must have at least one executed body line, else "reference does not execute: ...".
No data: "no coverage data". Deleted functions are ignored. coverage must be installed in the
command's interpreter.

Benchmarks are never cached (timing must stay paired): one untimed warm-up per side, then ABBA
rounds, ref-base tree against head; speedup is the median of the paired ratios base_i / head_i;
each run gets a fresh TMPDIR and XDG_CACHE_HOME and may not write into the tree. Refactor mode
(and non-primary tiers): median >= 1 / (1 + max_slowdown). Optimize mode, primary tier: accepted
when `speedup_lower_bound` (distribution-free one-sided 95 % lower bound of the median, from
order statistics: the k-th smallest ratio for the largest k with P(Binomial(n, 0.5) <= k-1) <=
0.05; n < 5 is "insufficient" and gives the minimum) reaches min_speedup. If only the median
does, ABBA rounds (2 runs per side) are added until the bound passes or n reaches `max_repeat`.
The detail reports median, bound, n and whether rounds were extended.

Informational check `numeric-literals` (always ok): per touched in-scope .py file the numeric
constants (int, float, complex; not bool) removed or added between --base and head, at most 10
entries then "+N more".

`codehealth config --check [--config FILE]` validates the config (unknown keys, tier kinds,
required keys, tolerance keys, thread pins, one primary benchmark, cadence and modes).
With `--config` (trial), a frozen_cmd that fails in the base tree is run in the working tree
instead, and the frozen check says so.

Empty gates fail: an empty diff at cadence step; optimize mode without a reference tier or
without a primary benchmark tier with min_speedup; refactor mode without a reference or
command tier. With `--config` (a trial) a passing gate exits 3, not 0.

Self-test: `codehealth gate --self-test [--mutate path:line]` mutates one numeric literal in an
in-scope file of a temporary worktree at HEAD and runs the reference and command tiers there
against HEAD. It passes (exit 0) only if every reference tier detects the mutation, which
shows the tiers exercise the checkout under test and not some installed copy. Before mutating,
every behaviour tier (reference and command tiers not marked `behaviour = false`) must pass on
the unmutated HEAD, else the self-test fails with "tier <name> fails without mutation".
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import resource
import shutil
import signal
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
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
    return any(config.match(path, p) for p in patterns)


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


def _gitdir(repo: Path) -> Path:
    out = gitio.git(repo, "rev-parse", "--git-common-dir").decode().strip()
    return (repo / out).resolve()


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


def base_tree(repo: Path, sha: str, keep: tuple = ()) -> Path:
    """Worktree of `sha` under the git common dir; reused for the same sha, others removed."""
    root = _common_dir(repo)
    tree = root / f"base-{sha}"
    if (tree / ".git").exists():
        gitio.git(tree, "reset", "--hard", "-q", sha)
        gitio.git(tree, "clean", "-fdxq")
        return tree
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob("base-*"):
        if old.name not in {f"base-{k}" for k in keep}:
            _remove_tree(repo, old)
    gitio.git(repo, "worktree", "add", "--detach", "-f", str(tree), sha)
    return tree


def static_checks(repo: Path, base: str, cfg: config.Config, changes, trial: bool = False) -> list[Check]:
    g = cfg.gate
    checks = []
    touched = {p for _, bp, hp in changes for p in (bp, hp) if p}
    frozen, note = list(g.get("frozen", [])), ""
    if g.get("frozen_cmd"):
        r = subprocess.run(g["frozen_cmd"], shell=True, cwd=base_tree(repo, base),
                           capture_output=True, text=True)
        if r.returncode != 0 and trial:
            # Trial against a base without the tooling: use the working tree and say so.
            note = f" [trial: frozen_cmd failed in the base tree ({r.returncode}), ran in the working tree]"
            r = subprocess.run(g["frozen_cmd"], shell=True, cwd=repo, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"frozen_cmd failed ({r.returncode}): {r.stderr.strip()[:300]}")
        frozen += [line.strip() for line in r.stdout.splitlines() if line.strip()]
    hit = sorted(p for p in touched if _match(p, frozen))
    checks.append(Check("frozen", not hit, ", ".join(hit[:10]) + note))
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
    checks.append(numeric_literals(py, tb, th))
    return checks


def _numeric_constants(source: str) -> Counter:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Counter()
    return Counter(repr(n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant)
                   and type(n.value) in (int, float, complex))


def numeric_literals(py, tb: dict, th: dict) -> Check:
    """Informational (always ok): numeric constants removed or added per touched .py file."""
    entries = []   # (path, "0.1 -> removed")
    for bp, hp in py:
        if not (hp or bp).endswith(".py"):
            continue
        cb = _numeric_constants(tb[bp]) if bp in tb else Counter()
        ch = _numeric_constants(th[hp]) if hp in th else Counter()
        entries += [(hp or bp, f"{k} -> removed") for k in sorted((cb - ch).elements())]
        entries += [(hp or bp, f"{k} -> added") for k in sorted((ch - cb).elements())]
    by_path: dict[str, list[str]] = {}
    for path, item in entries[:10]:
        by_path.setdefault(path, []).append(item)
    detail = "; ".join(f"{path}: " + ", ".join(items) for path, items in by_path.items())
    if len(entries) > 10:
        detail += f"; +{len(entries) - 10} more"
    return Check("numeric-literals", True, detail)


def _selected(cfg: config.Config, cadence: str, mode: str, gitdir: Path | None = None) -> list[dict]:
    """Tiers for this cadence and mode; [gate] prefix is prepended to each cmd ({gitdir} filled
    here, {repo} when the command is run)."""
    prefix = str(cfg.gate.get("prefix", "")).strip()
    if prefix and gitdir is not None:
        prefix = prefix.replace("{gitdir}", str(gitdir))
    tiers = [t for t in cfg.gate.get("tier", [])
             if (cadence == "campaign" or t.get("cadence", "step") == "step")
             and mode in t.get("modes", ["refactor", "optimize"])]
    return [dict(t, cmd=f"{prefix} {t['cmd']}") if prefix and t.get("cmd") else t for t in tiers]


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
    env = {k: v.replace("{repo}", str(cwd)) for k, v in env.items()}
    log.write(f"\n$ (cwd {cwd}) {cmd}\n")
    log.flush()
    # Own process group: on timeout (or interrupt) the whole tree dies, not only the shell.
    # Otherwise grandchildren such as pytest-xdist workers on a hung test keep running.
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env={**os.environ, **env},
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.write(f"TIMEOUT after {timeout} s\n")
        rc = 124
    finally:
        _kill_group(proc)  # also reaps stragglers a finished tier left behind
    log.write(f"exit {rc}\n")
    return rc, time.monotonic() - t0


_KILL_GRACE = 5.0  # seconds between SIGTERM and SIGKILL


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the process group of proc, then SIGKILL whatever survives the grace period."""
    def signal_group(sig) -> bool:
        try:
            os.killpg(proc.pid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False  # group is empty
    if not signal_group(signal.SIGTERM):
        return
    deadline = time.monotonic() + _KILL_GRACE
    while time.monotonic() < deadline:
        if proc.poll() is not None and not signal_group(0):
            return
        time.sleep(0.05)
    signal_group(signal.SIGKILL)
    proc.wait()


def _fill(cmd: str, **kw) -> str:
    for k, v in kw.items():
        cmd = cmd.replace("{" + k + "}", v)
    return cmd


def _outputs(out: Path, names) -> dict[str, bytes | None]:
    return {n: (out / n).read_bytes() if (out / n).is_file() else None for n in names}


@dataclass
class Ctx:
    """Everything a reference or benchmark tier needs. `base` is the last accepted step (static
    checks, debt, {files}); `ref_base` is the campaign base the tiers compare head with."""
    repo: Path
    base: str
    ref_base: str
    files: str
    env: dict
    log: object
    mode: str
    cfg: config.Config
    changes: list


def _cache_dir(repo: Path, sha: str, name: str) -> Path:
    return _common_dir(repo) / "cache" / sha / re.sub(r"[^\w.-]", "_", name)


def _base_outputs(t, ctx: Ctx, get_tree) -> tuple[dict | None, str, list]:
    """Outputs of the ref-base tree for tier t: (outputs, error, nondeterministic names).
    Cached under <git-common-dir>/codehealth/cache/<sha>/<tier>/ with a manifest holding the
    command, outputs and env, and the result of the determinism check (two runs, at caching time)."""
    names, timeout = list(t["outputs"]), t.get("timeout")
    cmd = _fill(t["cmd"], repo="<repo>", base=ctx.ref_base, files=ctx.files, out="<out>")
    key = {"cmd": cmd, "outputs": names, "env": ctx.env}
    cdir = _cache_dir(ctx.repo, ctx.ref_base, t["name"])
    try:
        m = json.loads((cdir / "manifest.json").read_text())
        if m.get("key") == key and (m["nondet"] or all((cdir / "out" / n).is_file() for n in names)):
            ctx.log.write(f"\n(reference cache hit: {cdir})\n")
            if m["nondet"]:
                return None, "", m["nondet"]
            return {n: (cdir / "out" / n).read_bytes() for n in names}, "", []
    except (OSError, ValueError, KeyError):
        pass
    shutil.rmtree(cdir, ignore_errors=True)
    tree, runs = get_tree(), []
    for _ in range(2):
        out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
        try:
            rc, _ = _run(_fill(t["cmd"], repo=str(tree), base=ctx.ref_base, files=ctx.files, out=str(out)),
                         tree, ctx.env, ctx.log, timeout)
            if rc != 0:
                return None, f"exit {rc} at base", []
            runs.append(_outputs(out, names))
        finally:
            shutil.rmtree(out, ignore_errors=True)
    missing = [n for n in names if runs[0][n] is None]
    if missing:
        return None, "missing output: " + ", ".join(missing), []
    nondet = [n for n in names if runs[0][n] != runs[1][n]]
    (cdir / "out").mkdir(parents=True)
    if not nondet:
        for n in names:
            (cdir / "out" / n).parent.mkdir(parents=True, exist_ok=True)
            (cdir / "out" / n).write_bytes(runs[0][n])
    (cdir / "manifest.json").write_text(json.dumps({"key": key, "nondet": nondet}))
    return (None if nondet else runs[0]), "", nondet


def _changed_lines(repo: Path, base: str, bp: str | None, hp: str) -> set[int]:
    """Added or changed line numbers of head file hp relative to base (git diff -U0 hunks)."""
    if bp is None:
        return set(range(1, len((repo / hp).read_text().splitlines()) + 2))
    diff = gitio.git(repo, "diff", "-U0", "-M", base, "--", bp, hp).decode(errors="replace")
    lines, here = set(), False
    for row in diff.splitlines():
        if row.startswith("+++ "):
            here = row[4:] == f"b/{hp}"
        elif here and row.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", row)
            if m:
                start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                # A pure deletion (count 0) sits between head lines start and start+1:
                # the function that contained the deleted lines has changed too.
                lines.update(range(start, start + count) if count else (start, start + 1))
    return lines


def unexecuted_changes(ctx: Ctx, executed: dict[str, set[int]]) -> list[str]:
    """path::qualname of changed head functions with no executed line in their body."""
    from .python_backend import units  # noqa: PLC0415
    bad = []
    for _, bp, hp in ctx.changes:
        if not hp or not hp.endswith(".py") or not ctx.cfg.in_scope(hp) or not (ctx.repo / hp).is_file():
            continue
        changed = _changed_lines(ctx.repo, ctx.base, bp, hp)
        if not changed:
            continue
        try:
            tree = ast.parse((ctx.repo / hp).read_text())
        except SyntaxError:
            continue
        hit = executed.get(os.path.realpath(ctx.repo / hp), set())
        for qual, node in units(tree):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if not changed & set(range(first, node.end_lineno + 1)):
                continue
            body = node.body
            if (len(body) > 1 and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
                body = body[1:]
            span = set(range(body[0].lineno, node.end_lineno + 1))
            if body is node.body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                continue  # docstring-only function: nothing to execute
            if not hit & span:
                bad.append(f"{hp}::{qual}")
    return bad


def _run_covered(cmd: str, cwd: Path, env: dict, log, timeout) -> tuple[int, dict[str, set[int]]]:
    """Run cmd with coverage.py measuring every Python (sub)process; executed lines by real path."""
    tmp = Path(tempfile.mkdtemp(prefix="codehealth-cov-"))
    try:
        (tmp / "data").mkdir()
        (tmp / "site").mkdir()
        rc_file = tmp / ".coveragerc"
        rc_file.write_text(f"[run]\ndata_file = {tmp / 'data' / 'cov'}\nparallel = True\n"
                           f"source =\n    {cwd}\n")
        (tmp / "site" / "sitecustomize.py").write_text(
            "try:\n    import coverage\n    coverage.process_startup()\nexcept ImportError:\n    pass\n")
        old = env.get("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
        cenv = {**env, "PYTHONPATH": str(tmp / "site") + (os.pathsep + old if old else ""),
                "COVERAGE_PROCESS_START": str(rc_file)}
        rc, _ = _run(cmd, cwd, cenv, log, timeout)
        executed: dict[str, set[int]] = {}
        try:
            import coverage  # noqa: PLC0415
        except ImportError:
            return rc, executed
        cov = coverage.Coverage(data_file=str(tmp / "data" / "cov"), config_file=str(rc_file))
        cov.combine(keep=False)
        data = cov.get_data()
        for f in data.measured_files():
            executed[os.path.realpath(f)] = set(data.lines(f) or [])
        return rc, executed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _reference(t, ctx: Ctx, get_tree) -> tuple[bool, str]:
    names, timeout = t["outputs"], t.get("timeout")
    b, err, nondet = _base_outputs(t, ctx, get_tree)
    if err:
        return False, err
    if nondet:
        return False, "not deterministic at base: " + ", ".join(nondet)
    runs, executed = [], None
    for i in range(2):
        out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
        cmd = _fill(t["cmd"], repo=str(ctx.repo), base=ctx.ref_base, files=ctx.files, out=str(out))
        try:
            if i == 0 and t.get("coverage", False):
                rc, executed = _run_covered(cmd, ctx.repo, ctx.env, ctx.log, timeout)
            else:
                rc, _ = _run(cmd, ctx.repo, ctx.env, ctx.log, timeout)
            if rc != 0:
                return False, f"exit {rc} at head"
            runs.append(_outputs(out, names))
        finally:
            shutil.rmtree(out, ignore_errors=True)
    h, h2 = runs
    missing = [n for n in names if h[n] is None]
    if missing:
        return False, "missing output: " + ", ".join(missing)
    nondet = [n for n in names if h[n] != h2[n]]
    if nondet:
        return False, "not deterministic at head: " + ", ".join(nondet)
    if t.get("coverage", False):
        if not executed:
            return False, "no coverage data (is coverage installed in the command's interpreter?)"
        bad = unexecuted_changes(ctx, executed)
        if bad:
            return False, "reference does not execute: " + ", ".join(bad)
    # Refactor mode is always bitwise; optimize mode uses the human-declared tolerance.
    tol = t.get("tolerance") if ctx.mode == "optimize" else None
    ok, details = True, []
    for n in names:
        good, detail = compare(b[n], h[n], n, tol)
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


def speedup_lower_bound(ratios, confidence: float = 0.95) -> tuple[float, bool]:
    """Distribution-free one-sided lower confidence bound for the median of paired ratios.

    Sort the ratios; take the largest k >= 1 with P(Binomial(n, 0.5) <= k-1) <= 1 - confidence
    and return the k-th smallest ratio. Returns (bound, insufficient); if no k qualifies (n too
    small: n < 5 at 95 %), the bound is the minimum ratio and insufficient is True."""
    xs = sorted(ratios)
    n = len(xs)
    if not n:
        raise ValueError("no ratios")
    alpha, cum, k = 1.0 - confidence, 0.0, 0
    for j in range(n):          # cum = P(X <= j)
        cum += math.comb(n, j) / 2 ** n
        if cum <= alpha + 1e-12:
            k = j + 1
        else:
            break
    if k == 0:
        return xs[0], True
    return xs[k - 1], False


def _bench_detail(ratios, times, cpu, extra="") -> str:
    return (f"speedup {statistics.median(ratios):.3f} (paired min {min(ratios):.3f}, max {max(ratios):.3f}, "
            f"n={len(ratios)}{extra}; "
            f"base median {statistics.median(times['base']):.3g} s, head median "
            f"{statistics.median(times['head']):.3g} s; load1 {os.getloadavg()[0]:.2f}; "
            f"children cpu base {cpu['base']:.3g} s, head {cpu['head']:.3g} s)")


def _benchmark(t, ctx: Ctx, get_tree) -> tuple[bool, str]:
    """Warm-up, then ABBA-ordered paired runs (base is the ref-base tree); speedup is the median
    of base_i / head_i. Primary tier in optimize mode: accepted when the lower confidence bound
    of the median reaches min_speedup; if only the median does, ABBA rounds (2 runs per side)
    are added until the bound passes or n reaches max_repeat (default 4 * repeat)."""
    repeat = int(t.get("repeat", 5))
    sides = {"base": get_tree(), "head": ctx.repo}
    args = dict(base=ctx.ref_base, files=ctx.files, env=ctx.env, log=ctx.log)
    for side, cwd in sides.items():
        rc, _, _, _ = _bench_run(t, cwd, args["base"], args["files"], args["env"], args["log"])
        if rc != 0:
            return False, f"exit {rc} at {side} (warm-up)"
    times, cpu = {"base": [], "head": []}, {"base": 0.0, "head": 0.0}

    def pairs(count: int) -> str:
        for i in range(count):
            for side in (("base", "head") if i % 2 == 0 else ("head", "base")):
                rc, sec, c, note = _bench_run(t, sides[side], args["base"], args["files"],
                                              args["env"], args["log"])
                if rc != 0:
                    return f"exit {rc} at {side}"
                if note:
                    return f"{note} ({side})"
                times[side].append(sec)
                cpu[side] += c
        return ""

    def ratios():
        return [b / h if h else float("inf") for b, h in zip(times["base"], times["head"])]

    err = pairs(repeat)
    if err:
        return False, err
    if ctx.mode == "optimize" and t.get("primary", False):
        need = float(t.get("min_speedup", 1.0))
        max_repeat = int(t.get("max_repeat", 4 * repeat))
        extended = False
        while True:
            r = ratios()
            median = statistics.median(r)
            bound, insufficient = speedup_lower_bound(r)
            if bound >= need or median < need or len(r) + 2 > max_repeat:
                break
            err = pairs(2)
            if err:
                return False, err
            extended = True
        extra = (f", lower bound {bound:.3f} at 95%" + (" (n too small)" if insufficient else "")
                 + (f", extended from {repeat} to {len(r)} rounds" if extended else ""))
        return bound >= need, _bench_detail(r, times, cpu, extra) + f", need >= {need}"
    r = ratios()
    allowed = float(t.get("max_slowdown", 0.03))
    return (statistics.median(r) >= 1 / (1 + allowed),
            _bench_detail(r, times, cpu) + f", max slowdown {allowed:.0%}")


def run_tiers(repo: Path, base: str, cfg: config.Config, changes, cadence: str, log,
              mode: str = "refactor", keep_going: bool | None = None,
              kinds: tuple | None = None, ref_base: str | None = None,
              only: set | None = None) -> list[Check]:
    g = cfg.gate
    gitdir = _gitdir(repo)
    env = {k: str(v).replace("{gitdir}", str(gitdir)) for k, v in g.get("env", {}).items()}
    files = " ".join(shlex.quote(hp) for _, _, hp in changes if hp)
    keep_going = g.get("keep_going", False) if keep_going is None else keep_going
    ctx = Ctx(repo, base, ref_base or base, files, env, log, mode, cfg, changes)
    get_tree = lambda: base_tree(repo, ctx.ref_base, keep=(base,))  # noqa: E731
    checks = []
    for t in _selected(cfg, cadence, mode, gitdir):
        name, kind, t0 = t["name"], t.get("kind", "command"), time.monotonic()
        if (kinds and kind not in kinds) or (only is not None and name not in only):
            continue
        if kind == "command":
            rc, _ = _run(_fill(t["cmd"], repo=str(repo), base=base, files=files, out=""),
                         repo, env, log, t.get("timeout"))
            ok, detail = rc == 0, (f"exit {rc}" if rc else "")
        elif kind in ("reference", "benchmark"):
            fn = _reference if kind == "reference" else _benchmark
            ok, detail = fn(t, ctx, get_tree)
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
         config_override: Path | None = None, mode: str = "refactor",
         ref_base: str | None = None) -> tuple[list[Check], str, str]:
    """config_override is for local trials only; CI must use the base's committed config.

    mode "refactor": references bitwise, benchmark may not slow down beyond max_slowdown.
    mode "optimize": references within each tier's declared `tolerance`, the primary benchmark
    must reach min_speedup. Debt may not rise in either mode. Returns (checks, base, ref_base).
    Static checks and debt use `base` (last accepted step); reference and benchmark tiers compare
    head with `ref_base` (campaign base; default: the same as base).
    """
    if mode not in ("refactor", "optimize"):
        raise ValueError(f"unknown mode {mode!r}")
    base = gitio.merge_base(repo, base_ref, "HEAD")
    ref_sha = gitio.merge_base(repo, ref_base, "HEAD") if ref_base else base
    cfg = (config.parse(config_override.read_text(), f"override:{config_override}")
           if config_override else config.load(repo, base))
    changes = gitio.changes(repo, base, None)
    checks = (config_checks(cfg, changes, cadence, mode) + static_checks(repo, base, cfg, changes,
                                                                  trial=config_override is not None)
              + [debt_check(repo, base, cfg, changes)])
    if all(c.ok for c in checks):
        with open(log_path, "w") as log:
            checks += run_tiers(repo, base, cfg, changes, cadence, log, mode, ref_base=ref_sha)
    return checks, base, ref_sha


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


def self_test_kind(cfg: config.Config) -> str:
    """What the self-test counts: reference tiers if there are any, else behaviour command tiers."""
    tiers = _selected(cfg, "campaign", "refactor")
    return "reference" if any(t.get("kind") == "reference" for t in tiers) else "behaviour command"


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
        # Baseline: every behaviour tier must pass on the unmutated HEAD, else "detected" is vacuous.
        beh = {t["name"] for t in _selected(cfg, "campaign", "refactor")
               if t.get("kind", "command") in ("reference", "command") and t.get("behaviour", True)}
        with open(log_path, "w") as log:
            base_checks = run_tiers(tree, head, cfg, [], "campaign", log, "refactor",
                                    keep_going=True, kinds=("reference", "command"), only=beh)
        broken = [c for c in base_checks if not c.ok]
        if broken:
            return False, [Check(c.name, False, f"tier {c.name} fails without mutation"
                                 + (f" ({c.detail})" if c.detail else ""), c.seconds)
                           for c in broken], "none (baseline failed)"
        gitio.git(tree, "clean", "-fdxq")   # drop stale bytecode: a same-size mutation keeps mtime and size
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
        with open(log_path, "a") as log:
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
