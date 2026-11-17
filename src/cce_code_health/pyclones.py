"""Short-block duplication within one file (python@1, docs/py1/design.md section 4).

Over the statement lists inside functions (docstring-like constant expressions dropped), every
window of `window` consecutive statements with at least `min_nodes` AST nodes and at least one
compound statement gets a structural key: names, attributes, constants, f-strings and loaded
subscripts become one atom; node types and keyword names are kept. Equal keys at two or more
non-overlapping places form a clone group. A function's `clones` is the number of its statements
(nested ones included) inside any occurrence: statements, not lines, so that formatting does not
move it. Pure AST, like cohesion.py.
"""
from __future__ import annotations

import ast
import hashlib
from collections import defaultdict

FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
ATOM = (ast.Name, ast.Attribute, ast.Constant, ast.JoinedStr, ast.Subscript)
COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)
if hasattr(ast, "TryStar"):
    COMPOUND = COMPOUND + (ast.TryStar,)
_SKIP_FIELDS = ("ctx", "type_comment", "lineno", "col_offset")


def _key(node, memo: dict) -> str:
    """Structural key with atoms abstracted (a Subscript store keeps its structure)."""
    if isinstance(node, ATOM) and not (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)):
        return "*"
    if not isinstance(node, ast.AST):
        return str(node)
    got = memo.get(id(node))
    if got is not None:
        return got
    parts = [type(node).__name__]
    for f, v in ast.iter_fields(node):
        if f in _SKIP_FIELDS:
            continue
        if isinstance(v, list):
            parts.append(f + "[" + ",".join(_key(x, memo) for x in v) + "]")
        elif isinstance(v, ast.AST):
            parts.append(f + "=" + _key(v, memo))
        elif f in ("arg", "attr") or isinstance(v, str):
            parts.append(f"{f}:{v}" if f == "arg" else "")
        elif v is not None:
            parts.append(str(v))
    out = "(" + " ".join(parts) + ")"
    memo[id(node)] = out
    return out


def _outer_functions(tree: ast.AST):
    """Functions not nested in another function (classes are looked into)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, FUNC):
            yield node
        elif not isinstance(node, ast.Lambda):
            yield from _outer_functions(node)


def _blocks(tree: ast.Module):
    """Every statement list inside a function, the function bodies included (statement lists
    occur only in statements, handlers and match cases, so expressions are not visited)."""
    def lists(s):
        for f in ("body", "orelse", "finalbody"):
            b = getattr(s, f, None)
            if isinstance(b, list) and b and isinstance(b[0], ast.stmt):
                yield b
        for h in getattr(s, "handlers", None) or []:
            yield h.body
        for c in getattr(s, "cases", None) or []:
            yield c.body

    def walk(s):
        for b in lists(s):
            yield b
            for x in b:
                yield from walk(x)
    for fn in _outer_functions(tree):
        yield from walk(fn)


def _size(s: ast.stmt, sizes: dict) -> int:
    """AST nodes in s (all of them, as ast.walk counts), memoised by id."""
    n = sizes.get(id(s))
    if n is None:
        n = sizes[id(s)] = sum(1 for _ in ast.walk(s))
    return n


def _is_doc(s: ast.stmt) -> bool:
    return isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)


def clone_groups(tree: ast.Module, window: int = 3, min_nodes: int = 30) -> list[list[list]]:
    """Clone groups: each a list of >= 2 non-overlapping occurrences (statement lists), in
    source order."""
    memo: dict = {}
    sizes: dict = {}
    seen: dict = defaultdict(dict)        # key hash -> {(first line, last line): statements}
    for b in _blocks(tree):
        b = [s for s in b if not _is_doc(s)]
        for i in range(len(b) - window + 1):
            w = b[i:i + window]
            if not any(isinstance(x, COMPOUND) for x in w):
                continue
            if sum(_size(s, sizes) for s in w) < min_nodes:
                continue
            h = hashlib.blake2b("|".join(_key(s, memo) for s in w).encode(), digest_size=10).digest()
            seen[h].setdefault((w[0].lineno, w[-1].end_lineno), w)
    groups = []
    for locs in seen.values():
        keep = []
        for (a, e) in sorted(locs):
            if all(e < a2 or a > e2 for a2, e2 in keep):
                keep.append((a, e))
        if len(keep) > 1:
            groups.append([locs[loc] for loc in keep])
    groups.sort(key=lambda g: (g[0][0].lineno, g[0][-1].end_lineno))
    return groups


def marked_statements(tree: ast.Module, window: int = 3, min_nodes: int = 30) -> set[int]:
    """ids of the statements (nested ones included) inside any clone occurrence."""
    marked: set[int] = set()
    for g in clone_groups(tree, window, min_nodes):
        for occ in g:
            for s in occ:
                marked.update(id(x) for x in ast.walk(s) if isinstance(x, ast.stmt))
    return marked


def count_in(node: ast.AST, marked: set[int]) -> int:
    """Statements of node (nested ones included) that are marked."""
    return sum(1 for x in ast.walk(node) if isinstance(x, ast.stmt) and id(x) in marked) if marked else 0


def clone_statements(tree: ast.Module, window: int = 3, min_nodes: int = 30) -> dict[str, int]:
    """{unit qualname: statements of the unit inside any clone occurrence}, units with clones only
    (python_backend.units; two definitions of one name add up)."""
    from .python_backend import units  # noqa: PLC0415 (python_backend imports this module)
    marked = marked_statements(tree, window, min_nodes)
    out: dict[str, int] = {}
    for name, node in units(tree):
        n = count_in(node, marked)
        if n:
            out[name] = out.get(name, 0) + n
    return out
