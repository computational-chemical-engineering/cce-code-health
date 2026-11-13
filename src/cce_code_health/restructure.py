"""Restructure class: move code into new modules under a human-approved plan.

`codehealth gate --mode restructure --plan .codehealth/restructure/<id>.toml` reads the plan
from the BASE commit (a step cannot write its own plan; `.codehealth/*` is forbidden anyway):

    id = "split-solver"
    approved_by = "jane"                       # non-empty
    reason = "solver.py mixes assembly and time stepping"
    new_files = ["src/pkg/solver/assembly.py"] # globs; added files must match one
    new_public = ["src/pkg/solver/assembly.py:assemble"]   # path:Name, public names that may appear

Differs from refactor mode in two static rules: `plan` (every added file, including the new
path of a rename, matches `new_files`) and `public_api` (see `public_api_problems`). Everything
else, tiers included, runs as in refactor mode.
"""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from . import config, gitio

PLAN_DIR = ".codehealth/restructure/"
PLAN_KEYS = {"id", "approved_by", "reason", "new_files", "new_public"}


def load_plan(repo: Path, base: str, path: str, cfg: config.Config) -> dict:
    """The plan at the base commit, validated; ValueError (usage error) otherwise."""
    path = path[2:] if path.startswith("./") else path
    if not path.startswith(PLAN_DIR) or not path.endswith(".toml"):
        raise ValueError(f"--plan must be a .toml file under {PLAN_DIR}, got {path!r}")
    texts = gitio.read_files(repo, base, [path])
    if path not in texts:
        raise ValueError(f"--plan {path} does not exist at the base {base[:10]}: commit the approved plan first")
    try:
        data = tomllib.loads(texts[path])
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"plan {path}: not valid TOML: {exc}") from None
    bad = [f"unknown key {k!r}" for k in sorted(set(data) - PLAN_KEYS)]
    for k in ("id", "approved_by", "reason"):
        if not isinstance(data.get(k), str) or not data[k].strip():
            bad.append(f"{k} must be a non-empty string")
    for k in ("new_files", "new_public"):
        v = data.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            bad.append(f"{k} must be a list of strings")
    if not bad:
        bad += [f"new_files entry {g!r} is out of scope" for g in data.get("new_files", [])
                if not cfg.in_scope(g)]
        bad += [f"new_public entry {e!r} must be path/to/file.py:Name" for e in data.get("new_public", [])
                if e.count(":") != 1 or not all(e.split(":"))]
    if bad:
        raise ValueError(f"plan {path}: " + "; ".join(bad))
    blob = gitio.git(repo, "rev-parse", f"{base}:{path}").decode().strip()
    return {**data, "new_files": list(data.get("new_files", [])),
            "new_public": list(data.get("new_public", [])), "path": path, "blob": blob}


def added_paths(changes) -> list[str]:
    """Head paths that do not exist under the same name at base (added, copied, renamed to)."""
    return sorted(hp for _, bp, hp in changes if hp and bp != hp)


def plan_problems(plan: dict, changes) -> list[str]:
    return [f"unplanned new file: {p}" for p in added_paths(changes)
            if not any(config.match(p, g) for g in plan["new_files"])]


def _module_suffix(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    if p.endswith("/__init__"):
        p = p[:-len("/__init__")]
    return p.replace("/", ".")


def _import_source(node: ast.ImportFrom, path: str, candidates: list[str]) -> str | None:
    """The candidate file an ImportFrom in `path` refers to, if any."""
    if node.level:
        parts = path.split("/")[:-1]
        if node.level - 1 > len(parts):
            return None
        parts = parts[:len(parts) - (node.level - 1)] + (node.module.split(".") if node.module else [])
        stem = "/".join(parts)
        for c in candidates:
            if c in (stem + ".py", stem + "/__init__.py"):
                return c
        return None
    mod = node.module or ""
    for c in candidates:
        s = _module_suffix(c)
        if s == mod or s.endswith("." + mod):
            return c
    return None


def _reexports(source: str, path: str, candidates: list[str]) -> dict[str, str]:
    """Top-level `from X import N` (unaliased, or aliased to the same name) in source: N -> file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            src = _import_source(node, path, candidates)
            if src:
                out.update({a.name: src for a in node.names if a.asname in (None, a.name)})
    return out


def public_api_problems(plan: dict, py, tb: dict, th: dict, public_api) -> list[str]:
    """Restructure public-API rule over the touched in-scope (base path, head path) pairs.

    No public name may disappear from any module. A name of an old module may turn from a
    definition into a re-export (`from <new> import Name`) only when <new> is an added file
    matching new_files whose own API has the identical entry (signature, decorators, bases,
    fields: whatever `_public_api` compares); for a class, its members move with it. Public
    names in added files or new in old modules must be listed in new_public (a listed name
    covers its class members; `__all__` of a new file is exempt), and every listed name must
    exist at head."""
    planned = [hp for hp in added_paths([(None, bp, hp) for bp, hp in py])
               if hp in th and any(config.match(hp, g) for g in plan["new_files"])]
    apis = {hp: public_api(th[hp], hp) for hp in planned}
    listed = set(plan["new_public"])
    problems = []

    def allowed(path, name):
        return f"{path}:{name}" in listed or f"{path}:{name.split('.')[0]}" in listed

    for bp, hp in py:
        ab = public_api(tb[bp], bp) if bp in tb else {}
        ah = public_api(th[hp], hp) if hp in th else {}
        where = hp or bp
        if bp and not hp:
            if ab:
                problems.append(f"{bp}: module removed (old import paths must keep working)")
            continue
        if bp and bp != hp and ab:
            problems.append(f"{bp}: module moved to {hp} (old import paths must keep working)")
            continue
        moved = _reexports(th[hp], hp, planned) if ab and hp in th else {}
        for name, s in ab.items():
            if ah.get(name) == s:
                continue
            src = moved.get(name.split(".")[0])
            if src and apis[src].get(name) == s:
                continue
            if src:
                problems.append(f"{where}: {name}{s} re-exported from {src} with a different API "
                                f"({apis[src].get(name, 'missing')})")
            elif name in ah:
                problems.append(f"{where}: {name}{s} -> {ah[name]}")
            else:
                problems.append(f"{where}: {name} removed")
        problems += [f"{where}: {n} added (not in the plan's new_public)" for n in ah
                     if n not in ab and not allowed(where, n) and not (bp is None and n == "__all__")]
    for entry in sorted(listed):
        path, name = entry.split(":")
        if name not in (public_api(th[path], path) if path in th else {}):
            problems.append(f"plan new_public {entry}: not a public name of a touched file at head")
    return problems
