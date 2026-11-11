"""Open-source code-health baseline for a Python git repository.

Per function: cyclomatic complexity, NLOC and parameter count (lizard), cognitive
complexity (complexipy), max nesting depth and "bumpy road" bumps (own AST walk).
Per file: smell counts, duplicated-line fraction, a proxy health score (1..10),
and git churn. Hotspots = files ranked by commit count.

The proxy score is NOT CodeScene's Code Health. It imitates its ingredients with
open, documented thresholds so it can be compared against CodeScene later.

Usage: baseline.py REPO OUTDIR [--since DATE]
Writes OUTDIR/functions.json, OUTDIR/files.json, OUTDIR/summary.md and prints
one summary line.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import complexipy
import lizard

# Thresholds (documented, chosen to resemble common CodeScene / Sonar defaults).
COGNITIVE_COMPLEX = 15      # Sonar default for "too complex"
COGNITIVE_BRAIN = 40        # far beyond any reasonable limit
LONG_FUNCTION_NLOC = 70
DEEP_NESTING = 4
BUMPY_ROAD_BUMPS = 3
MANY_ARGS = 6               # excluding self/cls
LARGE_FILE_NLOC = 1000
HUGE_FILE_NLOC = 2000
DUP_WINDOW = 8              # normalised lines per duplicate window
DUP_FILE_FRACTION = 0.10

# Penalty per smell occurrence and cap per category (in score points).
PENALTY = {
    "complex_function": (0.5, 2.0),
    "brain_function": (1.0, 3.0),
    "long_function": (0.3, 1.5),
    "deep_nesting": (0.5, 2.0),
    "bumpy_road": (0.5, 1.5),
    "many_args": (0.2, 1.0),
}

CONTROL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
           ast.AsyncWith, ast.Match)
if hasattr(ast, "TryStar"):
    CONTROL = CONTROL + (ast.TryStar,)
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def nesting_depth(node: ast.AST, depth: int = 0) -> int:
    """Max depth of nested control structures below node (elif does not nest)."""
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, FUNC + (ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, CONTROL):
            is_elif = (isinstance(node, ast.If) and isinstance(child, ast.If)
                       and node.orelse == [child])
            d = depth if is_elif else depth + 1
            best = max(best, nesting_depth(child, d))
        else:
            best = max(best, nesting_depth(child, depth))
    return best


def bumps(func: ast.AST) -> int:
    """Top-level statements in the body that contain nesting depth >= 2."""
    return sum(1 for stmt in func.body
               if isinstance(stmt, CONTROL) and nesting_depth(stmt, 1) >= 2)


def ast_functions(path: Path) -> dict[int, dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return {}
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, FUNC):
            out[node.lineno] = {"nesting": nesting_depth(node),
                                "bumps": bumps(node)}
    return out


def normalise(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith("#") or s.startswith(("import ", "from ")):
        return None
    if s in ('"""', "'''", ")", "]", "}", "),", "],", "},", "else:", "try:"):
        return None
    return " ".join(s.split())


def duplication(files: list[Path], root: Path) -> dict[str, int]:
    """Lines per file that lie in a window repeated elsewhere."""
    seq = {}
    where = defaultdict(list)
    for p in files:
        rel = str(p.relative_to(root))
        lines = []
        for i, raw in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            n = normalise(raw)
            if n is not None:
                lines.append((i, n))
        seq[rel] = lines
        for k in range(len(lines) - DUP_WINDOW + 1):
            h = hashlib.blake2b("\n".join(t for _, t in lines[k:k + DUP_WINDOW]).encode(),
                                digest_size=12).digest()
            where[h].append((rel, k))
    dup_idx = defaultdict(set)
    for locs in where.values():
        if len(locs) < 2:
            continue
        for rel, k in locs:
            dup_idx[rel].update(range(k, k + DUP_WINDOW))
    return {rel: len(idx) for rel, idx in dup_idx.items()}


def churn(repo: Path, since: str | None) -> tuple[Counter, Counter]:
    cmd = ["git", "-C", str(repo), "log", "--format=@%H", "--numstat", "--no-renames"]
    if since:
        cmd.append(f"--since={since}")
    commits, lines = Counter(), Counter()
    for row in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines():
        parts = row.split("\t")
        if len(parts) == 3:
            a, d, f = parts
            commits[f] += 1
            if a != "-":
                lines[f] += int(a) + int(d)
    return commits, lines


def area(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "src":
        return "src"
    return parts[0] if len(parts) > 1 else "(root)"


def main() -> None:
    repo, outdir = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else None
    outdir.mkdir(parents=True, exist_ok=True)
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "*.py"],
                             capture_output=True, text=True, check=True).stdout.split()
    files = [repo / t for t in tracked if (repo / t).is_file()]
    dup = duplication(files, repo)
    commits, churn_lines = churn(repo, since)

    functions, file_rows = [], []
    for p in files:
        rel = str(p.relative_to(repo))
        li = lizard.analyze_file(str(p))
        try:
            cog = {f.line_start: f.complexity for f in complexipy.file_complexity(str(p)).functions}
        except Exception:
            cog = {}
        astf = ast_functions(p)
        smells = Counter()
        for fn in li.function_list:
            params = [x for x in fn.full_parameters if x.strip() not in ("self", "cls")]
            a = astf.get(fn.start_line, {"nesting": 0, "bumps": 0})
            row = {"file": rel, "function": fn.name, "line": fn.start_line,
                   "nloc": fn.nloc, "ccn": fn.cyclomatic_complexity,
                   "cognitive": cog.get(fn.start_line), "params": len(params),
                   "nesting": a["nesting"], "bumps": a["bumps"]}
            c = row["cognitive"] or 0
            row["smells"] = [s for s, hit in (
                ("brain_function", c > COGNITIVE_BRAIN and fn.nloc > LONG_FUNCTION_NLOC),
                ("complex_function", c > COGNITIVE_COMPLEX),
                ("long_function", fn.nloc > LONG_FUNCTION_NLOC),
                ("deep_nesting", a["nesting"] >= DEEP_NESTING),
                ("bumpy_road", a["bumps"] >= BUMPY_ROAD_BUMPS),
                ("many_args", len(params) >= MANY_ARGS)) if hit]
            smells.update(row["smells"])
            functions.append(row)
        penalty = sum(min(w * smells[s], cap) for s, (w, cap) in PENALTY.items())
        dup_frac = dup.get(rel, 0) / max(li.nloc, 1)
        if dup_frac > DUP_FILE_FRACTION:
            penalty += 1.0
        if li.nloc > HUGE_FILE_NLOC:
            penalty += 2.0
        elif li.nloc > LARGE_FILE_NLOC:
            penalty += 1.0
        file_rows.append({"file": rel, "area": area(rel), "nloc": li.nloc,
                          "functions": len(li.function_list),
                          "smells": dict(smells), "dup_fraction": round(dup_frac, 3),
                          "health": round(max(1.0, 10.0 - penalty), 2),
                          "commits": commits.get(rel, 0),
                          "churn_lines": churn_lines.get(rel, 0)})

    (outdir / "functions.json").write_text(json.dumps(functions, indent=1))
    (outdir / "files.json").write_text(json.dumps(file_rows, indent=1))
    write_summary(repo, outdir, file_rows, functions)


def weighted(rows):
    n = sum(r["nloc"] for r in rows)
    return sum(r["health"] * r["nloc"] for r in rows) / n if n else float("nan")


def write_summary(repo, outdir, files, functions):
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    L = [f"# Code-health baseline: {repo.name} @ {head}", "",
         "Proxy score (1..10), NLOC-weighted per area. Not CodeScene's metric.", "",
         "| area | files | NLOC | health | files < 4 | files 4..9 | files = 10 |",
         "|---|---|---|---|---|---|---|"]
    by_area = defaultdict(list)
    for r in files:
        by_area[r["area"]].append(r)
    for a in sorted(by_area, key=lambda a: -sum(r["nloc"] for r in by_area[a])):
        rs = by_area[a]
        L.append(f"| {a} | {len(rs)} | {sum(r['nloc'] for r in rs)} | {weighted(rs):.2f} | "
                 f"{sum(r['health'] < 4 for r in rs)} | "
                 f"{sum(4 <= r['health'] < 10 for r in rs)} | {sum(r['health'] == 10 for r in rs)} |")
    L.append(f"| **all** | {len(files)} | {sum(r['nloc'] for r in files)} | {weighted(files):.2f} | | | |")
    sc = Counter(s for f in functions for s in f["smells"])
    L += ["", "## Function smells", "", "| smell | count |", "|---|---|"]
    L += [f"| {s} | {n} |" for s, n in sc.most_common()]
    L += ["", "## Hotspots (top 20 by commits)", "",
          "| file | commits | churn lines | NLOC | health | main smells |", "|---|---|---|---|---|---|"]
    for r in sorted(files, key=lambda r: -r["commits"])[:20]:
        sm = ", ".join(f"{k}x{v}" for k, v in sorted(r["smells"].items(), key=lambda kv: -kv[1])[:3])
        L.append(f"| {r['file']} | {r['commits']} | {r['churn_lines']} | {r['nloc']} | {r['health']} | {sm} |")
    L += ["", "## Worst functions (top 20 by cognitive complexity)", "",
          "| file:line | function | cognitive | CCN | NLOC | nesting | bumps |", "|---|---|---|---|---|---|---|"]
    for f in sorted(functions, key=lambda f: -(f["cognitive"] or 0))[:20]:
        L.append(f"| {f['file']}:{f['line']} | {f['function']} | {f['cognitive']} | {f['ccn']} | "
                 f"{f['nloc']} | {f['nesting']} | {f['bumps']} |")
    (outdir / "summary.md").write_text("\n".join(L) + "\n")
    src = by_area.get("src", files)
    print(f"BASELINE {repo.name}@{head}: {len(files)} files, {sum(r['nloc'] for r in files)} NLOC, "
          f"health all={weighted(files):.2f} src={weighted(src):.2f}, "
          f"src files <4: {sum(r['health'] < 4 for r in src)}, =10: {sum(r['health'] == 10 for r in src)}/{len(src)}")


if __name__ == "__main__":
    main()
