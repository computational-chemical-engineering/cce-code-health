"""Git access. ref=None means the working tree (tracked + untracked, not ignored)."""
from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], input=input, capture_output=True)
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def rev(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()


def merge_base(repo: Path, a: str, b: str) -> str:
    return git(repo, "merge-base", a, b).decode().strip()


def ls_files(repo: Path, ref: str | None) -> list[str]:
    if ref is None:
        out = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return sorted({p for p in out.decode().split("\0") if p and (repo / p).is_file()})
    return [p for p in git(repo, "ls-tree", "-r", "-z", "--name-only", ref).decode().split("\0") if p]


def read_files(repo: Path, ref: str | None, paths: list[str]) -> dict[str, str]:
    if not paths:
        return {}
    if ref is None:
        return {p: (repo / p).read_text(encoding="utf-8", errors="replace") for p in paths}
    out = git(repo, "cat-file", "--batch", input="".join(f"{ref}:{p}\n" for p in paths).encode())
    texts, pos = {}, 0
    for p in paths:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        if header[-1] == b"missing":
            pos = nl + 1
            continue
        size = int(header[2])
        texts[p] = out[nl + 1:nl + 1 + size].decode("utf-8", errors="replace")
        pos = nl + 1 + size + 1
    return texts


def changes(repo: Path, base: str, head: str | None) -> list[tuple[str, str | None, str | None]]:
    """(status, base_path, head_path) for files changed between base and head, renames detected."""
    args = ["diff", "--name-status", "-z", "-M", base] + ([head] if head else [])
    fields = git(repo, *args).decode().split("\0")
    out, i = [], 0
    while i < len(fields) and fields[i]:
        st = fields[i][0]
        if st in "RC":
            old, new = fields[i + 1], fields[i + 2]
            out.append((st, old if st == "R" else None, new))
            i += 3
        else:
            p = fields[i + 1]
            out.append((st, None if st == "A" else p, None if st == "D" else p))
            i += 2
    if head is None:
        untracked = git(repo, "ls-files", "-z", "--others", "--exclude-standard").decode().split("\0")
        out += [("A", None, p) for p in untracked if p]
    return out


def commit_counts(repo: Path, since: str | None = None) -> Counter:
    """Commits per file, attributed to the file's current name (follows renames)."""
    args = ["log", "-M", "--name-status", "-z", "--format=%x00@%H"]
    if since:
        args.append(f"--since={since}")
    fields = [f.strip("\n") for f in git(repo, *args).decode().split("\0")]
    alias: dict[str, str] = {}
    counts: Counter = Counter()
    i = 0
    while i < len(fields):
        f = fields[i]
        if not f or f.startswith("@"):
            i += 1
            continue
        st = f[0]
        if st in "RC" and i + 2 < len(fields):
            old, new = fields[i + 1], fields[i + 2]
            cur = alias.get(new, new)
            counts[cur] += 1
            if st == "R":
                alias[old] = cur
            i += 3
        elif i + 1 < len(fields):
            p = fields[i + 1]
            counts[alias.get(p, p)] += 1
            i += 2
        else:
            break
    return counts


def worktree_tree(repo: Path) -> str:
    """Tree sha of the working tree (tracked + untracked, not ignored), without touching the real index."""
    import os  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    index = repo / git(repo, "rev-parse", "--git-path", "index").decode().strip()
    fd, tmp = tempfile.mkstemp(prefix="codehealth-index-")
    os.close(fd)
    try:
        if index.is_file():
            shutil.copyfile(index, tmp)
        else:
            os.unlink(tmp)
        env = {**os.environ, "GIT_INDEX_FILE": tmp}
        for args in (["add", "-A"], ["write-tree"]):
            r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
            if r.returncode != 0:
                raise GitError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout.decode().strip()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
