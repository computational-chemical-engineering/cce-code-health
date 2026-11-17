"""Prototype measures for the python@1 design (scratch, not shipped).

usage: measure.py ROOT OUT.json   -> per-function and per-class candidate metrics
"""
import ast
import json
import os
import sys

sys.path.insert(0, "/home/eajfpeters/Code/cce-code-health/src")
from cce_code_health import python_backend as pb  # noqa: E402

LOOPS = (ast.For, ast.AsyncFor, ast.While)
CONDS = (ast.If, ast.Match)
TRY = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())
SETS = {"all": pb.CONTROL, "nontry": CONDS + LOOPS, "cond": CONDS}
CTOR = {"__init__", "__post_init__", "__new__", "__init_subclass__", "setUp", "setup_method",
        "setUpClass", "setup_class", "__setstate__", "__enter__"}


def stmt_depths(func, ctl):
    """[(lineno, depth)] for every statement in source order; depth = enclosing control count
    (elif does not nest). Nested defs/classes/lambdas are walked as part of the unit, like nesting_depth
    (classes skipped)."""
    out = []

    def walk(stmts, depth, parent=None):
        for s in stmts:
            if isinstance(s, ast.ClassDef):
                continue
            out.append((s.lineno, depth, s))
            is_ctl = isinstance(s, ctl)
            for name in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(s, name, None)
                if not sub:
                    continue
                if name == "orelse" and isinstance(s, ast.If) and len(sub) == 1 and isinstance(sub[0], ast.If):
                    walk(sub, depth)          # elif: same level as the if
                    continue
                if name == "handlers":
                    for h in sub:
                        walk(h.body, depth + 1 if is_ctl else depth)
                    continue
                walk(sub, depth + 1 if is_ctl else depth)
            if isinstance(s, ast.Match):
                for c in s.cases:
                    walk(c.body, depth + 1 if is_ctl else depth)
    walk(func.body, 0)
    return out


def bumps_runs(func, ctl, k=2):
    """Maximal runs of consecutive statements at depth >= k (a statement at depth < k separates)."""
    n, inrun = 0, False
    for _, d, _ in stmt_depths(func, ctl):
        if d >= k:
            if not inrun:
                n += 1
            inrun = True
        else:
            inrun = False
    return n


def bumps_runh(func, ctl, k=2, need_if=False):
    """Runs of statements whose level (enclosing controls + 1 if itself a control) >= k."""
    n, inrun, has = 0, False, False
    for _, d, s in stmt_depths(func, ctl) + [(0, -9, None)]:
        lvl = d + (1 if isinstance(s, ctl) else 0)
        if lvl >= k:
            if not inrun:
                inrun, has = True, False
            if isinstance(s, CONDS) and d >= 1:
                has = True
        else:
            if inrun and (has or not need_if):
                n += 1
            inrun = False
    return n


def boolops_cond(func, kinds):
    def count(n):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return count(n.operand)
        if isinstance(n, ast.BoolOp):
            return len(n.values) - 1 + sum(count(v) for v in n.values)
        return 0
    best = 0
    for n in ast.walk(func):
        tests = []
        if isinstance(n, kinds):
            tests = [n.test]
        if "comp" in KINDS_FLAGS and isinstance(n, ast.comprehension):
            tests = list(n.ifs)
        for t in tests:
            best = max(best, count(t))
    return best


KINDS_FLAGS = set()


def bumps_heads(func, ctl, k=2):
    """Control statements at depth k-1 that contain a control statement (reach depth k)."""
    n = 0
    for _, d, s in stmt_depths(func, ctl):
        if d == k - 1 and isinstance(s, ctl) and pb.nesting_depth(s, 1) >= k if ctl is pb.CONTROL else False:
            n += 1
    return n


def bumps_top_cond(func, ctl):
    """python@0 rule with a different control set: top-level statements reaching depth >= 2."""
    def depth(node, d=0):
        best = d
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(c, ctl):
                el = isinstance(node, ast.If) and isinstance(c, ast.If) and node.orelse == [c]
                best = max(best, depth(c, d if el else d + 1))
            else:
                best = max(best, depth(c, d))
        return best
    return sum(1 for s in func.body if isinstance(s, ctl) and depth(s, 1) >= 2)


def is_public(qual):
    return not any(p.startswith("_") and not (p.startswith("__") and p.endswith("__"))
                   for p in qual.split("."))


# ---------- class state ----------
def self_name(m):
    a = m.args.posonlyargs + m.args.args
    return a[0].arg if a else None


def attr_key(node, me):
    """'x' for self.x, 'x[k]' for self.x['k'] (literal str key), else None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == me:
        return node.attr
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        base = attr_key(node.value, me)
        if base and "[" not in base:
            return f"{base}[{node.slice.value}]"
    return None


def writes_reads(m, me):
    w, r = set(), set()
    for n in ast.walk(m):
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets = [n.target]
        elif isinstance(n, ast.Delete):
            targets = n.targets
        flat = []
        for t in targets:
            flat += list(t.elts) if isinstance(t, (ast.Tuple, ast.List)) else [t]
        for t in flat:
            k = attr_key(t, me)
            if k:
                w.add(k)
                if isinstance(t, ast.Subscript):     # self.d['k'] = v also mutates d
                    w.add(k.split("[")[0])
            elif isinstance(t, ast.Subscript):        # self.d[i] = v: container write to d
                k2 = attr_key(t.value, me)
                if k2:
                    w.add(k2)
        if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(getattr(n, "ctx", None), ast.Load):
            k = attr_key(n, me)
            if k:
                r.add(k)
    return w, r


def decorated(m, names):
    for d in m.decorator_list:
        s = ast.unparse(d)
        if any(s == x or s.endswith("." + x) for x in names):
            return True
    return False


def class_state(cls):
    methods = [n for n in cls.body if isinstance(n, pb.FUNC)]
    method_names = {m.name for m in methods}
    init_false = 0
    is_dc = any("dataclass" in ast.unparse(d) for d in cls.decorator_list)
    for n in cls.body:
        if is_dc and isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Call):
            if any(kw.arg == "init" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                   for kw in n.value.keywords):
                init_false += 1
    # constructor helpers: private methods called (self.m()) only from constructors or helpers
    callers = {m.name: set() for m in methods}
    for m in methods:
        me = self_name(m)
        for n in ast.walk(m):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == me
                    and n.func.attr in callers):
                callers[n.func.attr].add(m.name)
    ctor = set(CTOR) & method_names
    changed = True
    while changed:
        changed = False
        for name, cs in callers.items():
            if (name not in ctor and name.startswith("_") and not name.endswith("__") and cs
                    and cs <= ctor):
                ctor.add(name)
                changed = True
    W, R = {}, {}
    for m in methods:
        if m.name in ctor or decorated(m, ("staticmethod", "classmethod", "setter", "deleter")):
            continue
        me = self_name(m)
        if not me:
            continue
        w, r = writes_reads(m, me)
        for a in w:
            if a not in method_names:
                W.setdefault(a, set()).add(m.name)
        for a in r:
            R.setdefault(a, set()).add(m.name)
    # channel: written by a non-constructor method and read by another method
    chan = {a for a, ws in W.items() if (R.get(a, set()) - ws) or len(ws) >= 2}
    chan_strict = {a for a, ws in W.items() if R.get(a, set()) - ws}
    fanin3 = {a for a, ws in W.items() if len(ws) >= 3}
    dictkeys = {a for a in chan if "[" in a}
    pairs = sum(len(W[a]) for a in chan)
    base = [b for b in (ast.unparse(x) for x in cls.bases)]
    return dict(ctor_helpers=len(ctor - set(CTOR)), methods=len(methods), init_false=init_false, chan=len(chan), chan_strict=len(chan_strict),
                fanin3=len(fanin3), dictkeys=len(dictkeys), write_pairs=pairs, bases=base,
                chan_names=sorted(chan)[:40])


def main(root, out):
    funcs, classes = [], []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root)
            src = open(p, encoding="utf-8", errors="replace").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            fm = pb.analyze(rel, src)
            nodes = dict(pb.units(tree))
            for m in fm.functions:
                node = nodes.get(m.name)
                rec = dict(path=rel, name=m.name, nloc=m.nloc, cog=m.cognitive, nest=m.nesting,
                           b0=m.bumps, params=m.params, boolops=m.boolops, public=is_public(m.name),
                           span=m.end - min([d.lineno for d in node.decorator_list] + [node.lineno]) + 1)
                for key, ctl in SETS.items():
                    rec[f"run_{key}"] = bumps_runs(node, ctl)
                    rec[f"top_{key}"] = bumps_top_cond(node, ctl)
                for key in ("all", "nontry"):
                    rec[f"runh_{key}"] = bumps_runh(node, SETS[key])
                    rec[f"runhif_{key}"] = bumps_runh(node, SETS[key], need_if=True)
                rec["bo_ifw"] = boolops_cond(node, (ast.If, ast.While))
                rec["bo_ifwe"] = boolops_cond(node, (ast.If, ast.While, ast.IfExp))
                rec["bo_ifwea"] = boolops_cond(node, (ast.If, ast.While, ast.IfExp, ast.Assert))
                funcs.append(rec)

            def walk(body, prefix):
                for n in body:
                    if isinstance(n, ast.ClassDef):
                        classes.append(dict(path=rel, name=prefix + n.name, line=n.lineno,
                                            nloc=n.end_lineno - n.lineno + 1, **class_state(n)))
                        walk(n.body, prefix + n.name + ".")
            walk(tree.body, "")
    json.dump(dict(funcs=funcs, classes=classes), open(out, "w"))
    print(f"{root}: {len(funcs)} functions, {len(classes)} classes")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
