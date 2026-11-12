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
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from . import config, gitio


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


def run_tiers(repo: Path, base: str, cfg: config.Config, changes, cadence: str, log) -> list[Check]:
    g = cfg.gate
    env = {k: str(v) for k, v in g.get("env", {}).items()}
    files = " ".join(shlex.quote(hp) for _, _, hp in changes if hp)
    tiers = [t for t in g.get("tier", []) if cadence == "campaign" or t.get("cadence", "step") == "step"]
    checks, base_tree = [], None
    try:
        for t in tiers:
            name, kind, timeout = t["name"], t.get("kind", "command"), t.get("timeout")
            if kind == "command":
                rc, sec = _run(_fill(t["cmd"], repo=str(repo), base=base, files=files, out=""),
                               repo, env, log, timeout)
                checks.append(Check(name, rc == 0, f"exit {rc}" if rc else "", sec))
            elif kind == "reference":
                if base_tree is None:
                    base_tree = Path(tempfile.mkdtemp(prefix="codehealth-base-"))
                    gitio.git(repo, "worktree", "add", "--detach", "-f", str(base_tree), base)
                names, t0, detail = t["outputs"], time.monotonic(), ""
                runs = []
                for cwd in (base_tree, base_tree, repo):
                    out = Path(tempfile.mkdtemp(prefix=f"codehealth-{name}-"))
                    rc, _ = _run(_fill(t["cmd"], repo=str(cwd), base=base, files=files, out=str(out)),
                                 cwd, env, log, timeout)
                    if rc != 0:
                        detail = f"exit {rc} at {'head' if cwd == repo else 'base'}"
                        break
                    runs.append(_outputs(out, names))
                if not detail:
                    b1, b2, h = runs
                    missing = [n for n in names if b1[n] is None]
                    nondet = [n for n in names if b1[n] != b2[n]]
                    changed = [n for n in names if b1[n] != h[n]]
                    if missing:
                        detail = "missing at base: " + ", ".join(missing)
                    elif nondet:
                        detail = "not deterministic at base: " + ", ".join(nondet)
                    elif changed:
                        detail = "differs from base: " + ", ".join(changed)
                checks.append(Check(name, not detail, detail, time.monotonic() - t0))
            else:
                raise ValueError(f"tier {name}: unknown kind {kind!r}")
            log.write(f"--- tier {name}: {'PASS' if checks[-1].ok else 'FAIL'} {checks[-1].detail}\n")
            if not checks[-1].ok and not g.get("keep_going", False):
                break
    finally:
        if base_tree is not None:
            gitio.git(repo, "worktree", "remove", "--force", str(base_tree))
    return checks


def gate(repo: Path, base_ref: str, cadence: str, log_path: Path,
         config_override: Path | None = None) -> tuple[list[Check], str]:
    """config_override is for local trials only; CI must use the base's committed config."""
    base = gitio.merge_base(repo, base_ref, "HEAD")
    cfg = (config.parse(config_override.read_text(), f"override:{config_override}")
           if config_override else config.load(repo, base))
    changes = gitio.changes(repo, base, None)
    checks = static_checks(repo, base, cfg, changes)
    if all(c.ok for c in checks):
        with open(log_path, "w") as log:
            checks += run_tiers(repo, base, cfg, changes, cadence, log)
    return checks, base
