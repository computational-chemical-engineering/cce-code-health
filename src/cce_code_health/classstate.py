"""Class state (python@1, docs/py1/design.md section 3): methods that communicate through fields.

Per class C (nested classes on their own; the methods are the defs directly in C's body):
constructors K are the set-up methods in CTOR plus constructor helpers (private, non-dunder
methods whose every `self.m()` call site in C lies in K, fixpoint); excluded methods X are
staticmethod, classmethod, `*.setter` and `*.deleter`. Stores to self write a key (`self.a` -> a,
`self.a.b` -> a.b, `self.a["k"]` -> a[k] and a, `self.a[i]` -> a, `setattr(self, "k", v)` -> k).
W(a) and R(a) are the methods outside K and X that write and load a. a is a channel when W(a) is
not empty, a is not a method name, and some method reads a without writing it or two methods
write it. A method's state_writes is the number of channels it writes (0 for K and X); the
class's is the sum over channels of |W(a)|. Pure AST, like cohesion.py.
"""
from __future__ import annotations

import ast

FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
CTOR = frozenset({"__init__", "__post_init__", "__new__", "__init_subclass__", "__setstate__",
                  "__enter__", "setUp", "setUpClass", "setup_method", "setup_class"})
EXCLUDED_DECORATORS = ("staticmethod", "classmethod", "setter", "deleter")


def _self_name(m: ast.AST) -> str | None:
    a = m.args.posonlyargs + m.args.args
    return a[0].arg if a else None


def _key(node: ast.AST, me: str) -> str | None:
    """'a' for self.a, 'a.b' for self.a.b, 'a[k]' for self.a["k"] (literal str key), else None."""
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == me:
            return node.attr
        base = _key(node.value, me)
        return f"{base}.{node.attr}" if base and "[" not in base else None
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        base = _key(node.value, me)
        return f"{base}[{node.slice.value}]" if base and "[" not in base else None
    return None


def _flat(targets) -> list:
    out = []
    for t in targets:
        if isinstance(t, (ast.Tuple, ast.List)):
            out += _flat(t.elts)
        elif isinstance(t, ast.Starred):
            out += _flat([t.value])
        else:
            out.append(t)
    return out


def _store_targets(n: ast.AST) -> list:
    if isinstance(n, ast.Assign):
        return n.targets
    if isinstance(n, ast.AugAssign) or (isinstance(n, ast.AnnAssign) and n.value is not None):
        return [n.target]
    if isinstance(n, ast.Delete):
        return n.targets
    if isinstance(n, (ast.For, ast.AsyncFor)):
        return [n.target]
    if isinstance(n, (ast.With, ast.AsyncWith)):
        return [i.optional_vars for i in n.items if i.optional_vars is not None]
    return []


def writes_reads(m: ast.AST, me: str) -> tuple[set[str], set[str]]:
    """(keys m writes, keys m loads) through its first parameter `me`."""
    w: set[str] = set()
    r: set[str] = set()
    for n in ast.walk(m):
        for t in _flat(_store_targets(n)):
            k = _key(t, me)
            if k:
                w.add(k)
                if isinstance(t, ast.Subscript):          # self.d["k"] = v also mutates d
                    w.add(k.rsplit("[", 1)[0])
            elif isinstance(t, ast.Subscript):            # self.d[i] = v: a write to d
                k2 = _key(t.value, me)
                if k2:
                    w.add(k2)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setattr"
                and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) and n.args[0].id == me
                and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)):
            w.add(n.args[1].value)
        if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, ast.Load):
            k = _key(n, me)
            if k:
                r.add(k)
    return w, r


def _excluded(m: ast.AST) -> bool:
    for d in m.decorator_list:
        s = ast.unparse(d)
        if any(s == x or s.endswith("." + x) for x in EXCLUDED_DECORATORS):
            return True
    return False


def _methods(cls: ast.ClassDef) -> list:
    return [n for n in cls.body if isinstance(n, FUNC)]


def constructors(cls: ast.ClassDef) -> set[str]:
    """Names in K: the set-up methods present plus constructor helpers (fixpoint)."""
    methods = _methods(cls)
    callers: dict[str, set[str]] = {m.name: set() for m in methods}
    for m in methods:
        me = _self_name(m)
        for n in ast.walk(m):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == me
                    and n.func.attr in callers):
                callers[n.func.attr].add(m.name)
    ctor = set(CTOR) & set(callers)
    changed = True
    while changed:
        changed = False
        for name, cs in callers.items():
            if (name not in ctor and name.startswith("_") and not name.endswith("__")
                    and cs and cs <= ctor):
                ctor.add(name)
                changed = True
    return ctor


def _counted(cls: ast.ClassDef) -> list:
    """Methods outside K and X, with a first parameter."""
    methods = _methods(cls)
    if not methods:
        return []
    ctor = constructors(cls)
    return [m for m in methods
            if m.name not in ctor and not _excluded(m) and _self_name(m)]


def channels(cls: ast.ClassDef, counted: list | None = None) -> dict[str, list[str]]:
    """{channel key: sorted names of the methods that write it}."""
    names = {m.name for m in _methods(cls)}
    W: dict[str, set[str]] = {}
    R: dict[str, set[str]] = {}
    for m in _counted(cls) if counted is None else counted:
        w, r = writes_reads(m, _self_name(m))
        for a in w:
            if a not in names:
                W.setdefault(a, set()).add(m.name)
        for a in r:
            R.setdefault(a, set()).add(m.name)
    return {a: sorted(ws) for a, ws in sorted(W.items())
            if (R.get(a, set()) - ws) or len(ws) >= 2}


def method_state_writes(cls: ast.ClassDef, chan: dict | None = None,
                        counted: list | None = None) -> dict:
    """{method node: number of channels it writes} for every def directly in cls (0 for K and X)."""
    counted = _counted(cls) if counted is None else counted
    chan = channels(cls, counted) if chan is None else chan
    ids = {id(m) for m in counted}
    return {m: (sum(1 for ws in chan.values() if m.name in ws) if id(m) in ids else 0)
            for m in _methods(cls)}


def class_state_writes(chan: dict) -> int:
    return sum(len(ws) for ws in chan.values())


def classes(tree: ast.Module) -> list[tuple[str, dict, dict]]:
    """[(qualname, channels, {method node: state_writes})] in the order of cohesion.classes."""
    out = []

    def walk(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):
                counted = _counted(node)
                chan = channels(node, counted)
                out.append((prefix + node.name, chan, method_state_writes(node, chan, counted)))
                walk(node.body, prefix + node.name + ".")
    walk(tree.body, "")
    return out
