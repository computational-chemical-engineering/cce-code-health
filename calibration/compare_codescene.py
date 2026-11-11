"""Compare codehealth with CodeScene per-file Code Health.

  fetch:   compare_codescene.py fetch PROJECT_ID OUT.json   (token: ~/.config/codescene/token)
  compare: compare_codescene.py compare CODESCENE.json SCAN.json

The fetched file keeps only path, LOC, language, score, hotspot flag, change frequency
and rule violations (no author or ownership data).
"""
import json
import math
import sys
import urllib.request
from collections import Counter
from pathlib import Path


def fetch(project, out):
    token = (Path.home() / ".config/codescene/token").read_text().strip()
    files, page, pages = [], 1, 1
    while page <= pages:
        req = urllib.request.Request(
            f"https://api.codescene.io/v2/projects/{project}/analyses/latest/files?page_size=200&page={page}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        d = json.load(urllib.request.urlopen(req))
        pages = d["max_pages"]
        files += d["files"]
        page += 1
    keep = [{"path": f["path"].split("/", 1)[1], "loc": f["lines_of_code"], "language": f["language"],
             "code_health": f["code_health"]["current_score"], "hotspot": f["hotspot"],
             "change_frequency": f["change_frequency"], "violations": f["code_health_rule_violations"]}
            for f in files]
    Path(out).write_text(json.dumps(keep, indent=1))
    print(f"FETCH {len(keep)} files -> {out}")


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))


def compare(cs_path, scan_path):
    cs = {f["path"]: f for f in json.load(open(cs_path)) if f["language"] == "Python"}
    ours = json.load(open(scan_path))["files"]
    common = [p for p in cs if p in ours and isinstance(cs[p]["code_health"], (int, float))]
    print(f"common Python files with a CodeScene score: {len(common)}")
    print(f"{'area':10} {'n':>5} {'rho(health)':>12} {'rho(-debt)':>11}")
    for area in ("", "src/", "tests/", "scripts/", "docs/", "benchmarks/"):
        ps = [p for p in common if p.startswith(area)]
        if len(ps) < 10:
            continue
        c = [cs[p]["code_health"] for p in ps]
        print(f"{area or 'all':10} {len(ps):5} {spearman(c, [ours[p]['health'] for p in ps]):12.2f} "
              f"{spearman(c, [-ours[p]['debt'] for p in ps]):11.2f}")
    smells = Counter()
    for p in common:
        for v in cs[p]["violations"]:
            smells[f"{v['code_smell']} ({v['rule_set']})"] += v.get("count", 1)
    print("CodeScene smells:", ", ".join(f"{s}: {n}" for s, n in smells.most_common()))


if __name__ == "__main__":
    {"fetch": fetch, "compare": compare}[sys.argv[1]](*sys.argv[2:])
