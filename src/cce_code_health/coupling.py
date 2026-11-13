"""Change coupling from git history: files that change in the same commits."""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from pathlib import PurePosixPath

from . import gitio


def module_of(path: str, depth: int | None) -> str:
    parts = PurePosixPath(path).parent.parts
    return "/".join(parts if depth is None else parts[:depth]) or "."


def commit_file_sets(repo, since: str | None, max_files: int, keep) -> tuple[list[list[str]], int]:
    """Per non-merge commit, the files (current names, filtered by keep) it touched.

    Commits touching more than max_files paths are skipped. One git log call; log runs
    newest first, so a rename old -> new maps old to the name it has at HEAD.
    Returns (file lists, number of skipped commits)."""
    args = ["log", "--no-merges", "-M", "--name-status", "-z", "--format=%x00@%H"]
    if since:
        args.append(f"--since={since}")
    fields = [f.strip("\n") for f in gitio.git(repo, *args).decode().split("\0")]
    alias: dict[str, str] = {}
    commits: list[list[str]] = []
    skipped = 0
    cur: list[str] | None = None

    def close():
        nonlocal skipped
        if cur is not None:
            if len(cur) > max_files:
                skipped += 1
            else:
                commits.append(sorted({p for p in cur if keep(p)}))
    i = 0
    while i < len(fields):
        f = fields[i]
        if not f:
            i += 1
        elif f.startswith("@"):
            close()
            cur = []
            i += 1
        elif f[0] in "RC" and i + 2 < len(fields):
            old, new = fields[i + 1], fields[i + 2]
            name = alias.get(new, new)
            cur.append(name)
            if f[0] == "R":
                alias[old] = name
            i += 3
        elif i + 1 < len(fields):
            p = fields[i + 1]
            cur.append(alias.get(p, p))
            i += 2
        else:
            break
    close()
    return commits, skipped


def compute(commits: list[list[str]], depth: int | None, min_shared: int, min_degree: float) -> dict:
    revs: Counter = Counter()
    shared: Counter = Counter()
    for files in commits:
        revs.update(files)
        shared.update(combinations(files, 2))     # files are sorted, so a < b
    pairs = []
    for (a, b), n in shared.items():
        degree = n / ((revs[a] + revs[b]) / 2)
        if n >= min_shared and degree >= min_degree:
            ma, mb = module_of(a, depth), module_of(b, depth)
            pairs.append({"a": a, "b": b, "shared": n, "revs_a": revs[a], "revs_b": revs[b],
                          "degree": round(degree, 3), "module_a": ma, "module_b": mb,
                          "cross_module": ma != mb})
    pairs.sort(key=lambda p: (-p["degree"], -p["shared"], p["a"], p["b"]))
    mods: Counter = Counter()
    for (a, b), n in shared.items():
        ma, mb = module_of(a, depth), module_of(b, depth)
        if ma != mb:
            mods[tuple(sorted((ma, mb)))] += n
    module_pairs = [{"a": a, "b": b, "shared": n} for (a, b), n in
                    sorted(mods.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"pairs": pairs, "module_pairs": module_pairs}
