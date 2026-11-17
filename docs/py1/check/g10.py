"""G10: python@1 function metrics except nloc, original vs ruff-formatted copies."""
import os, sys
from cce_code_health import profiles
from cce_code_health.python_backend import analyze
P1 = profiles.get("python@1")
KEYS = [m for m in P1.metrics if m != "nloc"] + ["public"]

def metrics(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in sorted(fn):
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                m = analyze(p, open(p).read(), 3, P1.py_rules, 3, 30)
                out[os.path.relpath(p, root)] = [(x.name, *[getattr(x, k) for k in KEYS]) for x in m.functions]
    return out

orig = metrics(sys.argv[1])
nf = sum(len(v) for v in orig.values())
for other in sys.argv[2:]:
    o = metrics(other)
    diffs = sum(1 for p in orig for a, b in zip(orig[p], o.get(p, [])) if a != b)
    diffs += sum(abs(len(orig[p]) - len(o.get(p, []))) for p in orig)
    print(f"G10 {other.rsplit('/', 1)[-1]}: {nf} functions, {diffs} differences ({', '.join(KEYS)})")
    for p in orig:
        for a, b in zip(orig[p], o.get(p, [])):
            if a != b:
                print("   ", p, a, b)
