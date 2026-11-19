"""Interface plans: private signature changes and the caller defs that follow them.

`changed` may name a private function (`path.py:_f` or `path.py:Cls._m`): it must be one def at
base and at head in the same module and its signature (decorators, parameters, return
annotation) must differ. Private names are not in the public API, so they are checked here.

`adapted = ["tests/t.py:test_x.fake"]` names defs in caller files (fakes, spies, wrappers) whose
parameters had to follow a changed signature. Such a def must be linked to a changed name: its
name (or that of a def enclosing it) occurs in one statement with the changed name's token (a
`setattr`, `monkeypatch.setattr`, `mock.patch(..., new=...)` or an assignment), or its body calls
the changed name. Its parameters may change; its body must equal the base body after the inputs
are canonicalised on both sides:
- base: a parameter `k`, `kw["k"]` and `kw.get("k")` (kw the `**` parameter), and `a[i]` (a the
  `*` parameter) for the linked function's i-th base parameter, all read as input `k`;
- head: the same, plus `b.k` and `a[j].k` when b, or the linked function's j-th head parameter,
  is a new parameter (a bundle such as a context object);
- a call that forwards every input of the def unchanged (`f(*a, **kw)`, `f(**kw)`, `f(call)`)
  is the same forwarding call on both sides.
So a fake may read its inputs from a bundle instead of keywords; any other change (a condition, a
return value, another field) is a difference. The gate records old and new text for review.
"""
from __future__ import annotations

import ast
import textwrap
from collections import Counter

from . import config, gitio

IN = "⟦in:{}⟧"
FORWARD = "⟦in*⟧"


def split(entry: str) -> tuple[str, str]:
    p, _, q = entry.partition(":")
    return p, q


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _nested(node, path: list[str]):
    """(child, its def/class path) for the defs and classes nested in node at any statement depth."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPES):
            yield child, [*path, child.name]
        elif isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case)):
            yield from _nested(child, path)


def find_defs(tree: ast.AST, qual: str) -> list:
    """Function defs whose def/class nesting path is `qual` (dotted), at any statement depth."""
    want, out, todo = qual.split("."), [], [(tree, [])]
    while todo:
        node, path = todo.pop()
        for child, here in _nested(node, path):
            if here == want and not isinstance(child, ast.ClassDef):
                out.append(child)
            if want[:len(here)] == here:
                todo.append((child, here))
    return out


def _member(tree, n: str) -> bool:
    """n is `f` or `Cls.m` with Cls a class bound at module level."""
    parts = n.split(".")
    if len(parts) == 1:
        return True
    top = [c for c, _ in _nested(tree, [])]
    return len(parts) == 2 and any(isinstance(c, ast.ClassDef) and c.name == parts[0] for c in top)


def signature(fn) -> str:
    dec = "".join(f"@{ast.unparse(d)} " for d in fn.decorator_list)
    return dec + f"({ast.unparse(fn.args)})" + (f" -> {ast.unparse(fn.returns)}" if fn.returns else "")


def _parse(text):
    if text is None:
        return None
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, TypeError):
        return None


def private_changed(p: str, n: str, tb: dict, th: dict, record: list) -> list[str]:
    """Check a `changed` entry for a private name; append its old and new signature to `record`."""
    head = f"plan changed {p}:{n}:"
    trees = _parse(tb.get(p)), _parse(th.get(p))
    if None in trees:
        return [f"{head} the file is not touched (or does not parse)"]
    found = [find_defs(t, n) for t in trees]
    if not found[0] and any(isinstance(x, ast.ClassDef) and x.name == n for x in trees[0].body):
        return [f"{head} a private class cannot be changed (list its changed methods)"]
    if not all(_member(t, n) for t in trees):
        return [f"{head} must be a module function or a method of a module class (not a nested def)"]
    if len(found[0]) != 1 or len(found[1]) != 1:
        return [f"{head} must be exactly one def at base and at head (found {len(found[0])}, {len(found[1])})"]
    old, new = signature(found[0][0]), signature(found[1][0])
    if old == new:
        return [f"{head} its signature did not change"]
    record.append({"name": f"{p}:{n}", "old": old, "new": new, "api": "private"})
    return []


# -- linked changed function -------------------------------------------------------------------

def _shallow(s: ast.stmt):
    """The statement without its nested blocks (header only)."""
    names = ("body", "orelse", "finalbody", "handlers", "cases")
    return [n for f, v in ast.iter_fields(s) if f not in names
            for x in (v if isinstance(v, list) else [v]) if isinstance(x, ast.AST) for n in ast.walk(x)]


def _tokens(nodes, tok) -> set[str]:
    out = set()
    for n in nodes:
        v = n.id if isinstance(n, ast.Name) else n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None
        m = tok.match(v) if isinstance(v, str) else None
        if m:
            out.add(m.group(1))
    return out


def links(tree: ast.AST, fn, names: set[str], changed: set[str], tok) -> set[str]:
    """Changed dotted names linked to def `fn` (normalised tree; `names`: fn and its enclosing defs)."""
    out = set()
    for s in ast.walk(tree):
        if isinstance(s, ast.stmt) and not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes = _shallow(s)
            if any(isinstance(n, ast.Name) and n.id in names for n in nodes):
                out |= _tokens(nodes, tok) & changed
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            out |= _tokens([n.func], tok) & changed
    return out


def linked_params(entry: str, texts: dict) -> list[str] | None:
    """Positional parameter names of the linked `changed` entry in `texts` (base or head texts)."""
    p, q = split(entry)
    t = _parse(texts.get(p, ""))
    found = find_defs(t, q) if t is not None else []
    if len(found) != 1:
        return None
    a = found[0].args
    return [x.arg for x in [*a.posonlyargs, *a.args]]


# -- canonical inputs ----------------------------------------------------------------------------

def _inputs(fn) -> tuple[list[str], str | None, str | None]:
    a = fn.args
    return [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]], (a.vararg.arg if a.vararg else None), \
        (a.kwarg.arg if a.kwarg else None)


def _is_name(node, name: str | None) -> bool:
    return name is not None and isinstance(node, ast.Name) and node.id == name


def _const(node, kind):
    """The value of a constant of exactly that type, else None."""
    return node.value if isinstance(node, ast.Constant) and type(node.value) is kind else None


class _Canon(ast.NodeTransformer):
    def __init__(self, fn, linked: list[str] | None, bundles: set[str], linked_bundles: set[int]):
        self.params, self.va, self.kw = _inputs(fn)
        a = fn.args
        self.pos, self.kwonly = [x.arg for x in [*a.posonlyargs, *a.args]], [x.arg for x in a.kwonlyargs]
        self.linked, self.bundles, self.linked_bundles = linked or [], bundles, linked_bundles

    @staticmethod
    def _in(k: str) -> ast.Name:
        return ast.Name(id=IN.format(k), ctx=ast.Load())

    def _forwards(self, node: ast.Call) -> bool:
        """Every input passed on once, in order: positional params, then *va; kw-only params as
        k=k and **kw as keywords."""
        pos = [x.value.id if isinstance(x, ast.Starred) and isinstance(x.value, ast.Name) else getattr(x, "id", None)
               for x in node.args]
        kws = [(k.arg, getattr(k.value, "id", None)) for k in node.keywords]
        want_kws = [(k, k) for k in self.kwonly] + ([(None, self.kw)] if self.kw else [])
        return bool(pos or kws) and pos == self.pos + ([self.va] if self.va else []) \
            and len(kws) == len(set(kws)) and set(kws) == set(want_kws)

    def _kw_get(self, node: ast.Call) -> str | None:
        """k of `kw.get("k")`."""
        f = node.func
        ok = isinstance(f, ast.Attribute) and f.attr == "get" and _is_name(f.value, self.kw)
        return _const(node.args[0], str) if ok and len(node.args) == 1 and not node.keywords else None

    def visit_Call(self, node):
        if self._forwards(node):
            node.func = self.visit(node.func)
            node.args, node.keywords = [ast.Name(id=FORWARD, ctx=ast.Load())], []
            return node
        k = self._kw_get(node)
        if k is not None:
            return self._in(k)
        self.generic_visit(node)
        return node

    def _index(self, node) -> int | None:
        ok = isinstance(node, ast.Subscript) and _is_name(node.value, self.va)
        return _const(node.slice, int) if ok else None

    def visit_Subscript(self, node):
        k = _const(node.slice, str) if _is_name(node.value, self.kw) else None
        if k is not None and isinstance(node.ctx, ast.Load):
            return self._in(k)
        i = self._index(node)
        if i is not None and 0 <= i < len(self.linked) and i not in self.linked_bundles:
            return self._in(self.linked[i])
        self.generic_visit(node)
        return node

    def visit_Attribute(self, node):
        v = node.value
        if isinstance(node.ctx, ast.Load):
            if isinstance(v, ast.Name) and v.id in self.bundles:
                return self._in(node.attr)
            i = self._index(v)
            if i is not None and i in self.linked_bundles:
                return self._in(node.attr)
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.params and node.id not in self.bundles:
            return self._in(node.id)
        return node

    def _shadows(self, node) -> bool:
        """A nested scope that binds an input name: left raw (its reads are not the inputs)."""
        names = {*self.params, *self.bundles, *([self.va] if self.va else []), *([self.kw] if self.kw else [])}
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            bound = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg] if x}
        else:
            bound = {n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)}
        return bool(bound & names)

    def _scope(self, node):
        if not self._shadows(node):
            self.generic_visit(node)
        return node

    visit_Lambda = visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _scope

    def visit_FunctionDef(self, node):
        if getattr(self, "_top", None) is None:
            self._top = node
            node.body = [self.visit(s) for s in node.body]
            node.args = ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[],
                                      kwarg=None, defaults=[])
            return node
        return self._scope(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def canonicalise(fb, fh, lb: list[str] | None, lh: list[str] | None) -> None:
    """Rewrite base def fb and head def fh in place to canonical inputs (see the module doc)."""
    pb, _, _ = _inputs(fb)
    ph, _, _ = _inputs(fh)
    old = set(lb or [])
    linked_bundles_h = {j for j, q in enumerate(lh or []) if q not in old}
    new_names = {(lh or [])[j] for j in linked_bundles_h}
    pos_h = [x.arg for x in [*fh.args.posonlyargs, *fh.args.args]]
    bundles_h = {q for j, q in enumerate(pos_h) if q not in pb and j in linked_bundles_h}
    bundles_h |= {q for q in ph if q not in pb and q in new_names}   # a new param of the linked function
    _Canon(fb, lb, set(), set()).visit(fb)
    _Canon(fh, lh, bundles_h, linked_bundles_h).visit(fh)


def private_entries(private: list, tb: dict, th: dict, record: list) -> list[str]:
    return [x for p, n in private for x in private_changed(p, n, tb, th, record)]


def unseen(plan: dict, seen: set) -> list[str]:
    return [f"plan adapted {e}: the file is not a touched caller" for e in plan.get("adapted", []) if e not in seen]


def plan_problems(data: dict, entry_ok) -> list[str]:
    """Plan-file checks of the `adapted` key."""
    ad, bad = data.get("adapted", []), []
    for e in ad:
        if not entry_ok(e) or ":" not in e:
            bad.append(f"adapted entry {e!r} must be path/to/file.py:qual.name (a def in a caller file)")
        elif not any(config.match(split(e)[0], g) for g in data.get("callers", [])):
            bad.append(f"adapted entry {e!r} is not in a file the plan's callers cover")
    if ad and not data.get("changed"):
        bad.append("adapted needs at least one changed entry (the signature its defs follow)")
    return bad + [f"adapted entry {e!r} is listed twice" for e in sorted({e for e in ad if ad.count(e) > 1})]


def raw_defs(plan: dict, path: str, base, head) -> dict[str, tuple[list, list]]:
    """Source text of each `adapted` def of caller `path`; base and head are (text, tree)."""
    return {e: tuple([ast.get_source_segment(t, f) for f in find_defs(tr, split(e)[1])] for t, tr in (base, head))
            for e in plan.get("adapted", []) if split(e)[0] == path}


def module_texts(repo, base: str, plan: dict) -> tuple[dict, dict]:
    """(base, head) texts of the modules the plan's `changed` names, when it has `adapted` defs."""
    paths = sorted({split(e)[0] for e in plan["changed"]}) if plan.get("adapted") else []
    return gitio.read_files(repo, base, paths), gitio.read_files(repo, None, paths)


def literals(plan: dict, texts: dict, count) -> Counter:
    """String literals inside the `adapted` defs in `texts` (left to the callers check)."""
    out: Counter = Counter()
    for e in plan.get("adapted", []):
        p, q = split(e)
        t = _parse(texts.get(p))
        for fn in (find_defs(t, q) if t is not None else []):
            out += count(textwrap.dedent(ast.get_source_segment(texts[p], fn) or ""))
    return out
