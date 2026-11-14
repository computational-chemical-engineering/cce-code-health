"""Interface class: change the public API on purpose, exactly as an approved plan lists.

`codehealth gate --mode interface --plan .codehealth/interface/<id>.toml` reads the plan from
the BASE commit (a step cannot write its own plan; `.codehealth/*` is forbidden anyway). The
mode needs `[gate] public_api = true` (usage error otherwise):

    id = "adr-0076-w4-1"
    approved_by = "jane"                          # non-empty
    reason = "ADR 0076: drop the compatibility re-exports of pkg.api"
    removed = ["src/pkg/api.py:old_solve",        # path:Name, a public name that disappears
               "src/pkg/compat.py"]               # a path alone: the whole module goes
    renamed = {"src/pkg/a.py:Solver" = "src/pkg/core/solver.py:Solver",   # path:Name -> path:Name
               "src/pkg/b.py" = "src/pkg/core/b.py"}                      # module -> module
    changed = ["src/pkg/run.py:run"]              # same name and module, new signature
    added = ["src/pkg/core/solver.py:make_solver"]   # new public names that are not renames
    new_files = ["src/pkg/core/*.py"]             # globs; added files must match one
    callers = ["tests/**", "docs/examples/**"]    # out-of-scope or forbidden files that follow

Static rules that differ from refactor mode:
- `plan`: every added in-scope file matches `new_files`; every touched path outside the scope
  or under `forbid` matches `callers` (frozen paths and `.codehealth/` stay refused).
- `public_api` (exact; see `public_api_problems`): the step's public-API differences over the
  touched in-scope Python files equal the plan, no fewer and no more.
- `callers` (see `caller_problems`): a caller file may only follow the plan: after replacing
  the plan's old names and modules by the new ones, it equals its base version, except that
  statements naming a removed name may be deleted and a statement naming a changed name may be
  rewritten into one of the same kind that still names it. Imports follow the renames. No
  caller file is added, deleted or renamed.
- `literals`: the elements of `__all__` in the files the plan names are left to `public_api`.
Tiers run as in refactor mode. `codehealth log` records class `interface`; accepted needs all
checks ok and needs no debt drop (the API change is the purpose; debt still may not rise).
"""
from __future__ import annotations

import ast
import difflib
import re
import tomllib
from collections import Counter
from pathlib import Path

from . import config, gitio

PLAN_DIR = ".codehealth/interface/"
PLAN_KEYS = {"id", "approved_by", "reason", "removed", "renamed", "changed", "added", "new_files", "callers"}


# -- plan --------------------------------------------------------------------------------------

def _split(e: str) -> tuple[str, str | None]:
    path, _, name = e.partition(":")
    return path, (name or None)


def _entry_ok(e: str) -> bool:
    """`path.py` or `path.py:Name[.member]`."""
    path, _, name = e.partition(":")
    return path.endswith(".py") and (":" not in e or (bool(name) and ":" not in name))


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
    for k in ("removed", "changed", "added", "new_files", "callers"):
        v = data.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            bad.append(f"{k} must be a list of strings")
    ren = data.get("renamed", {})
    if not isinstance(ren, dict) or not all(isinstance(v, str) and v for v in ren.values()):
        bad.append("renamed must be a table of \"old\" = \"new\" strings")
    if bad:
        raise ValueError(f"plan {path}: " + "; ".join(bad))
    removed, changed, added = data.get("removed", []), data.get("changed", []), data.get("added", [])
    entries = [*removed, *changed, *added, *ren, *ren.values()]
    bad += [f"entry {e!r} must be path/to/file.py or path/to/file.py:Name" for e in entries if not _entry_ok(e)]
    bad += [f"{k} entry {e!r} must name a public name (path:Name)"
            for k, v in (("added", added), ("changed", changed)) for e in v if ":" not in e]
    for old, new in ren.items():
        if (":" in old) != (":" in new):
            bad.append(f"renamed {old!r} = {new!r}: both path:Name, or both module paths")
    bad += [f"entry {e!r} is out of scope" for e in entries if _entry_ok(e) and not cfg.in_scope(_split(e)[0])]
    gone_mods = {e for e in removed if ":" not in e} | {o for o in ren if ":" not in o}
    bad += [f"entry {e!r} names a module the plan removes or renames: list the module entry only, "
            f"or name the new path" for e in entries if ":" in e and _split(e)[0] in gone_mods]
    names = [e for e in [*removed, *changed, *added, *ren] if ":" in e]
    bad += [f"entry {e!r} is listed twice" for e in sorted({e for e in names if names.count(e) > 1})]
    bad += [f"new_files entry {g!r} is out of scope" for g in data.get("new_files", []) if not cfg.in_scope(g)]
    for g in data.get("callers", []):
        if g.strip("*/") == "" or config.match(".codehealth/x.toml", g):
            bad.append(f"callers entry {g!r} is too broad (it would cover .codehealth/ or everything)")
    if not (removed or ren or changed or added):
        bad.append("the plan lists no change (removed, renamed, changed or added)")
    if bad:
        raise ValueError(f"plan {path}: " + "; ".join(bad))
    blob = gitio.git(repo, "rev-parse", f"{base}:{path}").decode().strip()
    return {"id": data["id"], "approved_by": data["approved_by"], "reason": data["reason"],
            "removed": list(removed), "renamed": dict(ren), "changed": list(changed), "added": list(added),
            "new_files": list(data.get("new_files", [])), "callers": list(data.get("callers", [])),
            "path": path, "blob": blob}


def exempt(plan: dict) -> list[str]:
    """Globs the scope and forbid checks skip in interface mode (checked by `caller_problems`)."""
    return list(plan["callers"])


def caller_paths(plan: dict, changes, cfg: config.Config, forbid: list[str]) -> list[tuple]:
    """(status, base path, head path) of touched files that are callers: outside the scope or
    forbidden, and matching `callers`."""
    def outside(p):
        return bool(p) and (not cfg.in_scope(p) or any(config.match(p, g) for g in forbid))
    return [(st, bp, hp) for st, bp, hp in changes
            if any(outside(p) and any(config.match(p, g) for g in plan["callers"]) for p in (bp, hp))]


def plan_problems(plan: dict, changes, cfg: config.Config, forbid: list[str]) -> list[str]:
    """Added in-scope files outside new_files; touched out-of-scope or forbidden paths outside callers."""
    out = [f"unplanned new file: {hp}" for _, bp, hp in changes
           if hp and bp != hp and cfg.in_scope(hp) and not any(config.match(hp, g) for g in forbid)
           and not any(config.match(hp, g) for g in plan["new_files"])]
    touched = sorted({p for _, bp, hp in changes for p in (bp, hp) if p})
    out += [f"{p}: outside the scope or forbidden, and not in the plan's callers" for p in touched
            if (not cfg.in_scope(p) or any(config.match(p, g) for g in forbid))
            and not any(config.match(p, g) for g in plan["callers"])]
    return out


def all_strings(plan: dict, texts: dict[str, str]) -> Counter:
    """repr() of the `__all__` elements of the files the plan names (left to public_api by the
    literals rule)."""
    paths = {_split(e)[0] for e in [*plan["removed"], *plan["changed"], *plan["added"],
                                     *plan["renamed"], *plan["renamed"].values()]}
    out: Counter = Counter()
    for p in paths & set(texts):
        v = _all_value(texts[p])
        if v is not None:
            out.update(repr(x) for x in v)
    return out


def _all_value(source: str) -> list | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in _flat(tree.body):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) and node.value else [])
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            try:
                v = ast.literal_eval(node.value)
            except ValueError:
                return None
            return list(v) if isinstance(v, (list, tuple)) else None
    return None


# -- API with every binding --------------------------------------------------------------------

def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _flat(body: list) -> list:
    """Statements of a block with if/try/with/for/while bodies inlined (not `if TYPE_CHECKING:`)."""
    out = []
    for node in body:
        if isinstance(node, ast.If):
            if not _is_type_checking(node.test):
                out += _flat(node.body)
            out += _flat(node.orelse)
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            out += _flat(node.body) + _flat(node.orelse) + _flat(node.finalbody)
            for h in node.handlers:
                out += _flat(h.body)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            out += _flat(node.body)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            out += _flat(node.body) + _flat(node.orelse)
        else:
            out.append(node)
    return out


def _targets(node) -> list[str]:
    if isinstance(node, ast.Assign):
        ts = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        ts = [node.target]
    else:
        return []
    return [n.id for t in ts for n in ast.walk(t) if isinstance(n, ast.Name)]


def api(source: str, path: str, public_api) -> dict[str, str]:
    """The public API as `_public_api` sees it, over every top-level binding (inside if, try,
    with and loops too), with constants described by their value, re-exports by their source,
    class-level assignments as members, and a module `__getattr__` as an entry of its own."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"<unparsable>": ""}
    flat = _flat(tree.body)
    out = public_api(ast.unparse(ast.Module(body=flat, type_ignores=[])), path)
    for node in flat:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for t in _targets(node):
                if out.get(t) == "const":
                    out[t] = "const = " + ast.unparse(node.value)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                name = a.asname or a.name
                if out.get(name) == "re-export":
                    out[name] = f"re-export {'.' * node.level}{node.module or ''}:{a.name}"
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for m in _flat(node.body):
                for t in _targets(m):
                    if not t.startswith("_") and f"{node.name}.{t}" not in out:
                        out[f"{node.name}.{t}"] = "attr = " + ast.unparse(m.value) if m.value else "attr"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
            out["__getattr__"] = "module __getattr__"
    return out


def bound(source: str) -> set[str]:
    """Every name bound at module level (imports included) and `Class.name` bound in a class
    body: a name the plan removes must not survive as a shim in any of these forms."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out = set()
    for node in _flat(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            if isinstance(node, ast.ClassDef):
                for m in _flat(node.body):
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out.add(f"{node.name}.{m.name}")
                    out |= {f"{node.name}.{t}" for t in _targets(m)}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in node.names}
        else:
            out |= set(_targets(node))
    return out


# -- the exact public-API rule -----------------------------------------------------------------

def public_api_problems(plan: dict, py, tb: dict, th: dict, public_api) -> tuple[list[str], dict]:
    """Exact interface rule over the touched in-scope (base path, head path) pairs; returns
    (problems, record) where record lists each planned change with its old and new API text."""
    api_b = {bp: api(tb[bp], bp, public_api) for bp, _ in py if bp in tb}
    api_h = {hp: api(th[hp], hp, public_api) for _, hp in py if hp in th}
    bound_h = {hp: bound(th[hp]) for hp in api_h}
    record: dict = {"changed": [], "renamed": [], "removed": [], "added": []}

    def entries(key):
        out: dict[str, set] = {}
        for p, n in map(_split, plan[key]):
            if n is not None:
                out.setdefault(p, set()).add(n)
        return out
    removed, changed, added = entries("removed"), entries("changed"), entries("added")
    removed_mod = {p for p, n in map(_split, plan["removed"]) if n is None}
    ren_mod = {o: n for o, n in plan["renamed"].items() if ":" not in o}
    ren = [(*_split(o), *_split(n)) for o, n in plan["renamed"].items() if ":" in o]

    # actual differences: a module that left the tree (deleted or moved away) loses every name,
    # a module that arrived gains every name
    gone: dict[str, dict[str, str]] = {}
    new: dict[str, dict[str, str]] = {}
    diffs: dict[str, dict[str, tuple[str, str]]] = {}
    for bp, hp in py:
        ab, ah = api_b.get(bp, {}), api_h.get(hp, {})
        if bp and bp != hp:
            gone.setdefault(bp, {}).update(ab)
            if hp:
                new.setdefault(hp, {}).update(ah)
            continue
        if not bp:
            new.setdefault(hp, {}).update(ah)
            continue
        for k, s in ab.items():
            if k not in ah:
                gone.setdefault(bp, {})[k] = s
            elif ah[k] != s:
                diffs.setdefault(bp, {})[k] = (s, ah[k])
        for k, s in ah.items():
            if k not in ab:
                new.setdefault(hp, {})[k] = s
    problems = []

    def still_bound(path, name):
        return path in bound_h and name in bound_h[path]

    # module renames: the whole API (including __all__) moves unchanged
    for old, nw in ren_mod.items():
        if old not in tb or old in th:
            problems.append(f"plan renamed {old} = {nw}: {old} was not removed by this step")
            continue
        if nw not in api_h or nw not in new:
            problems.append(f"plan renamed {old} = {nw}: {nw} is not a new module at head")
            continue
        if gone.get(old, {}) != new.get(nw, {}):
            diff = sorted(set(gone.get(old, {}).items()) ^ set(new.get(nw, {}).items()))[:3]
            problems.append(f"plan renamed {old} = {nw}: the API differs ({diff})")
        record["renamed"].append({"old": old, "new": nw})
        gone.pop(old, None)
        new.pop(nw, None)
    for m in sorted(removed_mod):
        if m not in tb or m in th:
            problems.append(f"plan removed {m}: the module was not removed by this step")
        record["removed"].append({"name": m})
        gone.pop(m, None)
    # name renames: old gone (no shim), new present, same API; members follow the top name
    for po, no, pn, nn in ren:
        g, h = gone.get(po, {}), new.get(pn, {})
        if no not in g or still_bound(po, no):
            problems.append(f"plan renamed {po}:{no} = {pn}:{nn}: {no} is still bound in {po}")
            continue
        if nn not in h:
            problems.append(f"plan renamed {po}:{no} = {pn}:{nn}: {nn} is not new in {pn}")
            continue
        if g[no] != h[nn]:
            problems.append(f"plan renamed {po}:{no} = {pn}:{nn}: API differs: {g[no]} -> {h[nn]}")
        record["renamed"].append({"old": f"{po}:{no}", "new": f"{pn}:{nn}", "api": h[nn]})
        for k in [k for k in g if k == no or k.startswith(no + ".")]:
            tail = k[len(no):]
            if tail and h.get(nn + tail) != g[k]:
                problems.append(f"plan renamed {po}:{no} = {pn}:{nn}: member {k} differs or is missing")
            g.pop(k)
            h.pop(nn + tail, None)
        for k in [k for k in h if k.startswith(nn + ".") and k not in added.get(pn, set())]:
            problems.append(f"{pn}: {k} added to renamed {nn} (not in the plan)")
            h.pop(k)
    # listed removals, signature changes and additions must happen in this step
    for p, ns in sorted(removed.items()):
        for n in sorted(ns):
            if n not in gone.get(p, {}) or still_bound(p, n):
                problems.append(f"plan removed {p}:{n}: still bound at head (or the file is not touched)")
            else:
                record["removed"].append({"name": f"{p}:{n}", "api": gone[p][n]})
    for p, ns in sorted(changed.items()):
        for n in sorted(ns):
            if n not in diffs.get(p, {}):
                problems.append(f"plan changed {p}:{n}: its API did not change (or the file is not touched)")
            else:
                old, nw = diffs[p].pop(n)
                record["changed"].append({"name": f"{p}:{n}", "old": old, "new": nw})
    for p, ns in sorted(added.items()):
        for n in sorted(ns):
            if n not in new.get(p, {}):
                problems.append(f"plan added {p}:{n}: not new at head")
            else:
                record["added"].append({"name": f"{p}:{n}", "api": new[p][n]})

    # __all__ may change only by the names listed for that file
    def listed_names(path):
        return ({n.split(".")[0] for n in removed.get(path, ())} | {n.split(".")[0] for n in added.get(path, ())}
                | {no for po, no, _, _ in ren if po == path} | {nn for _, _, pn, nn in ren if pn == path})
    for p in sorted(set(diffs)):
        if "__all__" in diffs[p]:
            b, h = diffs[p].pop("__all__")
            try:
                sb, sh = set(ast.literal_eval(b)), set(ast.literal_eval(h))
            except (ValueError, SyntaxError):
                sb, sh = {b}, {h}
            extra = sorted((sb ^ sh) - listed_names(p))
            if extra:
                problems.append(f"{p}: __all__ changed beyond the plan's names for this file: {extra}")
    for p, g in sorted(gone.items()):
        if "__all__" in g and p in api_h:
            problems.append(f"{p}: __all__ removed (not in the plan)")
            g.pop("__all__")
    for p, h in sorted(new.items()):
        if "__all__" in h and p in api_b:
            problems.append(f"{p}: __all__ added (not in the plan)")
            h.pop("__all__")

    # everything else that changed, disappeared or appeared is unplanned
    problems += [f"{p}: {k}{b} -> {h} (not in the plan's changed)"
                 for p, d in sorted(diffs.items()) for k, (b, h) in sorted(d.items())]
    for p, g in sorted(gone.items()):
        for k in sorted(g):
            if k != "__all__" and not (k in removed.get(p, set()) or k.split(".")[0] in removed.get(p, set())):
                problems.append(f"{p}: {k} removed (not in the plan)")
    for p, h in sorted(new.items()):
        for k in sorted(h):
            if k != "__all__" and not (k in added.get(p, set()) or k.split(".")[0] in added.get(p, set())):
                problems.append(f"{p}: {k} added (not in the plan)")
    return problems, record


# -- callers follow the plan, nothing else -----------------------------------------------------

def _dotted(path: str, roots) -> str | None:
    from .architecture import module_name  # noqa: PLC0415
    m = module_name(path, roots)
    return m[0] if m else None


class _Rename(ast.NodeTransformer):
    """Base caller source with the plan's renames applied: names, attributes, modules."""

    def __init__(self, names: dict[str, str], modules: dict[str, str]):
        self.names, self.modules = names, modules

    def _mod(self, dotted: str | None) -> str | None:
        if dotted is None:
            return None
        for o, n in sorted(self.modules.items(), key=lambda x: -len(x[0])):
            if dotted == o or dotted.startswith(o + "."):
                return n + dotted[len(o):]
        return dotted

    def visit_Name(self, node):
        node.id = self.names.get(node.id, node.id)
        return node

    def visit_Attribute(self, node):
        chain, cur = [], node
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            dotted = ".".join([cur.id, *reversed(chain)])
            moved = self._mod(dotted)
            if moved != dotted:
                return ast.parse(moved, mode="eval").body
        self.generic_visit(node)
        node.attr = self.names.get(node.attr, node.attr)
        return node

    def visit_alias(self, node):
        node.name = self._mod(node.name) if "." in node.name else self.names.get(node.name, node.name)
        if node.asname:
            node.asname = self.names.get(node.asname, node.asname)
        return node

    def visit_ImportFrom(self, node):
        node.module = self._mod(node.module)
        self.generic_visit(node)
        return node


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return any((isinstance(n, ast.Name) and n.id in names) or (isinstance(n, ast.Attribute) and n.attr in names)
               or (isinstance(n, ast.alias) and (n.asname or n.name).split(".")[-1] in names)
               for n in ast.walk(node))


def _imports(tree: ast.Module) -> Counter:
    out: Counter = Counter()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(("import", a.name, a.asname) for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            out.update((f"from {'.' * n.level}{n.module or ''}", a.name, a.asname) for a in n.names)
    return out


def _strip_imports(body: list) -> list:
    out = []
    for s in body:
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            continue
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(s, field, None), list):
                setattr(s, field, _strip_imports(getattr(s, field)))
        for h in getattr(s, "handlers", []):
            h.body = _strip_imports(h.body)
        out.append(s)
    return out


def _header(s: ast.stmt) -> str:
    """dump of a compound statement without its blocks."""
    c = ast.parse(ast.unparse(s)).body[0]
    for field in ("body", "orelse", "finalbody", "handlers", "cases"):
        if isinstance(getattr(c, field, None), list):
            setattr(c, field, [])
    return ast.dump(c)


def _blocks(s: ast.stmt) -> list[list]:
    out = [getattr(s, f) for f in ("body", "orelse", "finalbody") if isinstance(getattr(s, f, None), list)]
    out += [h.body for h in getattr(s, "handlers", [])]
    return out


def _compare_body(b: list, h: list, removed: set[str], changed: set[str], where: str) -> list[str]:
    db, dh = [ast.dump(s) for s in b], [ast.dump(s) for s in h]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=db, b=dh, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        bs, hs = b[i1:i2], h[j1:j2]
        if op == "delete" and all(_mentions(s, removed) for s in bs):
            continue
        if op == "replace" and len(bs) != len(hs):     # deletions of removed names inside a replace
            bs = [s for s in bs if not _mentions(s, removed)]
            op = "delete" if not bs and not hs else op
        if op == "replace" and len(bs) == len(hs) and all(type(x) is type(y) for x, y in zip(bs, hs)):
            for x, y in zip(bs, hs):
                bx, by = _blocks(x), _blocks(y)
                if bx and _header(x) == _header(y) and len(bx) == len(by):
                    for u, v in zip(bx, by):
                        out += _compare_body(u, v, removed, changed, where)
                elif not (changed and _mentions(x, changed) and _mentions(y, changed)):
                    out.append(f"{where}:{y.lineno}: changed beyond the plan ({ast.unparse(y).splitlines()[0][:80]})")
            continue
        line = (hs[0].lineno if hs else bs[0].lineno)
        out.append(f"{where}:{line}: {op} beyond the plan (only deleting statements that name a removed "
                   f"name, or rewriting a statement that names a changed name, is allowed)")
    return out


def caller_problems(plan: dict, callers: list[tuple], tb: dict, th: dict, roots) -> list[str]:
    """A caller file may only follow the plan (see the module docstring)."""
    names = {}                                        # bare old name -> bare new name
    for o, n in plan["renamed"].items():
        if ":" in o:
            bo, bn = _split(o)[1].split(".")[-1], _split(n)[1].split(".")[-1]
            if bo != bn:
                names[bo] = bn
    modules = {}
    for o, n in plan["renamed"].items():
        if ":" not in o:
            mo, mn = _dotted(o, roots), _dotted(n, roots)
            if mo and mn:
                modules[mo] = mn
    from_moves = {}                                   # (old module, name) -> (new module, new name)
    for o, n in plan["renamed"].items():
        if ":" in o:
            (po, no), (pn, nn) = _split(o), _split(n)
            mo, mn = _dotted(po, roots), _dotted(pn, roots)
            if mo and mn and "." not in no:
                from_moves[(mo, no)] = (mn, nn)
    removed = {n.split(".")[-1] for _, n in map(_split, plan["removed"]) if n}
    removed |= {(_dotted(p, roots) or "").split(".")[-1] for p, n in map(_split, plan["removed"]) if n is None} - {""}
    changed = {n.split(".")[-1] for _, n in map(_split, plan["changed"])}
    removed_mods = {_dotted(p, roots) for p, n in map(_split, plan["removed"]) if n is None} - {None}
    out = []
    for st, bp, hp in callers:
        if bp != hp or not bp or not hp:
            out.append(f"{hp or bp}: a caller file may not be added, deleted or renamed ({st})")
            continue
        if bp not in tb or hp not in th:
            continue
        if not bp.endswith(".py"):
            out += _text_problems(bp, tb[bp], th[hp], names, modules, removed, changed)
            continue
        try:
            tbase, thead = ast.parse(tb[bp]), ast.parse(th[hp])
        except SyntaxError:
            out.append(f"{hp}: does not parse at base or head")
            continue
        tbase = _Rename(names, modules).visit(tbase)
        ib, ih = _imports(tbase), _imports(thead)
        moved: Counter = Counter()                    # the base imports as the plan moves them
        for (kind, name, asname), c in ib.items():
            mod = kind[5:] if kind.startswith("from ") else None
            for (mo, no), (mn, nn) in from_moves.items():
                if mod == _Rename({}, modules)._mod(mo) and name in (no, nn):
                    moved[(f"from {mn}", nn, asname)] += c
                    break
            else:
                if (mod in removed_mods or (mod is None and name in removed_mods)
                        or (mod is not None and name in removed)):
                    continue
                moved[(kind, name, asname)] += c
        if moved != ih:
            extra = sorted((ih - moved) + (moved - ih))[:3]
            out.append(f"{hp}: imports differ from the plan's renames: {extra}")
        out += _compare_body(_strip_imports(tbase.body), _strip_imports(thead.body), removed, changed, hp)
    return out


def _text_problems(path, base, head, names, modules, removed, changed) -> list[str]:
    for o, n in names.items():
        base = re.sub(rf"\b{re.escape(o)}\b", n, base)
    for o, n in modules.items():
        base = re.sub(rf"\b{re.escape(o)}\b", n, base)
    out = []
    bl, hl = base.split("\n"), head.split("\n")
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=bl, b=hl, autojunk=False).get_opcodes():
        if op == "equal":
            continue

        def has(lines, ns):
            return all(any(re.search(rf"\b{re.escape(x)}\b", ln) for x in ns) for ln in lines)
        if op == "delete" and removed and has(bl[i1:i2], removed):
            continue
        if op == "replace" and changed and has(bl[i1:i2], changed) and has(hl[j1:j2], changed):
            continue
        out.append(f"{path}:{j1 + 1}: {op} beyond the plan")
    return out
