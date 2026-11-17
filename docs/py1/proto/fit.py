"""Compare per-file smell counts of candidate rules with CodeScene (scratch)."""
import json
import sys
from collections import defaultdict

S = sys.argv[1]
cs = {f["path"]: f for f in json.load(open(
    "/home/eajfpeters/Code/cce-code-health/baselines/openflowsheet-a9e733e4/codescene_files.json"))
    if f["language"] == "Python" and isinstance(f["code_health"], (int, float))}
d = json.load(open(f"{S}/proto/ofs.json"))
by = defaultdict(list)
for f in d["funcs"]:
    by[f["path"]].append(f)
paths = [p for p in cs if p in by or True]


def cscount(p, smell):
    for v in cs[p]["violations"] or []:
        if v["code_smell"] == smell:
            return v.get("count", 1)
    return 0


def rankdata(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and xs[o[j + 1]] == xs[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2
        i = j + 1
    return r


def pearson(a, b):
    n = len(a); ma = sum(a) / n; mb = sum(b) / n
    c = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return c / ((sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5 or 1)


def report(label, smell, pred, areas=("src", "all")):
    for area in areas:
        ps = [p for p in paths if area == "all" or p.startswith(area + "/")]
        ours = [sum(1 for f in by.get(p, []) if pred(f)) for p in ps]
        theirs = [cscount(p, smell) for p in ps]
        both = sum(1 for a, b in zip(ours, theirs) if a or b)
        exact = sum(1 for a, b in zip(ours, theirs) if (a or b) and a == b)
        fp = sum(1 for a, b in zip(ours, theirs) if a and not b)
        fn = sum(1 for a, b in zip(ours, theirs) if b and not a)
        print(f"{smell[:14]:14} {label:22} {area:4} ours {sum(ours):5} cs {sum(theirs):5} "
              f"absdiff {sum(abs(a-b) for a,b in zip(ours,theirs)):5} exact {exact}/{both} "
              f"fileFP {fp} fileFN {fn} r {pearson(ours, theirs):.3f}")


if __name__ == "__main__":
  pass
for v in ["b0", "run_nontry", "runh_all", "runh_nontry", "runhif_all", "runhif_nontry"]:
    for k in (2, 3):
        report(f"{v}>={k}", "Bumpy Road", lambda f, v=v, k=k: f[v] >= k)
for v in ("bo_ifw", "bo_ifwe", "bo_ifwea"):
    report(f"{v}>=2", "Complex Conditional", lambda f, v=v: f[v] >= 2)
import sys as _s
if len(_s.argv) > 2: raise SystemExit
for k in (9, 10, 11, 15):
    report(f"cog>={k}", "Complex Method", lambda f, k=k: f["cog"] >= k)
for k in (4, 5):
    report(f"params>{k}", "Excess Number of Function Arguments", lambda f, k=k: f["params"] > k)
for k in (2,):
    report(f"boolops>={k}", "Complex Conditional", lambda f, k=k: f["boolops"] >= k)
for k in (3, 4):
    report(f"nest>={k}", "Deep, Nested Complexity", lambda f, k=k: f["nest"] >= k)
report("nloc>70", "Large Method", lambda f: f["nloc"] > 70)
report("span>70", "Large Method", lambda f: f["span"] > 70)
report("nloc>60", "Large Method", lambda f: f["nloc"] > 60)
report("brain cog>40,nloc>70", "Brain Method", lambda f: f["cog"] > 40 and f["nloc"] > 70)
report("brain cog>=10,nloc>70,nest>=3", "Brain Method",
       lambda f: f["cog"] >= 10 and f["nloc"] > 70 and f["nest"] >= 3)
