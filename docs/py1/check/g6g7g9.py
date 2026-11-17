"""G6, G7, G9 on the extracted OFS tree and solve.py@230bbc3 (python@1 implementation)."""
import ast, json, os, sys
from collections import defaultdict
from cce_code_health import profiles, pyclones
from cce_code_health.python_backend import analyze

root, solve = sys.argv[1], sys.argv[2]
P0, P1 = profiles.get("python@0"), profiles.get("python@1")
cs = {f["path"]: f for f in json.load(open(
    "/home/eajfpeters/Code/cce-code-health/baselines/openflowsheet-a9e733e4/codescene_files.json"))
    if f["language"] == "Python" and isinstance(f["code_health"], (int, float))}

def cscount(p, smell):
    for v in cs[p]["violations"] or []:
        if v["code_smell"] == smell:
            return v.get("count", 1)
    return 0

f0, f1, cloned = defaultdict(list), defaultdict(list), {}
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if not d.startswith(".")]
    for f in fn:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f); rel = os.path.relpath(p, root)
        src = open(p, encoding="utf-8", errors="replace").read()
        f0[rel] = analyze(rel, src, 3).functions
        m = analyze(rel, src, 3, P1.py_rules, 3, 30)
        f1[rel] = m.functions
        if not m.error:
            cloned[rel] = bool(pyclones.clone_groups(ast.parse(src), 3, 30))

def cmp(label, by, smell, pred):
    ps = list(cs)
    ours = [sum(1 for x in by.get(p, []) if pred(x)) for p in ps]
    theirs = [cscount(p, smell) for p in ps]
    ad = sum(abs(a - b) for a, b in zip(ours, theirs))
    print(f"{label}: files {len(ps)}, ours {sum(ours)}, CodeScene {sum(theirs)}, sum abs diff {ad}")
    return sum(ours), ad

cmp("G6 python@0 bumps>=2", f0, "Bumpy Road", lambda x: x.bumps >= 2)
cmp("G6 python@1 bumps>=2", f1, "Bumpy Road", lambda x: x.bumps >= 2)
cmp("G7 python@0 boolops>=2", f0, "Complex Conditional", lambda x: x.boolops >= 2)
cmp("G7 python@1 boolops>=2", f1, "Complex Conditional", lambda x: x.boolops >= 2)
src_files = [p for p in cloned if p.startswith("src/")]
print(f"G9 OFS src files with clones: {sum(cloned[p] for p in src_files)} of {len(src_files)}")
for g in pyclones.clone_groups(ast.parse(open(solve).read()), 3, 30):
    print("G9 solve.py@230bbc3 group:", [(o[0].lineno, o[-1].end_lineno) for o in g])
