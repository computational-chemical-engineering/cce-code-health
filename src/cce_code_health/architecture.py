"""Architecture rules: layering, forbidden imports and import cycles, gated as a ratchet.

Configured in the optional `[architecture]` table of `.codehealth/config.toml` (absent: off):

    [architecture]
    roots = ["src"]                 # import roots: src/pkg/mod.py -> pkg.mod, src/pkg/__init__.py -> pkg
    type_checking = false           # false: imports under `if TYPE_CHECKING:` are ignored
                                    # (only a TYPE_CHECKING bound once, from typing/typing_extensions)
    cycle_depth = 2                 # cycles between packages truncated to this many dotted parts
    [[architecture.layers]]
    name = "app-layers"
    order = ["pkg.application", "pkg.services", "pkg.domain"]   # top to bottom
    [[architecture.forbid]]
    name = "domain-pure"
    from = ["pkg.domain"]
    to = ["pkg.application", "requests"]
    reason = "domain code has no I/O"

A module entry "a.b" matches a.b and a.b.*; an entry that matches no scanned or imported module
draws a warning (`codehealth arch`, the gate's architecture check), not a failure. Imports are read with ast from every in-scope .py
file under a root, at any nesting level: `import x.y` gives x.y; `from x import y` gives x.y if
that module is among the scanned files, else x; relative imports are resolved against the
file's module. Layers: an importer in layer i (longest matching entry) may not import a module
in a higher layer j < i; modules in no layer are ignored. Forbid: an importer matching `from`
may not import a module matching `to`. Cycles: the graph of internal imports (top-level package
among the scanned modules) truncated to `cycle_depth` parts, self-edges dropped; every strongly
connected component with more than one node is a cycle, reported with its edges and one
file:line example per edge.

Ratchet identities (line numbers ignored): a violation is (rule, importer, imported); a cycle
edge is the truncated (a, b) inside a cycle. `codehealth diff` and the gate's static check
`architecture` fail on identities present at head but not at base. In restructure mode a new
violation whose importer is a module of a planned new file is excused by a disappeared violation
with the same rule and imported module (code moved with its import); cycle edges stay strict.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from . import config, gitio

ARCH_KEYS = {"roots", "type_checking", "cycle_depth", "layers", "forbid"}
LAYER_KEYS = {"name", "order"}
FORBID_KEYS = {"name", "from", "to", "reason"}


def _str_list(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x for x in v)


def check_config(arch) -> list[str]:
    """Problems in an [architecture] table (empty list: valid)."""
    if not isinstance(arch, dict):
        return ["[architecture] must be a table"]
    bad = [f"unknown key {k!r} in [architecture]" for k in sorted(set(arch) - ARCH_KEYS)]
    if "roots" in arch and not _str_list(arch["roots"]):
        bad.append("[architecture] roots must be a non-empty list of strings")
    if not isinstance(arch.get("type_checking", False), bool):
        bad.append("[architecture] type_checking must be true or false")
    depth = arch.get("cycle_depth", 2)
    if not (isinstance(depth, int) and not isinstance(depth, bool) and depth >= 1):
        bad.append(f"[architecture] cycle_depth must be an integer >= 1, got {depth!r}")
    names = []
    for kind, keys, lists in (("layers", LAYER_KEYS, ("order",)), ("forbid", FORBID_KEYS, ("from", "to"))):
        rules = arch.get(kind, [])
        if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
            bad.append(f"[architecture] {kind} must be an array of tables ([[architecture.{kind}]])")
            continue
        for i, r in enumerate(rules):
            where = f"architecture.{kind} {r.get('name', f'#{i + 1}')!r}"
            if not isinstance(r.get("name"), str) or not r.get("name"):
                bad.append(f"{where}: name missing")
            else:
                names.append(r["name"])
            bad += [f"{where}: unknown key {k!r}" for k in sorted(set(r) - keys)]
            bad += [f"{where}: {k} must be a non-empty list of module names" for k in lists
                    if not _str_list(r.get(k))]
            if "reason" in r and not isinstance(r["reason"], str):
                bad.append(f"{where}: reason must be a string")
    bad += [f"duplicate architecture rule name {n!r}" for n in sorted({n for n in names if names.count(n) > 1})]
    return bad


# -- import extraction --------------------------------------------------------------------------

def module_name(path: str, roots) -> tuple[str, bool] | None:
    """(dotted module, is_package) of a .py path under one of the roots, or None."""
    if not path.endswith(".py"):
        return None
    for root in sorted(roots, key=len, reverse=True):
        root = root.strip("/")
        prefix = "" if root in ("", ".") else root + "/"
        if not path.startswith(prefix):
            continue
        parts = path[len(prefix):-3].split("/")
        pkg = parts[-1] == "__init__"
        if pkg:
            parts = parts[:-1]
        if parts and all(p.isidentifier() for p in parts):
            return ".".join(parts), pkg
    return None


TYPING_MODULES = ("typing", "typing_extensions")


def _bound_names(tree) -> dict[str, list]:
    """Every binding of a name anywhere in tree: name -> [binding nodes]."""
    out: dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                name = a.asname or (a.name.split(".")[0] if isinstance(node, ast.Import) else a.name)
                out.setdefault(name, []).append(node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.setdefault(node.id, []).append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.arg):
            out.setdefault(node.arg, []).append(node)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)) and node.name:
            out.setdefault(node.name, []).append(node)
    return out


def _type_checking_guards(tree) -> tuple[set[str], set[str]]:
    """(bare names, module names) under which `if NAME:` / `if MOD.TYPE_CHECKING:` is a genuine
    typing.TYPE_CHECKING guard: the name is bound exactly once in the module, by
    `from typing import TYPE_CHECKING` (or typing_extensions) or `import typing`, and
    TYPE_CHECKING is never assigned as an attribute."""
    bound = _bound_names(tree)
    names, mods = set(), set()
    for name, nodes in bound.items():
        if len(nodes) != 1:
            continue
        node = nodes[0]
        if isinstance(node, ast.ImportFrom) and not node.level and node.module in TYPING_MODULES:
            if any(a.name == "TYPE_CHECKING" and (a.asname or a.name) == name for a in node.names):
                names.add(name)
        elif isinstance(node, ast.Import):
            if any(a.name in TYPING_MODULES and (a.asname or a.name) == name for a in node.names):
                mods.add(name)
    if any(isinstance(n, ast.Attribute) and n.attr == "TYPE_CHECKING" and not isinstance(n.ctx, ast.Load)
           for n in ast.walk(tree)):
        mods = set()
    return names, mods


def _is_type_checking(test, guards: tuple[set[str], set[str]]) -> bool:
    names, mods = guards
    return ((isinstance(test, ast.Name) and test.id in names)
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                and isinstance(test.value, ast.Name) and test.value.id in mods))


def _import_nodes(tree, type_checking: bool):
    """Import and ImportFrom nodes anywhere in tree (iterative; no recursion limit)."""
    guards = (set(), set()) if type_checking else _type_checking_guards(tree)
    stack, out = [tree], []
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(node)
            continue
        if not type_checking and isinstance(node, ast.If) and _is_type_checking(node.test, guards):
            stack.extend(node.orelse)
        else:
            stack.extend(ast.iter_child_nodes(node))
    return sorted(out, key=lambda n: (n.lineno, n.col_offset))


def _targets(node, module: str, is_pkg: bool, known: set[str]) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if node.level:
        parts = module.split(".") if is_pkg else module.split(".")[:-1]
        if node.level - 1 >= len(parts):
            return []   # beyond the top-level package
        parts = parts[:len(parts) - (node.level - 1)]
        base = ".".join(parts + ([node.module] if node.module else []))
    else:
        base = node.module or ""
    if not base:
        return []
    out = []
    for a in node.names:
        sub = f"{base}.{a.name}"
        out.append(sub if a.name != "*" and sub in known else base)
    return list(dict.fromkeys(out))


@dataclass
class Imp:
    importer: str
    imported: str
    path: str
    line: int


@dataclass
class Result:
    files: int = 0
    imports: list = field(default_factory=list)       # [Imp]
    violations: dict = field(default_factory=dict)    # (rule, a, b) -> Imp (first example)
    cycles: list = field(default_factory=list)        # [{"nodes": [...], "edges": [...]}]
    cycle_edges: dict = field(default_factory=dict)   # (a, b) -> Imp (first example)
    kinds: dict = field(default_factory=dict)         # rule -> "layers" | "forbid"
    reasons: dict = field(default_factory=dict)       # rule -> reason
    warnings: list = field(default_factory=list)      # ["rule <name>: entry <x> matches no module"]


def _matches(mod: str, entry: str) -> bool:
    return mod == entry or mod.startswith(entry + ".")


def _layer(mod: str, order: list[str]) -> int | None:
    best = None
    for i, e in enumerate(order):
        if _matches(mod, e) and (best is None or len(e) > len(order[best])):
            best = i
    return best


def _sccs(nodes: list, succ: dict) -> list[list]:
    """Strongly connected components (iterative Tarjan)."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(succ.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(succ.get(w, ())))))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
    return out


def analyse_texts(texts: dict[str, str], arch: dict) -> Result:
    """Architecture result for {path: source} under the [architecture] table `arch`."""
    roots = arch.get("roots", ["."])
    mods = {}
    for p in sorted(texts):
        m = module_name(p, roots)
        if m:
            mods[p] = m
    known = {m for m, _ in mods.values()}
    tops = {m.split(".")[0] for m in known}
    res = Result(files=len(mods))
    for p, (mod, pkg) in mods.items():
        try:
            tree = ast.parse(texts[p])
        except SyntaxError:
            continue
        for node in _import_nodes(tree, arch.get("type_checking", False)):
            res.imports += [Imp(mod, t, p, node.lineno) for t in _targets(node, mod, pkg, known)]
    seen = known | {imp.imported for imp in res.imports}
    for kind, keys in (("layers", ("order",)), ("forbid", ("from", "to"))):
        for rule in arch.get(kind, []):
            res.warnings += [f"rule {rule['name']}: entry {e} matches no module"
                             for k in keys for e in rule[k] if not any(_matches(m, e) for m in seen)]
    for rule in arch.get("layers", []):
        res.kinds[rule["name"]] = "layers"
        for imp in res.imports:
            i, j = _layer(imp.importer, rule["order"]), _layer(imp.imported, rule["order"])
            if i is not None and j is not None and j < i:
                res.violations.setdefault((rule["name"], imp.importer, imp.imported), imp)
    for rule in arch.get("forbid", []):
        res.kinds[rule["name"]] = "forbid"
        res.reasons[rule["name"]] = rule.get("reason", "")
        for imp in res.imports:
            if (any(_matches(imp.importer, e) for e in rule["from"])
                    and any(_matches(imp.imported, e) for e in rule["to"])):
                res.violations.setdefault((rule["name"], imp.importer, imp.imported), imp)
    depth = arch.get("cycle_depth", 2)
    cut = lambda m: ".".join(m.split(".")[:depth])  # noqa: E731
    examples: dict[tuple, Imp] = {}
    for imp in res.imports:
        if imp.imported.split(".")[0] in tops:
            a, b = cut(imp.importer), cut(imp.imported)
            if a != b:
                examples.setdefault((a, b), imp)
    succ: dict[str, set] = {}
    for a, b in examples:
        succ.setdefault(a, set()).add(b)
    nodes = sorted(set(succ) | {b for s in succ.values() for b in s})
    for comp in _sccs(nodes, succ):
        if len(comp) < 2:
            continue
        inside = set(comp)
        edges = sorted(e for e in examples if e[0] in inside and e[1] in inside)
        for e in edges:
            res.cycle_edges[e] = examples[e]
        res.cycles.append({"nodes": comp, "edges": [
            {"from": a, "to": b, "importer": examples[(a, b)].importer,
             "imported": examples[(a, b)].imported, "path": examples[(a, b)].path,
             "line": examples[(a, b)].line} for a, b in edges]})
    res.cycles.sort(key=lambda c: c["nodes"])
    return res


def analyse(repo: Path, ref: str | None, cfg: config.Config) -> Result | None:
    """Analyse the whole scope at ref (None: worktree); None when [architecture] is absent."""
    arch = cfg.architecture
    if not arch:
        return None
    problems = check_config(arch)
    if problems:
        raise ValueError(f"{cfg.source}: " + "; ".join(problems))
    roots = arch.get("roots", ["."])
    files = [p for p in gitio.ls_files(repo, ref) if cfg.in_scope(p) and module_name(p, roots)]
    return analyse_texts(gitio.read_files(repo, ref, files), arch)


def _where(imp: Imp) -> str:
    return f"{imp.path}:{imp.line}"


def ratchet(before: Result, after: Result, moved_to: set[str] = frozenset()) -> tuple[list[str], list[str]]:
    """(failures, improvements) going from before (base) to after (head).

    `moved_to` (restructure mode): modules of the plan's new files. A new violation (r, m, b)
    with m in moved_to is excused by a violation (r, x, b) that disappeared in the same step,
    one for one: code moved with its grandfathered import. Cycle edges stay strict."""
    new = sorted(set(after.violations) - set(before.violations))
    gone = sorted(set(before.violations) - set(after.violations))
    moved = []
    for r, m, b in list(new):
        if m not in moved_to:
            continue
        x = next((g for g in gone if g[0] == r and g[2] == b), None)
        if x is not None:
            gone.remove(x)
            new.remove((r, m, b))
            moved.append(f"moved architecture violation: {r}: {x[1]} -> {b} now {m} -> {b}")
    fail = [f"new architecture violation: {r}: {a} -> {b} ({_where(after.violations[(r, a, b)])})"
            for r, a, b in new]
    fail += [f"new import cycle edge: {a} -> {b} ({_where(after.cycle_edges[(a, b)])})"
             for a, b in sorted(set(after.cycle_edges) - set(before.cycle_edges))]
    gain = [f"removed architecture violation: {r}: {a} -> {b}" for r, a, b in gone]
    gain += [f"removed import cycle edge: {a} -> {b}"
             for a, b in sorted(set(before.cycle_edges) - set(after.cycle_edges))]
    return fail, gain + moved


def compare_refs(repo: Path, base: str, head: str | None, cfg: config.Config,
                 moved_to: set[str] = frozenset()) -> tuple[list[str], list[str], list[str]] | None:
    """Ratchet base against head (None: worktree) with the base config: (failures, improvements,
    warnings about head); None when off."""
    before = analyse(repo, base, cfg)
    if before is None:
        return None
    after = analyse(repo, head, cfg)
    return (*ratchet(before, after, moved_to), after.warnings)


def edge_counts(res: Result, depth: int) -> list[tuple[str, str, int]]:
    """Distinct module-to-module imports between in-repo groups, modules cut to `depth` dotted
    components (1: top-level packages): [(from, to, n)], most edges first. For designing a layer
    order: a pair present in both directions is a cycle at that depth."""
    tops = {imp.importer.split(".")[0] for imp in res.imports}
    cut = lambda m: ".".join(m.split(".")[:depth])  # noqa: E731
    pairs = {(imp.importer, imp.imported) for imp in res.imports if imp.imported.split(".")[0] in tops}
    counts: dict[tuple[str, str], int] = {}
    for a, b in pairs:
        ka, kb = cut(a), cut(b)
        if ka != kb:
            counts[(ka, kb)] = counts.get((ka, kb), 0) + 1
    return sorted(((a, b, n) for (a, b), n in counts.items()), key=lambda x: (-x[2], x[0], x[1]))


def to_json(res: Result) -> dict:
    return {"files": res.files, "imports": len(res.imports),
            "violations": [{"rule": r, "kind": res.kinds.get(r), "importer": a, "imported": b,
                            "path": imp.path, "line": imp.line}
                           for (r, a, b), imp in sorted(res.violations.items())],
            "cycles": res.cycles, "warnings": res.warnings}


def report_lines(res: Result) -> list[str]:
    rows = []
    rules = sorted({r for r, _, _ in res.violations})
    for rule in rules:
        items = sorted((k, v) for k, v in res.violations.items() if k[0] == rule)
        reason = f" -- {res.reasons[rule]}" if res.reasons.get(rule) else ""
        rows.append(f"  {res.kinds.get(rule, 'rule')} {rule}: {len(items)} violation(s){reason}")
        rows += [f"    {a} -> {b} ({_where(imp)})" for (_, a, b), imp in items]
    for c in res.cycles:
        rows.append(f"  cycle {' <-> '.join(c['nodes'])}:")
        rows += [f"    {e['from']} -> {e['to']}: {e['importer']} imports {e['imported']} "
                 f"({e['path']}:{e['line']})" for e in c["edges"]]
    return rows
