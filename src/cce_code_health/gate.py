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

Placeholders in cmd: {repo} {base} {ref_base} {out} {files} (touched head paths, shell-quoted).
{ref_base} is the campaign base sha (`--ref-base`; equal to {base} when not given). In the
self-test `--ref-base` (merge-base with HEAD) is honoured for the {ref_base} placeholder only, so an
impacted-test selector sees the campaign base; reference comparison stays against the unmutated HEAD.
Without it {base} and {ref_base} are both HEAD there.
Every touched path (base or head side) must be in scope. Patterns are fnmatch with '*' matching
'/', and "/**/" also matching a single "/" (one matcher for scope, frozen and forbid).

Two bases (`--base` and `--ref-base`): static checks, debt and numeric-literals use `--base`
(the last accepted step); reference and benchmark tiers compare head with `--ref-base` (the
campaign base; default: the same as `--base`), so tolerances and slowdowns cannot compound over
steps. Both are reduced to their merge-base with HEAD; the GATE line prints `ref-base=<sha10>`.

Gate JSON `meta.head` is the HEAD commit sha when the worktree is clean relative to HEAD (a
committed step), else "worktree"; the GATE line prints `head=<sha10>` or `head=worktree`.

`--revert SHA` (repeatable) gates a commit that exactly undoes earlier accepted steps (review
rejected them). Static check `revert`: every SHA is a single-parent commit and an ancestor of the
resolved base, and the worktree tree must equal base with those commits reverted (newest first,
computed with `git merge-tree --write-tree`, needs git >= 2.38, worktree and index untouched); a
conflict or a difference fails the check (up to 10 differing paths listed). In revert mode the debt
check passes even when debt rises ("rise allowed: exact revert"); `--require-gain` is a usage
error; all other checks and tiers run as usual. Meta `revert` lists the full shas ([] otherwise).

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

Static check `architecture` (every mode; only when the base config has an `[architecture]`
table, see architecture.py): layer and forbid rules and import cycles over the whole scope at
base and at head; fails on a violation (rule, importer, imported) or a cycle edge (a, b) present
at head but not at base; removed ones are listed as improvements. `codehealth diff` applies the
same ratchet and `codehealth arch [--ref REF]` reports the current state.

Restructure mode (`--mode restructure --plan .codehealth/restructure/<id>.toml`, see
restructure.py): the plan is read from the base commit (absent or invalid: usage error, exit 2)
with keys id, approved_by, reason, new_files (globs, each in scope) and new_public
("path.py:Name"). As refactor mode (tiers, bitwise references, no slowdown, debt, literals as
one multiset over all touched files, --require-gain), plus static check `plan` (every added file,
or new path of a rename, matches new_files) and, with public_api = true, a different public-API
rule: no public name may disappear from any module (no removed or moved modules); a definition
may become a re-export (`from <new> import Name`) only from an added planned file whose own API
entry is identical; public names new at head must be listed in new_public, and every listed name
must exist at head. Meta `plan` = {path, blob (sha at base), id}; `codehealth log` writes class
`restructure` and, as for refactor, needs a debt drop for accepted.

Interface mode (`--mode interface --plan .codehealth/interface/<id>.toml`, see interface.py):
deliberate public-API changes, exactly as the plan lists (removed, renamed, added); callers
outside the scope or under forbid may follow when they match the plan's `callers`; accepted
needs no debt drop.

Build-tool tiers (C++ suites; see the README section "Build-tool tiers (C++)"):
- `skip_exit = 77` (int or list, any tier kind): a run of the tier (base or head) exiting with one
  of these codes fails the tier with "not run (exit 77)"; in the self-test that is outcome
  `error`, never a detection.
- `fail_regex` (any tier kind): a Python regex searched (re.MULTILINE) in the output of every run
  of the tier, base and head (not the logged command line). A match fails the tier with "output
  matched fail_regex: <first matching line, 120 chars>"; in the self-test it is outcome `error`.
  Use it for ctest, which exits 0 when tests are skipped. A failing base run is not cached.
- `same_lines` (reference tiers): a Python regex; the output lines of a run that match it, with
  the tree path replaced by `{repo}`, must be the same at base and head (sorted). For configure
  lines that name resolved dependencies or toolchains (`-- Found X: <path>`), so a base worktree
  that silently fetched or found a different dependency fails ("same_lines differ"). A regex that
  matches no line on either side fails too (a guard that checks nothing). Base lines are cached
  with the base outputs; keep the configure output in the log (no `>/dev/null`).
- kind `findings` (keys cmd, format = "gcc", ignore_exit = true, timeout, cadence, modes,
  skip_exit, fail_regex): runs cmd in the tree of `--base` (not ref_base) and at head, parses
  `file:line:col: warning|error: ... [check]` lines (gcc, clang, clang-tidy), maps file paths to
  tree-relative ones (outside the tree: `external:<path>`) and counts unique (file, line, col,
  check) per (file, check); base paths of renamed files are mapped to head. Fails when any count
  rises ("findings rose: path [check] 3 -> 5", up to 10), else "base N, head M". With
  ignore_exit = false a nonzero exit fails. Base findings are cached in
  `<git-common-dir>/codehealth/cache/<base sha>/<tier>/findings.json` keyed by cmd and env.
  Not a behaviour tier: the self-test skips it.
- kind `convergence` (keys cmd, outputs = [one file], order, band = [lo, hi], last): head only;
  the output holds `h error` lines (# comments) for successive refinements; observed orders
  p_i = log(e_i / e_{i+1}) / log(h_i / h_{i+1}); the last `last` of them (default all) must lie
  in band. Fewer than 2 levels, non-positive or non-finite values fail. Detail
  "orders 1.97 2.01 (band 1.8..2.2)". Skipped by the self-test.

Static check `flags` (decision 14: compiler and threading flags are human decisions): on when the
config has a C++ profile or `[gate] flags = true` (`flags = false` switches it off). Scans the
added and removed lines (base..worktree) of touched CMakeLists.txt, *.cmake, CMake(User)Presets.json,
meson.build, Makefile, *.mk, setup.py, pyproject.toml and C/C++/CUDA sources and headers for
FP-semantics, ISA and thread-count flags (-ffast-math, -Ofast, -ffp-contract, -march=, -mavx,
`#pragma omp ... num_threads`, `#pragma GCC optimize`, ...; see FLAG_PATTERNS), in build files also
any line with CMAKE_{C,CXX,CUDA}_FLAGS, add_compile_options, target_compile_options or
CMAKE_BUILD_TYPE, plus `[gate] flag_patterns` (regexes). Comments are not exempt. Detail: up to 10
`path:line: +line` / `-line` entries.

`codehealth config --check [--config FILE]` validates the config (unknown keys, tier kinds,
required keys, tolerance keys, thread pins, one primary benchmark, cadence and modes, and the
`[architecture]` table).
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
`--mutate path:line:N` picks the N-th (1-based, column order) numeric literal on that line.
Each self-test check records `outcome` (detected | missed | error), `exit_code` (mutated run),
`baseline_exit_code` (unmutated run) and the mutated run's `detail`. A command tier detects the
mutation by exiting nonzero; with `detect_exit = [1, 5]` (nonzero ints) only those codes count and
any other nonzero code is an `error` (not detected). A reference tier detects by output difference,
but a head exit code outside a configured `detect_exit` is an `error`. In normal gate steps
`detect_exit` is ignored (any nonzero exit fails).

`codehealth gate --require-gain` adds a static check `gain` that passes only if the debt over the
touched in-scope files dropped (a failure skips the tiers). The gate JSON `meta` records
`debt_before`, `debt_after`, `require_gain`, `trial` and `head_tree` (git tree sha of the worktree
state that was judged, computed with a temporary index). Check JSON carries `exit_code` (command
tiers: rc; reference tiers: rc of the head run).
"""
from __future__ import annotations

import ast
import difflib
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
    exit_code: int | None = None            # command tiers: rc; reference tiers: rc of the head run
    outcome: str | None = None              # self-test only: detected | missed | error
    baseline_exit_code: int | None = None   # self-test only: rc of the same tier on unmutated HEAD


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


def docs_only_paths(repo: Path, base: str, cfg: config.Config) -> set[str]:
    """Paths that only docs/config commits touched since base: every commit in base..HEAD that
    touches the path touches no in-scope file, and the working tree does not change it. At
    campaign cadence such paths (the playbook commit, the run-log copy, a docs-debt fix, a
    restructure plan) pass `scope` and `forbid`: each step was gated alone."""
    out = gitio.git(repo, "log", "--format=%x00%H", "--name-only", "--no-renames", f"{base}..HEAD").decode()
    step_paths, seen = set(), set()
    for block in out.split("\x00")[1:]:
        files = [ln for ln in block.splitlines()[1:] if ln.strip()]
        seen.update(files)
        if any(cfg.in_scope(f) for f in files):
            step_paths.update(files)
    dirty = {ln[3:] for ln in gitio.git(repo, "status", "--porcelain", "--no-renames",
                                        "--untracked-files=all").decode().splitlines() if len(ln) > 3}
    return seen - step_paths - dirty


def static_checks(repo: Path, base: str, cfg: config.Config, changes, trial: bool = False,
                  plan: dict | None = None, cadence: str = "step") -> list[Check]:
    """Static rules; with `plan` (restructure or interface mode, `plan["kind"]`) the `plan`
    check is added and `public_api` follows that mode's rule."""
    from . import architecture, interface, restructure  # noqa: PLC0415
    iface = plan is not None and plan.get("kind") == "interface"
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
    callers = interface.exempt(plan) if iface else []          # interface plan: callers may follow
    docs_only = docs_only_paths(repo, base, cfg) - set(hit) if cadence == "campaign" else set()
    hit = sorted(p for p in touched if ((_match(p, list(g.get("forbid", []))) and not _match(p, callers))
                                        or _match(p, [".codehealth/*"])) and p not in docs_only)
    excused = sorted(p for p in touched if p in docs_only
                     and (_match(p, list(g.get("forbid", [])) + [".codehealth/*"]) or not cfg.in_scope(p)))
    note = f" [campaign: {len(excused)} path(s) touched only by docs/config commits]" if excused else ""
    checks.append(Check("forbid", not hit, ", ".join(hit[:10]) + note))
    hit = sorted(p for p in touched if not cfg.in_scope(p) and not _match(p, callers) and p not in docs_only)
    checks.append(Check("scope", not hit, ("out of scope: " + ", ".join(hit[:10]) if hit else "") + note))
    if iface:
        hit = interface.plan_problems(plan, changes, cfg, list(g.get("forbid", [])))
        checks.append(Check("plan", not hit, "; ".join(hit[:5]) or f"plan {plan['id']}"))
        cp = interface.caller_paths(plan, changes, cfg, list(g.get("forbid", [])))
        cb = gitio.read_files(repo, base, sorted({bp for _, bp, _ in cp if bp}))
        ch = gitio.read_files(repo, None, sorted({hp for _, _, hp in cp if hp}))
        hit, rec = interface.caller_record(plan, cp, cb, ch, (cfg.architecture or {}).get("roots", ["src", "."]))
        summary = (f"{len(cp)} caller file(s) follow the plan; {len(rec['deleted'])} statement(s) deleted"
                   f"{' (' + '; '.join(rec['deleted'][:3]) + ')' if rec['deleted'] else ''}; "
                   f"{rec['changed_calls']} changed call(s)")
        c = Check("callers", not hit, "; ".join(hit[:5]) or summary)
        c.callers = rec                            # every deleted statement, for review
        checks.append(c)
    elif plan is not None:
        hit = restructure.plan_problems(plan, changes)
        checks.append(Check("plan", not hit, "; ".join(hit[:5]) or f"plan {plan['id']}"))

    roots = (cfg.architecture or {}).get("roots", ["."])
    # Python-only rules: C++ static rules (public headers, literals) are not implemented yet;
    # their checks pass and say how many touched C++ files went unchecked. A pair with a .py
    # side (a.py -> a.cpp included) is checked, so a Python API removed by the rename counts.
    def is_py(p):
        return bool(p) and p.endswith(".py")
    py = [(bp, hp) for _, bp, hp in changes
          if ((bp and cfg.in_scope(bp)) or (hp and cfg.in_scope(hp))) and (is_py(bp) or is_py(hp))]
    tb = gitio.read_files(repo, base, [bp for bp, _ in py if is_py(bp)])
    th = gitio.read_files(repo, None, [hp for _, hp in py if is_py(hp)])
    n_cpp = len({p for _, bp, hp in changes for p in (bp, hp)
                 if p and cfg.in_scope(p) and config.EXTENSIONS.get(Path(p).suffix) == "cpp"})
    unchecked = f"not checked: {n_cpp} C++ file(s)" if n_cpp else ""

    def with_cpp(detail):
        return "; ".join(x for x in (detail, unchecked) if x)
    if g.get("literals", False):
        lb, lh = Counter(), Counter()
        for t in tb.values():
            lb += _string_literals(t)
        for t in th.values():
            lh += _string_literals(t)
        if iface:                                  # __all__ of the plan's files: left to public_api
            lb, lh = lb - interface.all_strings(plan, tb), lh - interface.all_strings(plan, th)
        gone, new = lb - lh, lh - lb
        detail = "; ".join([f"removed {k}" for k in list(gone)[:3]] + [f"added {k}" for k in list(new)[:3]])
        checks.append(Check("literals", not gone and not new, with_cpp(detail[:400])))
    if iface:                                      # gate() refuses interface mode without public_api
        hp_ = sorted({interface._split(n)[0] for n in plan.get("retargeted", {}).values()})
        hb_, hh_ = gitio.read_files(repo, base, hp_), gitio.read_files(repo, None, hp_)
        homes = {p: (hb_.get(p), hh_.get(p)) for p in hp_}
        problems, record = interface.public_api_problems(plan, py, tb, th, _public_api, homes)
        c = Check("public_api", not problems, with_cpp("; ".join(problems[:5])))
        c.interface = record                       # old and new API text per planned change
        checks.append(c)
    elif g.get("public_api", False) and plan is not None:
        problems = restructure.public_api_problems(plan, py, tb, th, _public_api, roots)
        checks.append(Check("public_api", not problems, with_cpp("; ".join(problems[:5]))))
    elif g.get("public_api", False):
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
        checks.append(Check("public_api", not problems, with_cpp("; ".join(problems[:5]))))
    moved_to = set()
    if plan is not None:
        mods = [architecture.module_name(p, roots) for p in restructure.added_paths(changes)
                if _match(p, plan["new_files"])]
        moved_to = {m[0] for m in mods if m}
    arch = architecture.compare_refs(repo, base, None, cfg, moved_to)
    if arch is not None:
        fail, gain, warn = arch
        items = fail + gain
        checks.append(Check("architecture", not fail, "; ".join(items[:5])
                            + (f"; +{len(items) - 5} more" if len(items) > 5 else "")
                            + "".join(f"; warning: {w}" for w in warn)))
    flags = flags_check(repo, base, cfg, changes)
    if flags is not None:
        checks.append(flags)
    checks += docs_checks(repo, base, cfg, changes)
    checks.append(numeric_literals(py, tb, th))
    return checks


def docs_checks(repo: Path, base: str, cfg: config.Config, changes) -> list[Check]:
    """Static checks `docs` and `docstrings` (only with a `[docs]` table in the base config).
    A step cannot edit docs (scope), so references that rot only warn here (docs debt, fixed in a
    docs commit; CI `codehealth diff` over the whole branch fails on them); a new unresolved
    reference fails only with `[docs] new = "fail"`."""
    from . import docs, docstrings  # noqa: PLC0415
    out = []
    res = docs.compare(repo, base, None, cfg, rot="warn")
    if res is not None:
        items = res.failures + [f"warning: {w}" for w in res.warnings]
        out.append(Check("docs", not res.failures, "; ".join(items[:5])
                         + (f"; +{len(items) - 5} more" if len(items) > 5 else "")))
    ds = docstrings.compare_refs(repo, base, None, cfg, changes)
    if ds is not None:
        out.append(Check("docstrings", not ds, "; ".join(ds[:5]) + (f"; +{len(ds) - 5} more" if len(ds) > 5 else "")))
    return out


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


# -- static check flags (decision 14: compiler and threading flags are human decisions) ------

FLAG_PATTERNS = [re.escape(f) for f in (
    "-ffast-math", "-Ofast", "-funsafe-math-optimizations", "-fassociative-math", "-freciprocal-math",
    "-ffinite-math-only", "-fno-signed-zeros", "-ffp-contract", "-march=", "-mtune=", "-mfma", "-mavx",
    "omp_set_num_threads")] + [
    r"#\s*pragma\s+omp\b.*\bnum_threads", r"#\s*pragma\s+GCC\s+optimize", r"__attribute__\s*\(\(\s*optimize",
    r"#\s*pragma\s+STDC\s+FP_CONTRACT", r"#\s*pragma\s+clang\s+fp\b"]
BUILD_FLAG_PATTERNS = [r"CMAKE_CXX_FLAGS", r"CMAKE_C_FLAGS", r"CMAKE_CUDA_FLAGS", r"add_compile_options",
                       r"target_compile_options", r"CMAKE_BUILD_TYPE"]
BUILD_NAMES = {"CMakeLists.txt", "CMakePresets.json", "CMakeUserPresets.json", "meson.build", "Makefile",
               "setup.py", "pyproject.toml"}
BUILD_SUFFIXES = (".cmake", ".mk")
SOURCE_SUFFIXES = {s for s, lang in config.EXTENSIONS.items() if lang == "cpp"} | {".c"}


def flags_active(cfg: config.Config) -> bool:
    """On with a C++ profile or `[gate] flags = true`; `flags = false` switches it off."""
    return bool(cfg.gate.get("flags", "cpp" in cfg.other_profiles))


def _diff_lines(old: str, new: str):
    """(sign, line number, text) of removed (base numbering) and added (head numbering) lines."""
    a, b = old.splitlines(), new.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            yield from (("-", i + 1, a[i]) for i in range(i1, i2))
        if tag in ("replace", "insert"):
            yield from (("+", j + 1, b[j]) for j in range(j1, j2))


def flags_check(repo: Path, base: str, cfg: config.Config, changes) -> Check | None:
    """Fail on added or removed lines that set compiler, FP or threading flags (None if inactive)."""
    if not flags_active(cfg):
        return None
    common = [re.compile(p) for p in FLAG_PATTERNS + list(cfg.gate.get("flag_patterns", []))]
    build_only = [re.compile(p) for p in BUILD_FLAG_PATTERNS]
    hits = []
    for _, bp, hp in changes:
        path = hp or bp
        names = {Path(p).name for p in (bp, hp) if p}
        build = bool(names & BUILD_NAMES) or any(n.endswith(BUILD_SUFFIXES) for n in names)
        if not build and not any(Path(n).suffix in SOURCE_SUFFIXES for n in names):
            continue
        old = gitio.read_files(repo, base, [bp]).get(bp, "") if bp else ""
        new = (repo / hp).read_text(encoding="utf-8", errors="replace") if hp and (repo / hp).is_file() else ""
        pats = common + (build_only if build else [])
        hits += [f"{path}:{no}: {sign}{line.strip()}" for sign, no, line in _diff_lines(old, new)
                 if any(rx.search(line) for rx in pats)]
    more = f"; +{len(hits) - 10} more" if len(hits) > 10 else ""
    return Check("flags", not hits, "; ".join(h[:160] for h in hits[:10]) + more)


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
    ref_arg: str = ""   # value filled into {ref_base}; differs from ref_base only in the self-test


def _cache_dir(repo: Path, sha: str, name: str) -> Path:
    return _common_dir(repo) / "cache" / sha / re.sub(r"[^\w.-]", "_", name)


def same_lines(t: dict, text: str, tree: Path) -> list[str]:
    """Output lines matching the tier's `same_lines` regex, the tree path replaced by {repo},
    sorted: what must be identical at base and head (dependency paths, toolchain versions)."""
    rx = t.get("same_lines")
    if not rx:
        return []
    roots = sorted({str(tree), os.path.realpath(tree)}, key=len, reverse=True)
    out = []
    for ln in text.splitlines():
        if re.search(rx, ln):
            for r in roots:
                ln = ln.replace(r, "{repo}")
            out.append(ln.rstrip())
    return sorted(out)


def _lines_differ(base: list[str], head: list[str]) -> str:
    if not base and not head:
        return ("same_lines matched no output line at base or head: the guard checks nothing "
                "(CMake prints message(STATUS) lines as '-- <text>'; is the configure output "
                "silenced?)")
    gone = [ln for ln in base if ln not in head]
    new = [ln for ln in head if ln not in base]
    if not gone and not new:
        return ""
    return ("same_lines differ between base and head (a dependency or toolchain resolved "
            f"differently): base {gone[:2]} head {new[:2]}")


def _base_outputs(t, ctx: Ctx, get_tree) -> tuple[dict | None, str, list, list]:
    """Outputs of the ref-base tree for tier t: (outputs, error, nondeterministic names,
    same_lines of the first base run). Cached under <git-common-dir>/codehealth/cache/<sha>/<tier>/
    with a manifest holding the command, outputs, env and same_lines regex, the result of the
    determinism check (two runs, at caching time) and the base same_lines."""
    names, timeout = list(t["outputs"]), t.get("timeout")
    cmd = _fill(t["cmd"], repo="<repo>", base=ctx.ref_base, ref_base=ctx.ref_arg or ctx.ref_base, files=ctx.files, out="<out>")
    key = {"cmd": cmd, "outputs": names, "env": ctx.env, "same_lines": t.get("same_lines")}
    cdir = _cache_dir(ctx.repo, ctx.ref_base, t["name"])
    try:
        m = json.loads((cdir / "manifest.json").read_text())
        if m.get("key") == key and (m["nondet"] or all((cdir / "out" / n).is_file() for n in names)):
            ctx.log.write(f"\n(reference cache hit: {cdir})\n")
            if m["nondet"]:
                return None, "", m["nondet"], m.get("lines", [])
            return {n: (cdir / "out" / n).read_bytes() for n in names}, "", [], m.get("lines", [])
    except (OSError, ValueError, KeyError):
        pass
    shutil.rmtree(cdir, ignore_errors=True)
    tree, runs, lines = get_tree(), [], []
    for i in range(2):
        out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
        try:
            rc, _, text = _run_checked(t, _fill(t["cmd"], repo=str(tree), base=ctx.ref_base,
                                                ref_base=ctx.ref_arg or ctx.ref_base, files=ctx.files, out=str(out)),
                                       tree, ctx.env, ctx.log, timeout)
            if rc != 0:
                return None, f"exit {rc} at base", [], []
            if i == 0:
                lines = same_lines(t, text, tree)
            runs.append(_outputs(out, names))
        finally:
            shutil.rmtree(out, ignore_errors=True)
    missing = [n for n in names if runs[0][n] is None]
    if missing:
        return None, "missing output: " + ", ".join(missing), [], lines
    nondet = [n for n in names if runs[0][n] != runs[1][n]]
    (cdir / "out").mkdir(parents=True)
    if not nondet:
        for n in names:
            (cdir / "out" / n).parent.mkdir(parents=True, exist_ok=True)
            (cdir / "out" / n).write_bytes(runs[0][n])
    (cdir / "manifest.json").write_text(json.dumps({"key": key, "nondet": nondet, "lines": lines}))
    return (None if nondet else runs[0]), "", nondet, lines


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


def _run_covered(cmd: str, cwd: Path, env: dict, log, timeout,
                 t: dict | None = None) -> tuple[int, dict[str, set[int]]]:
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
        rc, _, _ = _run_checked(t or {}, cmd, cwd, cenv, log, timeout)
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


def _reference(t, ctx: Ctx, get_tree) -> tuple[bool, str, int | None]:
    """(ok, detail, exit code of the head run or None if it did not get that far)."""
    names, timeout = t["outputs"], t.get("timeout")
    b, err, nondet, base_lines = _base_outputs(t, ctx, get_tree)
    if err:
        return False, err, None
    if nondet:
        return False, "not deterministic at base: " + ", ".join(nondet), None
    runs, executed = [], None
    for i in range(2):
        out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
        cmd = _fill(t["cmd"], repo=str(ctx.repo), base=ctx.ref_base, ref_base=ctx.ref_arg or ctx.ref_base,
                    files=ctx.files, out=str(out))
        try:
            text = ""
            if i == 0 and t.get("coverage", False):
                rc, executed = _run_covered(cmd, ctx.repo, ctx.env, ctx.log, timeout, t)
            else:
                rc, _, text = _run_checked(t, cmd, ctx.repo, ctx.env, ctx.log, timeout)
            if rc != 0:
                return False, f"exit {rc} at head", rc
            if t.get("same_lines") and (i == 1 or not t.get("coverage", False)):
                differ = _lines_differ(base_lines, same_lines(t, text, ctx.repo))
                if differ:
                    return False, differ, 0
            runs.append(_outputs(out, names))
        finally:
            shutil.rmtree(out, ignore_errors=True)
    h, h2 = runs
    missing = [n for n in names if h[n] is None]
    if missing:
        return False, "missing output: " + ", ".join(missing), 0
    nondet = [n for n in names if h[n] != h2[n]]
    if nondet:
        return False, "not deterministic at head: " + ", ".join(nondet), 0
    if t.get("coverage", False):
        if not executed:
            return False, "no coverage data (is coverage installed in the command's interpreter?)", 0
        bad = unexecuted_changes(ctx, executed)
        if bad:
            return False, "reference does not execute: " + ", ".join(bad), 0
    # Refactor mode is always bitwise; optimize mode uses the human-declared tolerance.
    tol = t.get("tolerance") if ctx.mode == "optimize" else None
    ok, details = True, []
    for n in names:
        good, detail = compare(b[n], h[n], n, tol)
        ok &= good
        if detail != "bitwise equal":
            details.append(f"{n}: {detail}")
    return ok, "; ".join(details) or "bitwise equal", 0


def _status(cwd: Path) -> bytes:
    return gitio.git(cwd, "status", "--porcelain", "-uall")


def _bench_run(t, cwd, base, files, env, log) -> tuple[int, float, float, str]:
    """One run with fresh TMPDIR and XDG_CACHE_HOME: (rc, seconds, children CPU s, note)."""
    tmp, cache = tempfile.mkdtemp(prefix="codehealth-tmp-"), tempfile.mkdtemp(prefix="codehealth-cache-")
    try:
        before = _status(cwd)
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        rc, sec, _ = _run_checked(t, _fill(t["cmd"], repo=str(cwd), base=base, ref_base=base, files=files,
                                           out=""), cwd,
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


# -- build-tool tiers (C++): skip_exit, fail_regex, findings, convergence ---------------------

NOT_RUN = "not run"
FAIL_REGEX = "output matched fail_regex"


class TierProblem(Exception):
    """A run that fails its tier whatever the kind: skip_exit, fail_regex, or a findings exit."""

    def __init__(self, detail: str, rc: int | None):
        super().__init__(detail)
        self.detail, self.rc = detail, rc


def skip_codes(t: dict) -> list[int]:
    v = t.get("skip_exit")
    return [] if v is None else [v] if isinstance(v, int) else list(v)


def _run_checked(t: dict, cmd: str, cwd: Path, env: dict, log, timeout) -> tuple[int, float, str]:
    """`_run` plus the tier's skip_exit and fail_regex: (rc, seconds, this run's output).
    The output is the segment `_run` appended to the log file, without its header and exit line."""
    log.flush()
    start = os.fstat(log.fileno()).st_size
    rc, sec = _run(cmd, cwd, env, log, timeout)
    log.flush()
    with open(log.name, "rb") as f:
        f.seek(start)
        text = f.read().decode("utf-8", errors="replace")
    header = f"\n$ (cwd {cwd}) {cmd}\n"   # as written by _run
    text = text[len(header):] if text.startswith(header) else text
    text = text[:-len(f"exit {rc}\n")] if text.endswith(f"exit {rc}\n") else text
    if rc in skip_codes(t):
        raise TierProblem(f"{NOT_RUN} (exit {rc})", rc)
    if t.get("fail_regex"):
        m = re.search(t["fail_regex"], text, re.MULTILINE)
        if m:
            end = text.find("\n", m.start())
            line = text[text.rfind("\n", 0, m.start()) + 1:end if end >= 0 else len(text)].strip()
            raise TierProblem(f"{FAIL_REGEX}: {line[:120]}", rc)
    return rc, sec, text


FINDING_FORMATS = {
    # gcc and clang warnings with -fdiagnostics-show-option, and clang-tidy
    "gcc": re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+): (?:warning|error): "
                      r".*?\[(?P<check>[^\]]+)\]\s*$", re.MULTILINE),
}


def _tree_path(path: str, root: Path) -> str:
    """Repo-relative path of a reported file; files outside the tree keep their path as
    `external:<path>`."""
    full = Path(path) if os.path.isabs(path) else root / path
    for r in (Path(os.path.realpath(root)), Path(os.path.normpath(root))):
        for p in (Path(os.path.realpath(full)), Path(os.path.normpath(full))):
            try:
                return p.relative_to(r).as_posix()
            except ValueError:
                pass
    return "external:" + os.path.normpath(path)


def parse_findings(text: str, root: Path, fmt: str = "gcc") -> set[tuple[str, int, int, str]]:
    """Unique (file, line, col, check) in a tool's output, file relative to root."""
    return {(_tree_path(m["file"], root), int(m["line"]), int(m["col"]), m["check"])
            for m in FINDING_FORMATS[fmt].finditer(text)}


def _findings_run(t, tree: Path, ctx: Ctx, side: str) -> tuple[set, int]:
    cmd = _fill(t["cmd"], repo=str(tree), base=ctx.base, ref_base=ctx.ref_arg or ctx.ref_base,
                files=ctx.files, out="")
    rc, _, text = _run_checked(t, cmd, tree, ctx.env, ctx.log, t.get("timeout"))
    if rc and not t.get("ignore_exit", True):
        raise TierProblem(f"exit {rc} at {side}", rc)
    return parse_findings(text, tree, t.get("format", "gcc")), rc


def _base_findings(t, ctx: Ctx) -> set:
    """Findings in the tree of `base` (last accepted step), cached per (sha, tier, cmd, env)."""
    key = {"kind": "findings", "env": ctx.env, "format": t.get("format", "gcc"),
           "ignore_exit": t.get("ignore_exit", True),
           "cmd": _fill(t["cmd"], repo="<repo>", base=ctx.base, ref_base=ctx.ref_arg or ctx.ref_base,
                        files=ctx.files, out="")}
    cache = _cache_dir(ctx.repo, ctx.base, t["name"]) / "findings.json"
    try:
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            ctx.log.write(f"\n(findings cache hit: {cache})\n")
            return {tuple(f) for f in data["findings"]}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    found, _ = _findings_run(t, base_tree(ctx.repo, ctx.base, keep=(ctx.ref_base,)), ctx, "base")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"key": key, "findings": sorted(found)}))
    return found


def _findings(t, ctx: Ctx) -> tuple[bool, str, int | None]:
    """Ratchet: no (file, check) may have more unique findings at head than at base."""
    base = _base_findings(t, ctx)
    head, rc = _findings_run(t, ctx.repo, ctx, "head")
    renamed = {bp: hp for _, bp, hp in ctx.changes if bp and hp and bp != hp}
    cb = Counter((renamed.get(f, f), c) for f, _, _, c in base)
    ch = Counter((f, c) for f, _, _, c in head)
    rises = sorted(k for k in ch if ch[k] > cb[k])
    totals = f"base {len(base)}, head {len(head)}"
    if not rises:
        return True, totals, rc
    items = [f"{f} [{c}] {cb[(f, c)]} -> {ch[(f, c)]}" for f, c in rises]
    more = f" (+{len(items) - 10} more)" if len(items) > 10 else ""
    return False, "findings rose: " + "; ".join(items[:10]) + more + f"; {totals}", rc


def observed_orders(levels: list[tuple[float, float]]) -> tuple[list[float], str]:
    """p_i = log(e_i / e_{i+1}) / log(h_i / h_{i+1}) for successive (h, e); ([], why) if invalid."""
    if len(levels) < 2:
        return [], f"need at least 2 levels, got {len(levels)}"
    for i, (h, e) in enumerate(levels, 1):
        if not (math.isfinite(h) and math.isfinite(e)):
            return [], f"level {i}: non-finite value ({h!r}, {e!r})"
        if h <= 0 or e <= 0:
            return [], f"level {i}: non-positive value ({h!r}, {e!r})"
    orders = []
    for (h0, e0), (h1, e1) in zip(levels, levels[1:]):
        if h0 == h1:
            return [], f"equal h {h0!r} on successive levels"
        orders.append(math.log(e0 / e1) / math.log(h0 / h1))
    return orders, ""


def _read_levels(text: str) -> tuple[list[tuple[float, float]], str]:
    levels = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        try:
            if len(parts) != 2:
                raise ValueError
            levels.append((float(parts[0]), float(parts[1])))
        except ValueError:
            return [], f"line {no}: expected 'h error', got {raw.strip()!r}"
    return levels, ""


def _convergence(t, ctx: Ctx) -> tuple[bool, str, int | None]:
    """Head only: the observed orders (the last `last` of them) must lie within `band`."""
    out = Path(tempfile.mkdtemp(prefix=f"codehealth-{t['name']}-"))
    try:
        cmd = _fill(t["cmd"], repo=str(ctx.repo), base=ctx.base, ref_base=ctx.ref_arg or ctx.ref_base,
                    files=ctx.files, out=str(out))
        rc, _, _ = _run_checked(t, cmd, ctx.repo, ctx.env, ctx.log, t.get("timeout"))
        if rc:
            return False, f"exit {rc} at head", rc
        name = t["outputs"][0]
        if not (out / name).is_file():
            return False, f"missing output: {name}", rc
        levels, why = _read_levels((out / name).read_text(errors="replace"))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    orders, why = observed_orders(levels) if not why else ([], why)
    if why:
        return False, why, rc
    lo, hi = (float(x) for x in t["band"])
    last = int(t.get("last", len(orders)))
    shown = " ".join(f"{p:.2f}" for p in orders)
    band = f"band {lo:g}..{hi:g}" + (f", last {last} checked" if "last" in t else "")
    if all(lo <= p <= hi for p in orders[-last:]):
        return True, f"orders {shown} ({band})", rc
    return False, f"orders {shown} outside {band}", rc


def run_tiers(repo: Path, base: str, cfg: config.Config, changes, cadence: str, log,
              mode: str = "refactor", keep_going: bool | None = None,
              kinds: tuple | None = None, ref_base: str | None = None,
              only: set | None = None, ref_arg: str | None = None) -> list[Check]:
    g = cfg.gate
    gitdir = _gitdir(repo)
    env = {k: str(v).replace("{gitdir}", str(gitdir)) for k, v in g.get("env", {}).items()}
    files = " ".join(shlex.quote(hp) for _, _, hp in changes if hp)
    keep_going = g.get("keep_going", False) if keep_going is None else keep_going
    ctx = Ctx(repo, base, ref_base or base, files, env, log, mode, cfg, changes, ref_arg or "")
    get_tree = lambda: base_tree(repo, ctx.ref_base, keep=(base,))  # noqa: E731
    checks = []
    for t in _selected(cfg, cadence, mode, gitdir):
        name, kind, t0 = t["name"], t.get("kind", "command"), time.monotonic()
        if (kinds and kind not in kinds) or (only is not None and name not in only):
            continue
        try:
            ok, detail, exit_code = _dispatch(t, kind, ctx, get_tree)
        except TierProblem as exc:   # skip_exit or fail_regex in any run of the tier
            ok, detail, exit_code = False, exc.detail, exc.rc
        checks.append(Check(name, ok, detail, time.monotonic() - t0, exit_code))
        log.write(f"--- tier {name}: {'PASS' if ok else 'FAIL'} {detail}\n")
        if not ok and not keep_going:
            break
    return checks


def _dispatch(t, kind: str, ctx: Ctx, get_tree) -> tuple[bool, str, int | None]:
    """Run one tier of any kind: (ok, detail, exit code)."""
    if kind == "command":
        rc, _, _ = _run_checked(t, _fill(t["cmd"], repo=str(ctx.repo), base=ctx.base,
                                         ref_base=ctx.ref_arg or ctx.ref_base, files=ctx.files, out=""),
                                ctx.repo, ctx.env, ctx.log, t.get("timeout"))
        return rc == 0, (f"exit {rc}" if rc else ""), rc
    if kind == "reference":
        return _reference(t, ctx, get_tree)
    if kind == "benchmark":
        return (*_benchmark(t, ctx, get_tree), None)
    if kind == "findings":
        return _findings(t, ctx)
    if kind == "convergence":
        return _convergence(t, ctx)
    raise ValueError(f"tier {t['name']}: unknown kind {kind!r}")


def function_debts(reports: dict[str, dict]) -> dict[str, float]:
    """{path::name: debt} for the functions with debt (several definitions of one name add up)."""
    out: dict[str, float] = {}
    for r in reports.values():
        for f in r["functions"]:
            if f["debt"] > 0:
                out[f["qualname"]] = round(out.get(f["qualname"], 0.0) + f["debt"], 2)
    return out


def debt_check(repo: Path, base: str, cfg: config.Config, changes,
               fns: dict | None = None) -> tuple[Check, float, float]:
    """Debt over the touched in-scope files must not rise, and no touched file may newly fail
    analysis (it would score 0 debt), in every mode: (check, before, after). `fns`, if given,
    receives {"before": {path::name: debt}, "after": {...}} over the touched files (functions
    with debt only), so that `codehealth log` can report a function target's own delta."""
    from .cli import analyze_ref, new_errors  # noqa: PLC0415 (cli imports this module)
    bp = {b for _, b, _ in changes if b and cfg.in_scope(b)}
    hp = {h for _, _, h in changes if h and cfg.in_scope(h)}
    rb, rh = analyze_ref(repo, base, cfg, bp), analyze_ref(repo, None, cfg, hp)
    if fns is not None:
        fns.update(before=function_debts(rb), after=function_debts(rh))
    db = sum(r["debt"] for r in rb.values())
    dh = sum(r["debt"] for r in rh.values())
    errors = new_errors(rb, rh, changes)
    # a file that crosses a file-size or duplication threshold fails here, at the step that
    # caused it, as `codehealth diff` fails it over the branch (a later step cannot undo it
    # without a restructure)
    counterpart = {h: b for _, b, h in changes if h}
    for path, r in sorted(rh.items()):
        old = rb.get(counterpart.get(path) or "")
        new = sorted(set(r["file_smells"]) - set(old["file_smells"] if old else []))
        if new:
            errors.append(f"{path}: new file smell(s) {', '.join(new)} (nloc {r['nloc']})")
    base_brains = {f["name"] for r in rb.values() for f in r["functions"] if f["brain"]}
    brains = sorted(f"{r['path']}::{f['name']}" for r in rh.values() for f in r["functions"]
                    if f["brain"] and f["name"] not in base_brains)
    note = [f"new brain function(s): {', '.join(brains[:3])} (an intermediate; the milestone must "
            f"bring the count back)"] if brains else []
    return (Check("debt", dh <= db + 1e-9 and not errors,
                  "; ".join([f"{db:.1f} -> {dh:.1f}"] + errors[:3] + note)), db, dh)


def _git_rc(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def expected_revert_tree(repo: Path, base: str, shas: list[str]) -> tuple[str | None, str]:
    """Tree of `base` with the commits `shas` reverted, newest first, computed with
    `git merge-tree --write-tree` on dangling commits (worktree and index untouched).
    Returns (tree, "") or (None, reason) when a SHA is unusable or a revert conflicts."""
    ver = _git_rc(repo, "version").stdout.split()[2:3]
    nums = [int(x) for x in re.findall(r"\d+", ver[0])[:2]] if ver else []
    if nums < [2, 38]:
        raise RuntimeError("--revert needs git >= 2.38 (merge-tree --write-tree), found "
                           + (ver[0] if ver else "unknown"))
    full = []
    for s in shas:
        try:
            full.append(gitio.rev(repo, s))
        except gitio.GitError:
            return None, f"{s} does not resolve to a commit"
    for s in full:
        parents = _git_rc(repo, "rev-list", "--parents", "-n", "1", s).stdout.split()[1:]
        if len(parents) != 1:
            return None, f"{s[:10]} is not a single-parent commit"
        if _git_rc(repo, "merge-base", "--is-ancestor", s, base).returncode != 0:
            return None, f"{s[:10]} is not an ancestor of the base {base[:10]}"
    order = [c for c in _git_rc(repo, "rev-list", "--topo-order", base).stdout.split() if c in set(full)]
    ident = {k: v for n in ("AUTHOR", "COMMITTER") for k, v in
             ((f"GIT_{n}_NAME", "codehealth"), (f"GIT_{n}_EMAIL", "codehealth@localhost"))}
    cur, tree = base, None
    for r in order:
        m = _git_rc(repo, "merge-tree", "--write-tree", f"--merge-base={r}", cur, f"{r}^")
        if m.returncode == 1:
            return None, f"revert of {r[:10]} does not apply cleanly at base; rework it as a normal step"
        if m.returncode != 0:
            raise RuntimeError(f"git merge-tree: {m.stderr.strip()}")
        tree = m.stdout.split()[0]
        c = _git_rc(repo, "commit-tree", tree, "-p", cur, "-m", "codehealth revert check", env=ident)
        if c.returncode != 0:
            raise RuntimeError(f"git commit-tree: {c.stderr.strip()}")
        cur = c.stdout.strip()
    return tree, ""


def revert_check(repo: Path, base: str, shas: list[str], head_tree: str) -> Check:
    """The worktree must be exactly `base` with `shas` reverted."""
    tree, why = expected_revert_tree(repo, base, shas)
    if tree is None:
        return Check("revert", False, why)
    if tree == head_tree:
        return Check("revert", True, f"{len(shas)} commit(s) reverted exactly")
    paths = _git_rc(repo, "diff-tree", "-r", "--name-only", tree, head_tree).stdout.split()
    more = f" (+{len(paths) - 10} more)" if len(paths) > 10 else ""
    return Check("revert", False, "worktree differs from the exact revert: " + ", ".join(paths[:10]) + more)


def gate(repo: Path, base_ref: str, cadence: str, log_path: Path,
         config_override: Path | None = None, mode: str = "refactor",
         ref_base: str | None = None, require_gain: bool = False,
         info: dict | None = None, revert: list[str] | None = None,
         head_tree: str | None = None, plan: str | None = None) -> tuple[list[Check], str, str]:
    """config_override is for local trials only; CI must use the base's committed config.

    mode "refactor": references bitwise, benchmark may not slow down beyond max_slowdown.
    mode "optimize": references within each tier's declared `tolerance`, the primary benchmark
    must reach min_speedup. Debt may not rise in either mode. Returns (checks, base, ref_base).
    Static checks and debt use `base` (last accepted step); reference and benchmark tiers compare
    head with `ref_base` (campaign base; default: the same as base). With `require_gain` a static
    check `gain` fails unless debt over the touched files dropped. `info`, if given, receives
    debt_before and debt_after (rounded to 2 decimals). With `revert` (SHAs) a static check
    `revert` requires the worktree tree (`head_tree`) to equal base with those commits reverted,
    and the debt and architecture checks report but pass (an exact revert may restore debt or a
    violation the reverted step removed).
    mode "restructure" (needs `plan`, a path under .codehealth/restructure/ read at base): as
    refactor, plus the `plan` check and the restructure public-API rule; `info["plan"]` receives
    {path, blob, id}.
    """
    if mode not in ("refactor", "optimize", "restructure", "interface"):
        raise ValueError(f"unknown mode {mode!r}")
    if (mode in ("restructure", "interface")) != bool(plan):
        raise ValueError("--mode restructure and --mode interface need --plan, and --plan is only for those modes")
    if plan and revert:
        raise ValueError("--plan and --revert are incompatible")
    base = gitio.merge_base(repo, base_ref, "HEAD")
    ref_sha = gitio.merge_base(repo, ref_base, "HEAD") if ref_base else base
    cfg = (config.parse(config_override.read_text(), f"override:{config_override}")
           if config_override else config.load(repo, base))
    plan_data = None
    if plan:
        if mode == "interface":
            from .interface import load_plan  # noqa: PLC0415
        else:
            from .restructure import load_plan  # noqa: PLC0415
        if mode == "interface" and not cfg.gate.get("public_api", False):
            raise ValueError("--mode interface needs [gate] public_api = true (it is the rule that checks the plan)")
        plan_data = {**load_plan(repo, base, plan, cfg), "kind": mode}
        if info is not None:
            info["plan"] = {"path": plan_data["path"], "blob": plan_data["blob"], "id": plan_data["id"]}
    mode = "refactor" if mode in ("restructure", "interface") else mode   # tiers and config checks as in refactor
    changes = gitio.changes(repo, base, None)
    fns: dict = {}
    debt, db, dh = debt_check(repo, base, cfg, changes, fns)
    if info is not None:
        info.update(debt_before=round(db, 2), debt_after=round(dh, 2), function_debt=fns)
    extra = []
    if revert:
        debt.ok = True
        debt.detail += " (rise allowed: exact revert)"
        extra = [revert_check(repo, base, revert, head_tree or gitio.worktree_tree(repo))]
    static = static_checks(repo, base, cfg, changes, trial=config_override is not None, plan=plan_data,
                           cadence=cadence)
    for c in static:
        if revert and c.name == "architecture" and not c.ok:
            c.ok = True
            c.detail += " (allowed: exact revert)"
    checks = config_checks(cfg, changes, cadence, mode) + static + [debt] + extra
    if require_gain:
        gained = dh < db - 1e-9
        checks.append(Check("gain", gained, f"{db:.1f} -> {dh:.1f}" if gained
                            else f"no debt reduction: {db:.1f} -> {dh:.1f}"))
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


_CPP_NUM = re.compile(rb"(?<![\w.'])((?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)([uUlLfF]{0,3})(?![\w.'])")


def _cpp_blank(line: bytes, in_comment: bool) -> tuple[bytes, bool]:
    """The line with comments, string and char literals replaced by spaces (same byte offsets)."""
    out, i, n = bytearray(line), 0, len(line)
    while i < n:
        if in_comment:
            j = line.find(b"*/", i)
            end = n if j < 0 else j + 2
            out[i:end] = b" " * (end - i)
            in_comment, i = j < 0, end
            continue
        c = line[i:i + 1]
        if line.startswith(b"//", i):
            out[i:] = b" " * (n - i)
            break
        if line.startswith(b"/*", i):
            in_comment = True
            continue
        if c in (b'"', b"'"):
            j = i + 1
            while j < n and line[j:j + 1] != c:
                j += 2 if line[j:j + 1] == b"\\" else 1
            out[i:min(j + 1, n)] = b" " * (min(j + 1, n) - i)
            i = j + 1
            continue
        i += 1
    return bytes(out), in_comment


def _cpp_literal_sites(source: str, line: int) -> list[tuple[int, int, int, object]]:
    """(lineno, col, end_col, value) of decimal numeric literals on one line of C/C++/CUDA
    source, outside comments, strings and preprocessor lines; a suffix (f, u, L) stays."""
    in_comment, sites = False, []
    for i, raw in enumerate(source.encode().split(b"\n"), 1):
        clean, in_comment = _cpp_blank(raw, in_comment)
        if i != line:
            continue
        if clean.lstrip().startswith(b"#"):
            return []
        for m in _CPP_NUM.finditer(clean):
            txt = m.group(1).decode()
            if txt.startswith("0") and len(txt) > 1 and txt[1].isdigit():
                continue                                   # octal: leave alone
            is_float = any(ch in txt for ch in ".eE") or b"f" in m.group(2).lower()
            sites.append((i, m.start(1), m.end(1), float(txt) if is_float else int(txt)))
        break
    return sites


def mutate_source(source: str, site, nth: tuple[int, int] | None = None) -> tuple[str, str]:
    lineno, col, end, v = site
    new = repr(v + 1) if isinstance(v, int) else repr(v * 1.5 + 1.0)
    lines = source.split("\n")
    raw = lines[lineno - 1].encode()
    lines[lineno - 1] = (raw[:col] + new.encode() + raw[end:]).decode()
    where = f"line {lineno} col {col + 1}" + (f" (literal {nth[0]} of {nth[1]})" if nth else "")
    return "\n".join(lines), f"{where}: {v!r} -> {new}"


def parse_mutate(spec: str) -> tuple[str, int, int]:
    """path:line or path:line:N (N = 1-based literal index on that line), parsed from the right."""
    parts = spec.split(":")
    if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit() and ":".join(parts[:-2]):
        return ":".join(parts[:-2]), int(parts[-2]), int(parts[-1])
    if len(parts) >= 2 and parts[-1].isdigit() and ":".join(parts[:-1]):
        return ":".join(parts[:-1]), int(parts[-1]), 1
    raise ValueError(f"--mutate expects path:line or path:line:N, got {spec!r}")


def self_test_kind(cfg: config.Config) -> str:
    """What the self-test counts: reference tiers if there are any, else behaviour command tiers."""
    tiers = _selected(cfg, "campaign", "refactor")
    return "reference" if any(t.get("kind") == "reference" for t in tiers) else "behaviour command"


def _self_test_result(c: Check, t: dict, baseline_rc: int | None) -> Check:
    """Classify a mutated run of tier t: detected, missed, or error (exit code outside detect_exit)."""
    allowed = t.get("detect_exit")
    rc = c.exit_code
    if c.ok:
        outcome, detail = "missed", c.detail
    elif c.detail.startswith((NOT_RUN, FAIL_REGEX)):   # skipped or not run is never a detection
        outcome, detail = "error", c.detail
    elif allowed and rc not in (None, 0) and rc not in allowed:
        outcome, detail = "error", f"exit {rc} not in detect_exit {list(allowed)}"
    else:
        outcome, detail = "detected", c.detail
    return Check(c.name, outcome == "detected", detail, c.seconds, rc, outcome, baseline_rc)


def self_test(repo: Path, cfg: config.Config, log_path: Path,
              mutate: str | None = None, ref_base: str | None = None) -> tuple[bool, list[Check], str]:
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
            path, line, nth = parse_mutate(mutate)
            candidates = [path]
        else:
            path, line, nth = None, None, None
            candidates = sorted(p for p in gitio.ls_files(repo, head)
                                if cfg.in_scope(p) and p.endswith(".py"))
        # Baseline: every behaviour tier must pass on the unmutated HEAD, else "detected" is vacuous.
        beh = {t["name"] for t in _selected(cfg, "campaign", "refactor")
               if t.get("kind", "command") in ("reference", "command") and t.get("behaviour", True)}
        with open(log_path, "w") as log:
            base_checks = run_tiers(tree, head, cfg, [], "campaign", log, "refactor",
                                    keep_going=True, kinds=("reference", "command"), only=beh,
                                    ref_arg=ref_base)
        broken = [c for c in base_checks if not c.ok]
        if broken:
            return False, [Check(c.name, False, f"tier {c.name} fails without mutation"
                                 + (f" ({c.detail})" if c.detail else ""), c.seconds,
                                 outcome="error", baseline_exit_code=c.exit_code)
                           for c in broken], "none (baseline failed)"
        gitio.git(tree, "clean", "-fdxq")   # drop stale bytecode: a same-size mutation keeps mtime and size
        target = None
        for p in candidates:
            if config.EXTENSIONS.get(Path(p).suffix) == "cpp":
                sites = _cpp_literal_sites((tree / p).read_text(), line)
            else:
                sites = _literal_sites((tree / p).read_text(), line)
            if mutate and not 1 <= nth <= len(sites):
                listing = ", ".join(f"{i} = {s[3]!r} at col {s[1] + 1}" for i, s in enumerate(sites, 1))
                raise ValueError(f"--mutate {mutate}: literal {nth} not found on line {line} of {p} "
                                 + (f"(literals: {listing})" if sites else "(no numeric literal on that line)"))
            if sites:
                target = (p, sites[nth - 1 if mutate else 0], (nth, len(sites)) if mutate else None)
                break
        if target is None:
            raise RuntimeError("self-test: no numeric literal found to mutate (C/C++: pass "
                               "--mutate path:line[:N] on a literal the tests execute)")
        p, site, which = target
        text, what = mutate_source((tree / p).read_text(), site, which)
        (tree / p).write_text(text)
        what = f"{p} {what}"
        changes = gitio.changes(tree, head, None)
        with open(log_path, "a") as log:
            checks = run_tiers(tree, head, cfg, changes, "campaign", log, "refactor",
                               keep_going=True, kinds=("reference", "command"), ref_arg=ref_base)
        tiers = _selected(cfg, "campaign", "refactor")
        by_name = {t["name"]: t for t in tiers}
        base_rc = {c.name: c.exit_code for c in base_checks}
        refs = {t["name"] for t in tiers if t.get("kind") == "reference"}
        beh = {t["name"] for t in tiers if t.get("kind", "command") == "command"
               and t.get("behaviour", True)}
        # Every reference tier must detect the mutation; without reference tiers, at least one
        # behaviour command tier must (tiers marked `behaviour = false`, lint and format, do not count).
        counted = refs or beh
        out = [_self_test_result(c, by_name[c.name], base_rc.get(c.name)) for c in checks if c.name in counted]
        if refs:
            ok = all(c.ok for c in out) and len(out) == len(refs)
        else:
            ok = any(c.ok for c in out)
            if not beh:
                out.append(Check("self-test", False, "no reference or behaviour command tier", outcome="error"))
        return ok, out, what
    finally:
        _remove_tree(repo, tree)
