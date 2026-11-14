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
import copy
import difflib
import re
import tomllib
from collections import Counter
from pathlib import Path

from . import config, gitio

PLAN_DIR = ".codehealth/interface/"
PLAN_KEYS = {"id", "approved_by", "reason", "removed", "renamed", "retargeted", "changed", "added", "new_files",
             "callers"}


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
    ret = data.get("retargeted", {})
    if not isinstance(ret, dict) or not all(isinstance(v, str) and v for v in ret.values()):
        bad.append("retargeted must be a table of \"old.py:Name\" = \"home.py:Name\" strings")
    if bad:
        raise ValueError(f"plan {path}: " + "; ".join(bad))
    removed, changed, added = data.get("removed", []), data.get("changed", []), data.get("added", [])
    entries = [*removed, *changed, *added, *ren, *ren.values(), *ret, *ret.values()]
    bad += [f"entry {e!r} must be path/to/file.py or path/to/file.py:Name" for e in entries if not _entry_ok(e)]
    bad += [f"{k} entry {e!r} must name a public name (path:Name)"
            for k, v in (("added", added), ("changed", changed)) for e in v if ":" not in e]
    for old, new in ren.items():
        if (":" in old) != (":" in new):
            bad.append(f"renamed {old!r} = {new!r}: both path:Name, or both module paths")
    for old, new in ret.items():
        if ":" not in old or ":" not in new or _split(old)[0] == _split(new)[0]:
            bad.append(f"retargeted {old!r} = {new!r}: both path:Name, in two different modules")
    bad += [f"entry {e!r} is out of scope" for e in entries if _entry_ok(e) and not cfg.in_scope(_split(e)[0])]
    gone_mods = {e for e in removed if ":" not in e} | {o for o in ren if ":" not in o}
    bad += [f"entry {e!r} names a module the plan removes or renames: list the module entry only, "
            f"or name the new path" for e in entries if ":" in e and _split(e)[0] in gone_mods]
    names = [e for e in [*removed, *changed, *added, *ren, *ret] if ":" in e]
    bad += [f"entry {e!r} is listed twice" for e in sorted({e for e in names if names.count(e) > 1})]
    bad += [f"new_files entry {g!r} is out of scope" for g in data.get("new_files", []) if not cfg.in_scope(g)]
    for g in data.get("callers", []):
        if g.strip("*/") == "" or config.match(".codehealth/x.toml", g):
            bad.append(f"callers entry {g!r} is too broad (it would cover .codehealth/ or everything)")
    if not (removed or ren or ret or changed or added):
        bad.append("the plan lists no change (removed, renamed, changed or added)")
    if bad:
        raise ValueError(f"plan {path}: " + "; ".join(bad))
    blob = gitio.git(repo, "rev-parse", f"{base}:{path}").decode().strip()
    return {"id": data["id"], "approved_by": data["approved_by"], "reason": data["reason"],
            "removed": list(removed), "renamed": dict(ren), "retargeted": dict(ret), "changed": list(changed),
            "added": list(added),
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
    paths = {_split(e)[0] for e in [*plan["removed"], *plan["changed"], *plan["added"], *plan["renamed"],
                                     *plan["renamed"].values(), *plan.get("retargeted", {})]}
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

def public_api_problems(plan: dict, py, tb: dict, th: dict, public_api,
                        homes: dict | None = None) -> tuple[list[str], dict]:
    """Exact interface rule over the touched in-scope (base path, head path) pairs; returns
    (problems, record) where record lists each planned change with its old and new API text.
    `homes`: {path: (base text, head text)} of each `retargeted` home module."""
    api_b = {bp: api(tb[bp], bp, public_api) for bp, _ in py if bp in tb}
    api_h = {hp: api(th[hp], hp, public_api) for _, hp in py if hp in th}
    bound_h = {hp: bound(th[hp]) for hp in api_h}
    record: dict = {"changed": [], "renamed": [], "retargeted": [], "removed": [], "added": []}

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
    # retargets: the name leaves the old module (no shim); its home already had it, unchanged
    for o, n in plan.get("retargeted", {}).items():
        (po, no), (ph, nh) = _split(o), _split(n)
        g = gone.get(po, {})
        hb, hh = (homes or {}).get(ph, (None, None))
        ab = api(hb, ph, public_api) if hb is not None else {}
        ah = api(hh, ph, public_api) if hh is not None else {}
        if no not in g or still_bound(po, no):
            problems.append(f"plan retargeted {o} = {n}: {no} is still bound in {po}")
            continue
        if nh not in ab or ab.get(nh) != ah.get(nh):
            problems.append(f"plan retargeted {o} = {n}: {nh} must exist in {ph} at base and stay unchanged")
            continue
        for k in [k for k in g if k == no or k.startswith(no + ".")]:
            if ab.get(nh + k[len(no):]) != g[k]:
                problems.append(f"plan retargeted {o} = {n}: {k} differs from {ph}:{nh + k[len(no):]}")
            g.pop(k)
        record["retargeted"].append({"old": o, "home": n, "api": ab[nh]})
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
#
# Each caller file is normalised on both sides: every name and attribute chain that a file's own
# imports bind (per scope: module, class, function) becomes one token naming the full dotted
# target, imports themselves drop out, and `setattr(obj, "name")`-style patch targets and dotted
# strings become such tokens too. The plan's renames are applied to the base tokens. Head must
# then equal base statement by statement, except that a statement using a removed name may be
# deleted and the arguments of a call to a changed name may differ.

_TOK = re.compile(r"^⟨(.+)⟩$")          # ⟨dotted.target⟩
_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")


def _dotted(path: str, roots) -> str | None:
    from .architecture import module_name  # noqa: PLC0415
    m = module_name(path, roots)
    return m[0] if m else None


def _targets_map(plan: dict, roots) -> tuple[dict, set, set, set, set, set]:
    """(renames old dotted -> new dotted, removed dotted, changed dotted, removed member bare
    names, changed member bare names, top-level packages) from the plan."""
    def full(e):
        p, n = _split(e)
        d = _dotted(p, roots)
        return None if d is None else (d if n is None else f"{d}.{n}")
    renames = {}
    for o, n in [*plan["renamed"].items(), *plan.get("retargeted", {}).items()]:
        fo, fn = full(o), full(n)
        if fo and fn:
            renames[fo] = fn
    removed = {full(e) for e in plan["removed"]} - {None}
    changed = {full(e) for e in plan["changed"]} - {None}
    rem_members = {_split(e)[1].split(".")[-1] for e in plan["removed"] if ":" in e and "." in _split(e)[1]}
    chg_members = {_split(e)[1].split(".")[-1] for e in plan["changed"] if "." in _split(e)[1]}
    tops = {d.split(".")[0] for d in [*renames, *renames.values(), *removed, *changed]}
    return renames, removed, changed, rem_members, chg_members, tops


def _ren(d: str, renames: dict) -> str:
    for o in sorted(renames, key=len, reverse=True):
        if d == o or d.startswith(o + "."):
            return renames[o] + d[len(o):]
    return d


def _scope_bindings(body: list) -> dict[str, str]:
    """Import bindings of one scope (not descending into nested defs and classes)."""
    out = {}
    for node in _flat(body):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    out[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name
    return out


class _Normalise(ast.NodeTransformer):
    """Replace import-bound names by ⟨target⟩ tokens (renamed with `renames`), drop imports."""

    def __init__(self, renames: dict, tops: set):
        self.renames, self.tops, self.scopes = renames, tops, []

    def _tok(self, d: str) -> ast.Name:
        return ast.Name(id=f"⟨{_ren(d, self.renames)}⟩", ctx=ast.Load())

    def _lookup(self, name: str) -> str | None:
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None

    def _resolve(self, node) -> str | None:
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            d = self._resolve(node.value)
            return f"{d}.{node.attr}" if d else None
        return None

    def _scoped(self, node, body):
        self.scopes.append(_scope_bindings(body))
        self.generic_visit(node)
        self.scopes.pop()
        return node

    def visit_Module(self, node):
        return self._scoped(node, node.body)

    def visit_FunctionDef(self, node):
        return self._scoped(node, node.body)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Import(self, node):
        return None

    visit_ImportFrom = visit_Import

    def visit_Name(self, node):
        d = self._lookup(node.id)
        return self._tok(d) if d else node

    def visit_Attribute(self, node):
        d = self._resolve(node)
        if d:
            return self._tok(d)
        self.generic_visit(node)
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str) and _DOTTED.match(node.value) and node.value.split(".")[0] in self.tops:
            return ast.Constant(value=f"⟨{_ren(node.value, self.renames)}⟩")
        return node

    def visit_Call(self, node):
        f = node.func
        is_setattr = ((isinstance(f, ast.Name) and f.id == "setattr")
                      or (isinstance(f, ast.Attribute) and f.attr in ("setattr", "delattr", "object")))
        if is_setattr and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str):
            d = self._resolve(node.args[0])
            if d:                                      # (module or class, "name") -> one target token
                tok = ast.Constant(value=f"⟨{_ren(d + '.' + node.args[1].value, self.renames)}⟩")
                node.args = [tok] + node.args[2:]
        self.generic_visit(node)
        return node


def _token_targets(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        v = n.id if isinstance(n, ast.Name) else n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None
        m = _TOK.match(v) if isinstance(v, str) else None
        if m:
            out.add(m.group(1))
    return out


def _hits(targets: set[str], wanted: set[str]) -> bool:
    return any(t == w or t.startswith(w + ".") for t in targets for w in wanted)


def _uses_removed(node, removed: set[str], members: set[str]) -> bool:
    return _hits(_token_targets(node), removed) or any(
        isinstance(n, ast.Attribute) and n.attr in members for n in ast.walk(node))


class _ClearChanged(ast.NodeTransformer):
    """Clear the arguments of calls to a changed name (they may differ between base and head)."""

    def __init__(self, changed: set[str], members: set[str]):
        self.changed, self.members, self.count = changed, members, 0

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        m = _TOK.match(f.id) if isinstance(f, ast.Name) else None
        if (m and m.group(1) in self.changed) or (isinstance(f, ast.Attribute) and f.attr in self.members):
            node.args, node.keywords = [], []
            self.count += 1
        return node


_BLOCKS = ("body", "orelse", "finalbody")


def _blocks(s: ast.stmt) -> list[list]:
    out = [getattr(s, f) for f in _BLOCKS if isinstance(getattr(s, f, None), list)]
    out += [h.body for h in getattr(s, "handlers", [])]
    out += [c.body for c in getattr(s, "cases", [])]
    return out


def _header(s: ast.stmt) -> str:
    """dump of a statement without its blocks (decorators, bases, test, handler types kept)."""
    c = copy.deepcopy(s)
    for f in _BLOCKS:
        if isinstance(getattr(c, f, None), list):
            setattr(c, f, [])
    if hasattr(c, "handlers"):
        c.handlers = [ast.ExceptHandler(type=h.type, name=h.name, body=[]) for h in c.handlers]
    if hasattr(c, "cases"):
        c.cases = [ast.match_case(pattern=k.pattern, guard=k.guard, body=[]) for k in c.cases]
    return ast.dump(c)


def _leaves(s: ast.stmt) -> list:
    blocks = _blocks(s)
    if not blocks:
        return [s]
    return [x for b in blocks for y in b for x in _leaves(y)]


def _trivial(s: ast.stmt) -> bool:
    return (isinstance(s, (ast.Pass, ast.Assign, ast.AnnAssign, ast.AugAssign))
            or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)))


def _deletable(s: ast.stmt, removed: set[str], members: set[str]) -> bool:
    """A statement using a removed name; a compound one only when every leaf that is not an
    assignment, `pass` or docstring uses a removed name (no unrelated checks go with it)."""
    if not _blocks(s):
        return _uses_removed(s, removed, members)
    leaves = [x for x in _leaves(s) if not _trivial(x)]
    return bool(leaves) and all(_uses_removed(x, removed, members) for x in leaves)


def _first_line(s: ast.stmt) -> str:
    return re.sub("⟨|⟩", "", ast.unparse(s).splitlines()[0])[:80]


def _compare(b: list, h: list, removed, members, where: str, deleted: list) -> list[str]:
    db, dh = [ast.dump(x) for x in b], [ast.dump(x) for x in h]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=db, b=dh, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        bs, hs = b[i1:i2], h[j1:j2]
        keep = [x for x in bs if not _deletable(x, removed, members)]
        gone = [x for x in bs if x not in keep]
        if len(keep) == len(hs) and all(_blocks(x) and _header(x) == _header(y) and len(_blocks(x)) == len(_blocks(y))
                                        for x, y in zip(keep, hs)):
            deleted += [f"{where}:{x.lineno} {_first_line(x)}" for x in gone]
            for x, y in zip(keep, hs):
                for u, v in zip(_blocks(x), _blocks(y)):
                    out += _compare(u, v, removed, members, where, deleted)
            continue
        line = hs[0].lineno if hs else bs[0].lineno
        what = _first_line(hs[0]) if hs else _first_line(bs[0])
        out.append(f"{where}:{line}: {op} beyond the plan ({what})")
    return out


def caller_problems(plan: dict, callers: list[tuple], tb: dict, th: dict, roots) -> list[str]:
    """A caller file may only follow the plan (see above); returns the problems. Use
    `caller_record` for what was deleted and how many calls changed."""
    return _callers(plan, callers, tb, th, roots)[0]


def caller_record(plan: dict, callers: list[tuple], tb: dict, th: dict, roots) -> tuple[list[str], dict]:
    return _callers(plan, callers, tb, th, roots)


def _callers(plan, callers, tb, th, roots):
    renames, removed, changed, rem_m, chg_m, tops = _targets_map(plan, roots)
    out, deleted, calls = [], [], 0
    for st, bp, hp in callers:
        if bp != hp or not bp or not hp:
            out.append(f"{hp or bp}: a caller file may not be added, deleted or renamed ({st})")
            continue
        if bp not in tb or hp not in th:
            continue
        if "�" in tb[bp] + th[hp]:
            out.append(f"{hp}: binary caller file may not change")
            continue
        if not bp.endswith(".py"):
            out += _text_problems(hp, tb[bp], th[hp], renames, plan, roots)
            continue
        try:
            tbase, thead = ast.parse(tb[bp]), ast.parse(th[hp])
        except SyntaxError:
            out.append(f"{hp}: does not parse at base or head")
            continue
        nb = _Normalise(renames, tops).visit(tbase)
        nh = _Normalise({}, tops).visit(thead)
        cb, ch = _ClearChanged(changed, chg_m), _ClearChanged(changed, chg_m)
        nb, nh = cb.visit(nb), ch.visit(nh)
        calls += ch.count
        out += _compare(nb.body, nh.body, removed, rem_m, hp, deleted)
    return out, {"deleted": deleted, "changed_calls": calls, "files": len(callers)}


def _text_problems(path, base, head, renames, plan, roots) -> list[str]:
    """Non-Python callers (docs, data): only the plan's renames, as dotted names, file paths and
    bare names whose spelling changes; nothing deleted or rewritten."""
    subs = [(o, n) for o, n in renames.items()]
    for o, n in [*plan["renamed"].items(), *plan.get("retargeted", {}).items()]:
        po, no = _split(o)
        pn, nn = _split(n)
        subs.append((po, pn))
        if no and nn and no.split(".")[-1] != nn.split(".")[-1]:
            subs.append((no.split(".")[-1], nn.split(".")[-1]))
    for o, n in sorted(set(subs), key=lambda x: -len(x[0])):
        if o != n:
            base = re.sub(rf"(?<![\w.]){re.escape(o)}(?![\w])", n, base)
    if base == head:
        return []
    bl, hl = base.split("\n"), head.split("\n")
    first = next((i for i, (x, y) in enumerate(zip(bl, hl)) if x != y), min(len(bl), len(hl)))
    return [f"{path}:{first + 1}: changed beyond the plan's renames"]
