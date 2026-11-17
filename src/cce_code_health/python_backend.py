"""Python backend: per-function metrics from source text.

A "unit" is a function or method defined at module level or directly in a class body
(classes may nest). Functions nested inside a unit belong to that unit, as in
complexipy's cognitive complexity.

python@1 (profile py_rules, docs/py1/design.md) measures bumps as runs (bumps_runs), boolops in
conditions only (boolops_conditions), and adds state_writes (classstate.py), clones
(pyclones.py) and `public`. With no rules the python@0 path is unchanged.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field

import complexipy

from . import classstate, cohesion, pyclones
from .profiles import METRICS

# Nesting counts branches, loops and exception handling, not context managers.
CONTROL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match)
if hasattr(ast, "TryStar"):
    CONTROL = CONTROL + (ast.TryStar,)
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
NON_CODE = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
ALLOW_RE = re.compile(r"#\s*codehealth:\s*allow(-file)?\s+([\w,\s]+?)\s*(?:--\s*(.*\S))?\s*$")
SMELL_NAMES = set(METRICS) | {"all", "large_file", "huge_file", "duplication"}
PY1_SMELL_NAMES = frozenset(SMELL_NAMES | {"state_writes", "clones"})   # python@1 allow names
# python@1 bump levels: branches and loops; try and with do not count.
LEVEL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Match)


@dataclass
class Allow:
    line: int
    smells: tuple
    reason: str | None
    file_level: bool
    names: frozenset = frozenset(SMELL_NAMES)   # smell names valid in this language

    @property
    def valid(self) -> bool:
        return bool(self.reason) and all(s in self.names for s in self.smells)


@dataclass
class FunctionMetrics:
    name: str          # qualified name, e.g. "Class.method"
    line: int          # def line
    end: int
    nloc: int
    cognitive: int
    nesting: int
    bumps: int
    params: int
    boolops: int
    allows: list = field(default_factory=list)
    state_writes: int | None = None   # python@1 only (None: not measured)
    clones: int | None = None         # python@1 only
    public: bool | None = None        # python@1 only: no "_"-prefixed qualname component

    def value(self, metric: str) -> int:
        return getattr(self, metric)


@dataclass
class FileMetrics:
    path: str
    nloc: int
    functions: list
    allows: list           # all allow comments in the file
    error: str | None = None
    classes: list = field(default_factory=list)   # LCOM4 per class, informational


def nesting_depth(node: ast.AST, depth: int = 0) -> int:
    """Max depth of nested control structures below node (elif does not nest)."""
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, CONTROL):
            is_elif = (isinstance(node, ast.If) and isinstance(child, ast.If)
                       and node.orelse == [child])
            best = max(best, nesting_depth(child, depth if is_elif else depth + 1))
        else:
            best = max(best, nesting_depth(child, depth))
    return best


def bumps(func: ast.AST) -> int:
    """Top-level statements of the body that contain nesting depth >= 2."""
    return sum(1 for s in func.body if isinstance(s, CONTROL) and nesting_depth(s, 1) >= 2)


def boolops(func: ast.AST) -> int:
    """Largest number of boolean operators in one condition."""
    def count(n):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return count(n.operand)
        if isinstance(n, ast.BoolOp):
            return len(n.values) - 1 + sum(count(v) for v in n.values)
        return 0
    return max((count(n) for n in ast.walk(func) if isinstance(n, ast.BoolOp)), default=0)


def _statement_levels(func: ast.AST):
    """(statement, level) in source order, header before body; level = number of enclosing
    LEVEL statements, + 1 if the statement is one. elif stays on its if's level. Nested defs are
    walked as part of the unit, classes are skipped."""
    out = []

    def walk(stmts, depth):
        for s in stmts:
            if isinstance(s, ast.ClassDef):
                continue
            counted = isinstance(s, LEVEL)
            out.append((s, depth + counted))
            inner = depth + counted
            for name in ("body", "handlers", "orelse", "finalbody"):   # source order
                sub = getattr(s, name, None)
                if not sub:
                    continue
                if name == "handlers":
                    for h in sub:
                        walk(h.body, inner)
                elif (name == "orelse" and isinstance(s, ast.If) and len(sub) == 1
                        and isinstance(sub[0], ast.If)):
                    walk(sub, depth)                  # elif: the if's level
                else:
                    walk(sub, inner)
            if isinstance(s, ast.Match):
                for c in s.cases:
                    walk(c.body, inner)
    walk(func.body, 0)
    return out


def bumps_runs(func: ast.AST) -> int:
    """python@1: maximal runs of consecutive statements at level >= 2."""
    n, inrun = 0, False
    for _, level in _statement_levels(func):
        if level >= 2:
            n += not inrun
            inrun = True
        else:
            inrun = False
    return n


def _bool_count(n: ast.AST) -> int:
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
        return _bool_count(n.operand)
    if isinstance(n, ast.BoolOp):
        return len(n.values) - 1 + sum(_bool_count(v) for v in n.values)
    return 0


def boolops_conditions(func: ast.AST) -> int:
    """python@1: largest number of boolean operators in one if, elif or while condition or
    match guard."""
    best = 0
    for n in ast.walk(func):
        if isinstance(n, (ast.If, ast.While)):
            best = max(best, _bool_count(n.test))
        elif isinstance(n, ast.match_case) and n.guard is not None:
            best = max(best, _bool_count(n.guard))
    return best


def is_public(qualname: str) -> bool:
    """No "_"-prefixed component in the qualified name; dunders are public."""
    return not any(p.startswith("_") and not (p.startswith("__") and p.endswith("__"))
                   for p in qualname.split("."))


def params(func: ast.AST, varargs_weight: int) -> int:
    a = func.args
    names = [x.arg for x in a.posonlyargs + a.args]
    n = len(names) + len(a.kwonlyargs)
    if names and names[0] in ("self", "cls"):
        n -= 1
    n += varargs_weight * ((a.vararg is not None) + (a.kwarg is not None))
    return n


def code_lines(source: str, tree: ast.AST) -> set[int]:
    """Lines with code: not blank, not comment-only, not docstring."""
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type not in NON_CODE:
                lines.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        lines = {i for i, s in enumerate(source.splitlines(), 1)
                 if s.strip() and not s.strip().startswith("#")}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef) + FUNC):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                lines.difference_update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def units(tree: ast.Module):
    """Yield (qualname, node) for module-level functions and methods."""
    def walk(body, prefix):
        for node in body:
            if isinstance(node, FUNC):
                yield prefix + node.name, node
            elif isinstance(node, ast.ClassDef):
                yield from walk(node.body, prefix + node.name + ".")
    yield from walk(tree.body, "")


def parse_allows(source: str, pattern: re.Pattern = ALLOW_RE,
                 names: frozenset = frozenset(SMELL_NAMES)) -> list[Allow]:
    out = []
    for i, line in enumerate(source.splitlines(), 1):
        if "codehealth:" not in line:
            continue
        m = pattern.search(line)
        if m:
            smells = tuple(s for s in re.split(r"[,\s]+", m.group(2)) if s)
            out.append(Allow(i, smells, m.group(3), bool(m.group(1)), names))
    return out


def analyze(path: str, source: str, varargs_weight: int = 3, rules: frozenset = frozenset(),
            clone_window: int = 3, clone_min_nodes: int = 30) -> FileMetrics:
    """`rules`: the profile's py_rules (empty for python@0)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return FileMetrics(path, 0, [], [], error=f"parse: {exc}")
    lines = code_lines(source, tree)
    allows = parse_allows(source, names=PY1_SMELL_NAMES) if rules else parse_allows(source)
    try:
        cog = {f.line_start: f.complexity
               for f in complexipy.code_complexity(source).functions}
    except Exception as exc:  # complexipy rejects some valid files
        cog, error = {}, f"complexipy: {exc}"
    else:
        error = None
    funcs = []
    for name, node in units(tree):
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        span = range(node.lineno, node.end_lineno + 1)
        fn_allows = [a for a in allows if not a.file_level and start - 1 <= a.line <= node.lineno]
        funcs.append(FunctionMetrics(
            name=name, line=node.lineno, end=node.end_lineno,
            nloc=sum(1 for i in span if i in lines),
            cognitive=cog.get(start, cog.get(node.lineno, 0)),
            nesting=nesting_depth(node), bumps=bumps(node),
            params=params(node, varargs_weight), boolops=boolops(node),
            allows=fn_allows))
    classes = cohesion.classes(tree)
    if rules:
        _py1(tree, funcs, classes, rules, clone_window, clone_min_nodes)
    return FileMetrics(path, len(lines), funcs, allows, error, classes)


def _py1(tree, funcs, classes, rules, clone_window, clone_min_nodes) -> None:
    """Apply the python@1 rules to the units (in units() order) and the class records."""
    nodes = [node for _, node in units(tree)]
    writes: dict[int, int] = {}
    if "state" in rules:
        for rec, (_, cls, chan) in zip(classes, classstate.classes(tree)):
            writes.update({id(m): n for m, n in classstate.method_state_writes(cls, chan).items()})
            rec["state_writes"] = classstate.class_state_writes(chan)
            rec["channels"] = chan
    marked = (pyclones.marked_statements(tree, clone_window, clone_min_nodes)
              if "clones" in rules else set())
    for fm, node in zip(funcs, nodes):
        if "bump_runs" in rules:
            fm.bumps = bumps_runs(node)
        if "cond_boolops" in rules:
            fm.boolops = boolops_conditions(node)
        if "state" in rules:
            fm.state_writes = writes.get(id(node), 0)
        if "clones" in rules:
            fm.clones = pyclones.count_in(node, marked)
        fm.public = is_public(fm.name)
