"""Documentation staleness from history (`codehealth docs --stale`, and co-change rows in `diff`).

A reference that still resolves can describe code that has moved on. Two signals, both from
`git log` (renames followed to today's names, merges and commits touching more than
`max_files` paths skipped):

- referenced: for each living doc, the code and data files it references (other docs are not
  counted: a doc pointing at a changing doc is not stale code-wise) (paths, anchors, test ids, Python
  names resolved to their module file) and how many commits changed each of them after the
  doc's own last change;
- co-change: files that historically change together with the doc (at least `min_shared`
  shared commits, degree shared / mean(revisions) >= `min_degree`) but are not referenced,
  changed since the doc's last change.

`stale_docs` ranks living docs by commits to referenced files since the doc's last change.
`cochange_review` gives the docs a diff should look at because files they historically change
with were touched and the doc was not (informational, like "docs to review").
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from . import docs as docsmod
from .coupling import commit_file_sets

MAX_FILES = 30
DOC_EXTS = (".md", ".rst", ".txt", ".ipynb")   # docs referencing docs: not a code-staleness signal
MIN_SHARED = 3
MIN_DEGREE = 0.5


def history(repo: Path, max_files: int = MAX_FILES, since: str | None = None) -> list[list[str]]:
    """Per commit, newest first, the files it touched under today's names."""
    commits, _ = commit_file_sets(repo, since, max_files, lambda p: True)
    return commits


def _cochange(commits: list[list[str]], docs: set[str], min_shared: int, min_degree: float) -> dict:
    """doc -> {file: (shared, degree)} for files that change with the doc."""
    revs: Counter = Counter()
    shared: Counter = Counter()
    for files in commits:
        revs.update(files)
        ds = [f for f in files if f in docs]
        if ds:
            for d in ds:
                for f in files:
                    if f != d:
                        shared[(d, f)] += 1
    out: dict = defaultdict(dict)
    for (d, f), n in shared.items():
        degree = n / ((revs[d] + revs[f]) / 2)
        if n >= min_shared and degree >= min_degree:
            out[d][f] = (n, round(degree, 2))
    return out


def referenced_files(res, tree) -> dict[str, set[str]]:
    """doc -> files its resolving references point at (directories excluded)."""
    out: dict[str, set[str]] = {}
    for doc, occs in res.docs.items():
        fs = set()
        for o in occs:
            if o.status != "ok":
                continue
            if o.ref.kind == "python":
                f = docsmod._python_file(tree, o.ref.target.replace(":", ".").removesuffix("()"))
                if f:
                    fs.add(f)
            else:
                fs.update(p for p in o.paths if p != "(dir)" and p != doc)
        out[doc] = {f for f in fs if not f.endswith(DOC_EXTS)}
    return out


def stale_docs(repo: Path, cfg, d: dict | None = None, commits: list[list[str]] | None = None,
               min_shared: int = MIN_SHARED, min_degree: float = MIN_DEGREE) -> list[dict]:
    """Living docs ranked by commits to their referenced files since the doc last changed."""
    d = cfg.docs if d is None else d
    res = docsmod.analyse(repo, None, cfg, d)
    if res is None:
        return []
    try:
        refs = referenced_files(res, res.tree)
    finally:
        res.tree.close()
    living = {doc for doc in res.docs if not docsmod.is_record(doc, d)}
    commits = history(repo) if commits is None else commits
    touched: dict[str, list[int]] = defaultdict(list)          # file -> commit indices (0 = newest)
    for i, files in enumerate(commits):
        for f in files:
            touched[f].append(i)
    co = _cochange(commits, living, min_shared, min_degree)
    rows = []
    for doc in sorted(living):
        last = touched[doc][0] if touched.get(doc) else None
        if last is None:
            continue                                            # never committed (new file)

        def since(f):
            return sum(1 for i in touched.get(f, []) if i < last)
        ref_changes = {f: since(f) for f in refs.get(doc, set())}
        ref_changes = {f: n for f, n in ref_changes.items() if n}
        partners = {f: (since(f), *co[doc][f]) for f in co.get(doc, {})
                    if f not in refs.get(doc, set()) and not f.endswith(DOC_EXTS)}
        partners = {f: v for f, v in partners.items() if v[0]}
        if not ref_changes and not partners:
            continue
        rows.append({"doc": doc, "doc_commits_ago": last,
                     "referenced_commits_since": sum(ref_changes.values()),
                     "referenced": dict(sorted(ref_changes.items(), key=lambda kv: -kv[1])),
                     "cochange": {f: {"commits_since": n, "shared": s, "degree": g}
                                  for f, (n, s, g) in sorted(partners.items(), key=lambda kv: -kv[1][0])}})
    rows.sort(key=lambda r: (-r["referenced_commits_since"], -sum(v["commits_since"] for v in r["cochange"].values()),
                             r["doc"]))
    return rows


def cochange_review(repo: Path, cfg, touched: set[str], docs: list[str],
                    commits: list[list[str]] | None = None,
                    min_shared: int = MIN_SHARED, min_degree: float = MIN_DEGREE) -> list[tuple[str, str, int, float]]:
    """(doc, touched file, shared, degree) for living docs not touched here whose co-change
    partner was touched."""
    d = cfg.docs or {}
    living = {x for x in docs if not docsmod.is_record(x, d)} - touched
    if not living or not touched:
        return []
    commits = history(repo) if commits is None else commits
    co = _cochange(commits, living, min_shared, min_degree)
    out = []
    for doc, partners in co.items():
        for f, (n, g) in partners.items():
            if f in touched and not f.endswith(DOC_EXTS):
                out.append((doc, f, n, g))
    return sorted(out, key=lambda x: (-x[3], -x[2], x[0], x[1]))


