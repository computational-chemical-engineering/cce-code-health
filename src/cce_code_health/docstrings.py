"""Docstring rules for public Python names (opt-in: `[docs] docstrings = true`).

D1: a public function, class or method has a docstring.
D2: when a docstring documents parameters (numpydoc `Parameters` section, Google `Args:`,
    Sphinx `:param x:`), the documented names equal the signature's (self/cls dropped, stars
    ignored). A docstring without a parameter section is not checked for D2.

Both are ratchets (`ratchet`): only names that are new, or newly undocumented or drifting,
fail. Public: a module path with no `_private` component (`__init__.py` is fine; tests are
excluded), a top-level def or class without a leading underscore (the names in `__all__` when
it is a literal list; if/try branches included, `if TYPE_CHECKING:` not), and the
non-underscore methods of a public class. Skipped: nested classes, `@overload`, `@override`,
property setters and deleters, dunders, methods an in-module base class defines.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_NUMPY_HEAD = re.compile(r"^(\s*)(Parameters|Other Parameters)\s*$")
_DASHES = re.compile(r"^\s*-{3,}\s*$")
_GOOGLE_HEAD = re.compile(r"^(\s*)(Args|Arguments|Parameters|Keyword Args|Keyword Arguments):\s*$")
_SPHINX = re.compile(r":param\s+(?:[^:]*\s)?\**([A-Za-z_]\w*)\s*:")
_NAMES = re.compile(r"^\**[A-Za-z_]\w*(\s*,\s*\**[A-Za-z_]\w*)*$")


@dataclass(frozen=True)
class Item:
    path: str
    qualname: str
    line: int
    documented: bool
    drift: str | None      # D2: "missing a, b; extra c", or None


def public_module(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if any(p.startswith("_") for p in parts[:-1]):
        return False
    stem = PurePosixPath(path).stem
    return stem == "__init__" or not stem.startswith("_")


def _dec_name(d: ast.expr) -> str:
    if isinstance(d, ast.Call):
        d = d.func
    if isinstance(d, ast.Attribute):
        return d.attr
    return d.id if isinstance(d, ast.Name) else ""


def _skipped(fn: ast.AST) -> bool:
    names = {_dec_name(d) for d in getattr(fn, "decorator_list", [])}
    return bool(names & {"overload", "setter", "deleter"})


def _all_names(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) and node.value else [])
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            if isinstance(node.value, (ast.List, ast.Tuple)) and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.value.elts):
                return {e.value for e in node.value.elts}
            return None
    return None


def _section_names(lines: list[str], i: int, indent: int, google: bool) -> list[str]:
    """Parameter names of the section whose header is line i."""
    out, entry_indent = [], None
    for j in range(i + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        nxt = next((x for x in lines[j + 1:j + 2]), "")
        if not google and _DASHES.match(nxt):
            break                             # the next numpydoc section's title
        ind = len(ln) - len(ln.lstrip())
        if google:
            if ind <= indent:
                break
            if entry_indent is None:
                entry_indent = ind
            if ind == entry_indent:
                head = ln.strip().split(":", 1)[0].split("(", 1)[0].strip()
                if _NAMES.match(head):
                    out += [n.strip().lstrip("*") for n in head.split(",")]
        else:
            if ind < indent or _NUMPY_HEAD.match(ln) or _DASHES.match(ln):
                break
            if ind == indent:
                head = ln.strip().split(":", 1)[0].strip()
                if not _NAMES.match(head):
                    break
                out +=[n.strip().lstrip("*") for n in head.split(",")]
    return out


def documented_params(doc: str) -> list[str] | None:
    """Parameter names a docstring documents, or None when it has no parameter section."""
    lines = doc.expandtabs().splitlines()
    found, names = False, []
    for i, ln in enumerate(lines):
        m = _NUMPY_HEAD.match(ln)
        if m and i + 1 < len(lines) and _DASHES.match(lines[i + 1]):
            found = True
            names += _section_names(lines, i + 1, len(m.group(1)), google=False)
            continue
        m = _GOOGLE_HEAD.match(ln)
        if m:
            found = True
            names += _section_names(lines, i, len(m.group(1)), google=True)
    sphinx = _SPHINX.findall(doc)
    if sphinx:
        found = True
        names += sphinx
    return names if found else None


def signature_params(fn: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> list[str]:
    """Parameter names; the first positional parameter of a non-static method is dropped."""
    a = fn.args
    pos = [x.arg for x in a.posonlyargs + a.args]
    static = "staticmethod" in {_dec_name(d) for d in fn.decorator_list}
    if method and not static and pos:
        pos = pos[1:]
    return pos + [x.arg for x in a.kwonlyargs]


def _drift(node: ast.AST, method: bool) -> str | None:
    doc = ast.get_docstring(node)
    if doc is None:
        return None
    documented = documented_params(doc)
    if documented is None:
        return None
    fn = node
    if isinstance(node, ast.ClassDef):        # only an explicit __init__ (no dataclass, attrs, bases)
        fn = next((n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "__init__"), None)
        if fn is None:
            return None
        method = True
    sig = signature_params(fn, method)
    star = {x.arg for x in (fn.args.vararg, fn.args.kwarg) if x is not None}
    missing = [n for n in sig if n not in documented]
    extra = sorted(set(documented) - set(sig) - star)
    if fn.args.kwarg is not None:
        extra = []                            # **kwargs: extra documented names are allowed
    if not missing and not extra:
        return None
    parts = ([f"missing {', '.join(missing)}"] if missing else []) + ([f"extra {', '.join(extra)}"] if extra else [])
    return "; ".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _flat(body: list) -> list:
    """Top-level statements, with if/else and try/except branches inlined (not TYPE_CHECKING)."""
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
        else:
            out.append(node)
    return out


def _base_methods(cls: ast.ClassDef, classes: dict) -> set[str]:
    """Method names defined by in-module base classes (recursively)."""
    out, seen, todo = set(), set(), [b.id for b in cls.bases if isinstance(b, ast.Name)]
    while todo:
        name = todo.pop()
        if name in seen or name not in classes:
            continue
        seen.add(name)
        base = classes[name]
        out |= {n.name for n in base.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        todo += [b.id for b in base.bases if isinstance(b, ast.Name)]
    return out


def _add(found: dict, path: str, q: str, node: ast.AST, method: bool) -> None:
    doc = ast.get_docstring(node) is not None
    it = Item(path, q, node.lineno, doc, _drift(node, method))
    old = found.get(q)
    if old is None or (it.documented and not old.documented):
        found[q] = it


def items(path: str, text: str) -> list[Item]:
    """Public items of one module; [] for a private module, a test file or a file that does not
    parse. Nested classes are not public; overrides (`@override`, or a method an in-module base
    class defines) are skipped; duplicate definitions count as documented if one is."""
    if not path.endswith(".py") or not public_module(path) or is_test(path):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    allowed = _all_names(tree)
    top = _flat(tree.body)
    classes = {n.name: n for n in top if isinstance(n, ast.ClassDef)}
    found: dict[str, Item] = {}
    for node in top:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name.startswith("_") or (allowed is not None and node.name not in allowed) or _skipped(node):
            continue
        _add(found, path, node.name, node, False)
        if isinstance(node, ast.ClassDef):
            inherited = _base_methods(node, classes)
            for m in node.body:
                if (isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_")
                        and not _skipped(m) and m.name not in inherited
                        and "override" not in {_dec_name(d) for d in m.decorator_list}):
                    _add(found, path, f"{node.name}.{m.name}", m, True)
    return sorted(found.values(), key=lambda it: it.line)


def is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return ("tests" in parts[:-1] or "test" in parts[:-1] or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def ratchet(base: dict[str, str], head: dict[str, str], renames: dict[str, str]) -> list[str]:
    """Failures for head files vs base files. base/head: {path: text} of the touched files;
    renames: {head path: base path}. A name that moved keeps its status: a qualname undocumented
    (drifting) in a touched base file it has left (file deleted, or the name gone from it at
    head) is grandfathered."""
    old = [it for p, t in base.items() for it in items(p, t)]
    old_by_key = {(it.path, it.qualname): it for it in old}
    inv = {bp: hp for hp, bp in renames.items()}
    head_names = {path: {it.qualname for it in items(path, text)} for path, text in head.items()}

    def left(it):
        hp = inv.get(it.path, it.path)
        return hp not in head_names or it.qualname not in head_names[hp]
    undocumented = {it.qualname for it in old if not it.documented and left(it)}
    drifting = {it.qualname for it in old if it.drift and left(it)}
    fails = []
    for path, text in sorted(head.items()):
        bpath = renames.get(path, path)
        for it in items(path, text):
            prev = old_by_key.get((bpath, it.qualname))
            if not it.documented and not (prev and not prev.documented) and it.qualname not in undocumented:
                fails.append(f"{path}:{it.line} {it.qualname}: public name without docstring (D1)")
            if it.drift and not (prev and prev.drift) and it.qualname not in drifting:
                fails.append(f"{path}:{it.line} {it.qualname}: documented parameters differ from "
                             f"the signature: {it.drift} (D2)")
    return fails


def compare_refs(repo, base: str, head: str | None, cfg, changes) -> list[str] | None:
    """D1/D2 ratchet over the touched in-scope Python files; None unless `[docs] docstrings`."""
    from . import gitio  # noqa: PLC0415
    if not (cfg.docs or {}).get("docstrings"):
        return None

    def ok(p):
        return bool(p) and p.endswith(".py") and cfg.in_scope(p)
    tb = gitio.read_files(repo, base, sorted({bp for _, bp, _ in changes if ok(bp)}))
    th = gitio.read_files(repo, head, sorted({hp for _, _, hp in changes if ok(hp)}))
    renames = {hp: bp for _, bp, hp in changes if ok(bp) and ok(hp) and bp != hp}
    return ratchet(tb, th, renames)
