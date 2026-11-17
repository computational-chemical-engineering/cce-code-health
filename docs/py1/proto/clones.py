"""Intra-file AST clones: windows of K consecutive statements, atoms abstracted (scratch).

usage: clones.py ROOT K MINNODES [show-file-substring]
"""
import ast
import hashlib
import os
import sys
from collections import defaultdict

ATOM = (ast.Name, ast.Attribute, ast.Constant, ast.JoinedStr, ast.Subscript)


def key(node):
    """Structural key: names, attribute chains, constants, f-strings, subscripts -> '*'."""
    if isinstance(node, ATOM) and not (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)):
        return "*"
    if isinstance(node, ast.AST):
        parts = [type(node).__name__]
        for f, v in ast.iter_fields(node):
            if f in ("ctx", "type_comment", "lineno", "col_offset"):
                continue
            if isinstance(v, list):
                parts.append(f + "[" + ",".join(key(x) for x in v) + "]")
            elif isinstance(v, ast.AST):
                parts.append(f + "=" + key(v))
            elif f in ("arg", "attr") or isinstance(v, str):
                parts.append(f"{f}:{v}" if f == "arg" else "")
            elif v is not None:
                parts.append(str(v))
        return "(" + " ".join(parts) + ")"
    return str(node)


def size(stmts):
    return sum(1 for s in stmts for _ in ast.walk(s))


CTL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)


def blocks(tree):
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for n in (x for f in funcs for x in ast.walk(f)):
        for f in ("body", "orelse", "finalbody"):
            b = getattr(n, f, None)
            if isinstance(b, list) and b and isinstance(b[0], ast.stmt) and not isinstance(n, ast.Module):
                yield b
        for h in getattr(n, "handlers", []) or []:
            yield h.body


def file_clones(src, K, MIN):
    tree = ast.parse(src)
    doc = set()
    seen = defaultdict(list)
    for b in blocks(tree):
        b = [s for s in b if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        for i in range(len(b) - K + 1):
            w = b[i:i + K]
            if size(w) < MIN or not any(isinstance(x, CTL) for x in w):
                continue
            h = hashlib.blake2b("|".join(key(s) for s in w).encode(), digest_size=10).digest()
            seen[h].append((w[0].lineno, w[-1].end_lineno))
    lines, groups = set(), []
    for locs in seen.values():
        locs = sorted(set(locs))
        # distinct, non-overlapping occurrences
        keep = []
        for a, e in locs:
            if all(e < a2 or a > e2 for a2, e2 in keep):
                keep.append((a, e))
        if len(keep) > 1:
            groups.append(keep)
            for a, e in keep:
                lines.update(range(a, e + 1))
    return lines, groups


def main():
    root, K, MIN = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    show = sys.argv[4] if len(sys.argv) > 4 else None
    tot_lines = tot = files_hit = nfiles = 0
    top = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            src = open(p, encoding="utf-8", errors="replace").read()
            try:
                lines, groups = file_clones(src, K, MIN)
            except SyntaxError:
                continue
            nloc = sum(1 for s in src.splitlines() if s.strip() and not s.strip().startswith("#"))
            nfiles += 1
            tot += nloc
            tot_lines += len(lines)
            files_hit += bool(groups)
            top.append((len(lines) / max(nloc, 1), len(lines), nloc, os.path.relpath(p, root)))
            if show and show in p:
                for g in groups:
                    print("  clone", g)
    top.sort(reverse=True)
    print(f"K={K} MIN={MIN}: files {nfiles}, with clones {files_hit}, cloned lines {tot_lines}/{tot} "
          f"({tot_lines / max(tot, 1):.1%}); files >10%: {sum(t[0] > 0.10 for t in top)}, >5%: {sum(t[0] > 0.05 for t in top)}")
    for t in top[:6]:
        print(f"   {t[0]:.1%} {t[1]}/{t[2]} {t[3]}")


main()
