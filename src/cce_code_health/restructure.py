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
from .architecture import module_name

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


def _import_source(node: ast.ImportFrom, path: str, candidates: list[str], roots) -> str | None:
    """The candidate file an ImportFrom in `path` refers to, if any. Absolute imports resolve
    exactly through the import roots (architecture.module_name)."""
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
    for c in candidates:
        m = module_name(c, roots)
        if m and m[0] == node.module:
            return c
    return None


def _binders(stmt) -> tuple[set[str], bool]:
    """(names a top-level statement binds or deletes in module scope, star import?).
    Nested function, class and lambda bodies are separate scopes and are not entered;
    comprehension targets are local in Python 3 and skipped likewise."""
    if isinstance(stmt, ast.Import):
        return {(a.asname or a.name).split(".")[0] for a in stmt.names}, False
    if isinstance(stmt, ast.ImportFrom):
        names = {a.asname or a.name for a in stmt.names}
        return names - {"*"}, "*" in names
    names, star, stack = set(), False, [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            stack += node.decorator_list + ([] if isinstance(node, ast.ClassDef) else
                                            node.args.defaults
                                            + [d for d in node.args.kw_defaults if d is not None])
            continue
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        if node is not stmt and isinstance(node, (ast.Import, ast.ImportFrom)):
            sub, s = _binders(node)
            names |= sub
            star |= s
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        stack.extend(ast.iter_child_nodes(node))
    return names, star


def _reexports(source: str, path: str, candidates: list[str], roots) -> dict[str, str]:
    """Names N whose LAST top-level binder in source is an unaliased (or same-name aliased)
    `from <candidate> import N`: N -> candidate file. A later def, class, assignment, import,
    star import or `global N` anywhere in the module disqualifies N."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    globals_ = {n for node in ast.walk(tree) if isinstance(node, ast.Global) for n in node.names}
    for stmt in tree.body:
        names, star = _binders(stmt)
        if star:
            out.clear()
        for n in names:
            out.pop(n, None)
        if isinstance(stmt, ast.ImportFrom):
            src = _import_source(stmt, path, candidates, roots)
            if src:
                out.update({a.name: src for a in stmt.names if a.asname in (None, a.name)})
    return {n: src for n, src in out.items() if n not in globals_}


def public_api_problems(plan: dict, py, tb: dict, th: dict, public_api, roots=(".",)) -> list[str]:
    """Restructure public-API rule over the touched in-scope (base path, head path) pairs.

    No public name may disappear from any module. A name N of an old module may turn from a
    definition into a re-export only when the last top-level binder of N in the old module is
    `from <new> import N`, <new> is an added file matching new_files (absolute imports resolve
    through `roots`), and <new>'s entries {N, N.*} equal the old module's base entries {N, N.*}
    exactly (signature, decorators, bases, fields: whatever `_public_api` compares). A member
    added to a re-exported class needs its own `path:N.member` entry in new_public. Other public
    names in added files or new in old modules must be listed in new_public (a listed name covers
    its class members; `__all__` of a new file is exempt), and every listed name must exist at
    head."""
    planned = [hp for hp in added_paths([(None, bp, hp) for bp, hp in py])
               if hp in th and any(config.match(hp, g) for g in plan["new_files"])]
    apis = {hp: public_api(th[hp], hp) for hp in planned}
    listed = set(plan["new_public"])
    problems = []
    pinned: dict[str, dict[str, set]] = {}   # planned file -> re-exported N -> base {N, N.*}
    moves = {}
    for bp, hp in py:
        if bp and bp == hp and bp in tb and hp in th:
            ab = public_api(tb[bp], bp)
            moves[hp] = _reexports(th[hp], hp, planned, roots) if ab else {}
            for n, src in moves[hp].items():
                pinned.setdefault(src, {}).setdefault(n, set()).update(
                    k for k in ab if k.split(".")[0] == n)

    def allowed(path, name):
        top = name.split(".")[0]
        if name != top and top in pinned.get(path, {}):   # a moved member, or listed exactly
            return name in pinned[path][top] or f"{path}:{name}" in listed
        return f"{path}:{name}" in listed or f"{path}:{top}" in listed

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
        moved = moves.get(hp, {})
        for name, s in ab.items():
            if ah.get(name) == s:
                continue
            src = moved.get(name.split(".")[0])
            if src and ah.get(name, "re-export") != "re-export":
                src = None
            if src and apis[src].get(name) == s:
                continue
            if src:
                problems.append(f"{where}: {name}{s} re-exported from {src} with a different API "
                                f"({apis[src].get(name, 'missing')})")
            elif name in ah:
                problems.append(f"{where}: {name}{s} -> {ah[name]}")
            else:
                problems.append(f"{where}: {name} removed")
        for n in ah:
            if n in ab or allowed(where, n) or (bp is None and n == "__all__"):
                continue
            top = n.split(".")[0]
            if n != top and top in pinned.get(where, {}):
                problems.append(f"{where}: {n} added to re-exported {top} (not in the plan's new_public; "
                                f"list {where}:{n})")
            else:
                problems.append(f"{where}: {n} added (not in the plan's new_public)")
    for entry in sorted(listed):
        path, name = entry.split(":")
        if name not in (public_api(th[path], path) if path in th else {}):
            problems.append(f"plan new_public {entry}: not a public name of a touched file at head")
    return problems
