"""LCOM4 per class (informational; never feeds debt or health)."""
from __future__ import annotations

import ast

FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
SKIP = {"__init__", "__post_init__", "__new__"}


def _self_attrs(method: ast.AST) -> set[str]:
    a = method.args
    first = (a.posonlyargs + a.args)[:1]
    owner = first[0].arg if first else "self"
    return {n.attr for n in ast.walk(method)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == owner}


def lcom4(cls: ast.ClassDef) -> tuple[int, int | None]:
    """(method count, LCOM4 or None when fewer than 3 methods)."""
    methods = [n for n in cls.body if isinstance(n, FUNC) and n.name not in SKIP]
    if len(methods) < 3:
        return len(methods), None
    attrs = [_self_attrs(m) for m in methods]
    parent = list(range(len(methods)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first_user: dict[str, int] = {}
    for i, used in enumerate(attrs):
        for attr in used:
            j = first_user.setdefault(attr, i)
            parent[find(i)] = find(j)
    by_name = {m.name: i for i, m in enumerate(methods)}
    for i, used in enumerate(attrs):
        for attr in used:
            if attr in by_name:
                parent[find(i)] = find(by_name[attr])
    return len(methods), len({find(i) for i in range(len(methods))})


def classes(tree: ast.Module) -> list[dict]:
    out: list[dict] = []

    def walk(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):
                n, l4 = lcom4(node)
                out.append({"name": prefix + node.name, "line": node.lineno,
                            "end_line": node.end_lineno, "methods": n, "lcom4": l4})
                walk(node.body, prefix + node.name + ".")
    walk(tree.body, "")
    return out
