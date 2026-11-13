"""Documentation references (opt-in `[docs]` table): extraction, resolution, ratchet.

Docs are Markdown files and notebooks (markdown cells) matched by `[docs] include`/`exclude`.
Candidates are inline code spans and Markdown link targets outside fenced blocks. A candidate
is a reference when it has one of these shapes (else it is ignored):

- path: `[A-Za-z0-9_.-/]` only, after stripping a `word:` prefix, a `#fragment` and a line
  list `:N`, `:N-M`, `:N,K-L`; with a known extension or a trailing `/` it is a hard path,
  otherwise only when it resolves as written. Resolution roots, in order: repo root, the doc's
  directory, `<python_root>/<top package>/`, then any tracked path with that suffix. Skipped:
  absolute or `~` paths, paths leaving the repo, a first component that is no tracked
  directory name, gitignored paths. A bare file name is never a NEW failure.
- anchor: a path with a line list; every line must be <= the file's line count (fragile).
- node: `file.py::name[::name]` (pytest id): the file resolves and defines each name.
- python: `pkg.mod.name[()]` or `pkg.mod:name`, first component a top-level package or module
  under `python_roots`: resolved statically through modules, namespaces (imports followed up
  to 5 hops), classes (body, `self.x =`, in-module bases). Imports from outside the repo,
  star imports, module `__getattr__` and out-of-module bases resolve anything below them.
  A `_private` component makes it fragile.

Ratchet (`compare`), per occurrence: a head occurrence on a line carried over from base (the
doc's own `git diff -U0`) is ROT when it resolved at base and does not at head; it fails
(warns when fragile or `rot = "warn"`, as in refactor and restructure gate modes). An
occurrence on a new or changed line is NEW-broken when the target never resolved at base,
has no history before base (`git log`) and no base doc names it broken: it warns (fails with
`new = "fail"`; plans and briefs name files before they exist).
`records` docs (decision logs, reviews) get the NEW check only. Anchor drift: a carried anchor
into a file this change touched is mapped through that file's hunks; moved or changed lines
warn (fail with `anchors = "fail"`). Docs to review: carried references whose target file
this change touched.
"""
from __future__ import annotations

import ast
import json
import os
import posixpath
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import config, gitio

DOCS_KEYS = {"include", "exclude", "records", "python_roots", "anchors", "new", "docstrings"}
EXTS = {".py", ".md", ".toml", ".cpp", ".hpp", ".h", ".cc", ".cu", ".cuh", ".yml", ".yaml", ".json",
        ".sh", ".ipynb", ".cmake", ".txt", ".cfg", ".ini", ".rst", ".svg", ".html", ".lock", ".csv",
        ".jsonl", ".qmd", ".tex", ".bib", ".in", ".pyi", ".c", ".f90", ".js", ".ts", ".css"}
IMPLICIT = {"__file__", "__version__", "__name__", "__path__", "__doc__", "__all__", "__spec__",
            "__package__", "__loader__", "__dict__", "__module__", "__class__"}
DIFF_OPTS = ("-U0", "--no-ext-diff", "--no-textconv", "--no-color", "--diff-algorithm=myers",
             "--no-indent-heuristic", "--inter-hunk-context=0")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
_COMMENT = re.compile(r"<!--.*?-->")
_PREFIX = re.compile(r"^[A-Za-z][\w-]*:(?=[A-Za-z_.~/])")
_LINES = re.compile(r":(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)$")
_CHARS = re.compile(r"^[A-Za-z0-9_.\-/]+$")
_PY = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+(\(\))?$")
_ENTRY = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------- config

def check_config(d: dict) -> list[str]:
    probs = [f"[docs]: unknown key {k!r}" for k in sorted(set(d) - DOCS_KEYS)]
    for k in ("include", "exclude", "records", "python_roots"):
        if k in d and not (isinstance(d[k], list) and all(isinstance(x, str) for x in d[k])):
            probs.append(f"[docs] {k} must be a list of strings")
    for k in ("anchors", "new"):
        if d.get(k, "warn") not in ("warn", "fail"):
            probs.append(f"[docs] {k} must be \"warn\" or \"fail\"")
    if not isinstance(d.get("docstrings", False), bool):
        probs.append("[docs] docstrings must be true or false")
    if not d.get("include"):
        probs.append("[docs] include is required (globs of Markdown files and notebooks)")
    return probs


def _matches(path: str, pats: list[str]) -> bool:
    return any(config.match(path, p) for p in pats)


def is_doc(path: str, d: dict) -> bool:
    return (path.endswith((".md", ".ipynb")) and _matches(path, d.get("include", []))
            and not _matches(path, d.get("exclude", [])))


def is_record(path: str, d: dict) -> bool:
    return _matches(path, d.get("records", []))


# ---------------------------------------------------------------- extraction

@dataclass(frozen=True)
class Candidate:
    line: int          # 1-based line in the doc (notebooks: line in the cell, see `where`)
    where: str         # "" for Markdown, "cell N" for notebooks
    kind: str          # "code" (inline code span) or "link" (Markdown link target)
    text: str


def _markdown(text: str, where: str = "") -> list[Candidate]:
    out: list[Candidate] = []
    fence: str | None = None
    for i, ln in enumerate(text.split("\n"), 1):          # git line numbering (\n only)
        m = _FENCE.match(ln)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not ln.strip()[len(mark):].strip():
                fence = None
            continue
        if fence is not None:
            continue
        ln = _COMMENT.sub("", ln)
        for cm in _CODE.finditer(ln):
            out.append(Candidate(i, where, "code", cm.group(2).strip()))
        for lm in _LINK.finditer(_CODE.sub("", ln)):
            out.append(Candidate(i, where, "link", lm.group(1)))
    return out


def candidates(path: str, text: str) -> list[Candidate]:
    """Inline code spans and link targets outside fenced blocks; notebooks: markdown cells."""
    if not path.endswith(".ipynb"):
        return _markdown(text)
    try:
        cells = json.loads(text).get("cells", [])
    except (ValueError, AttributeError):
        return []
    out = []
    for n, cell in enumerate(cells, 1):
        if cell.get("cell_type") == "markdown":
            src = cell.get("source", "")
            out += _markdown("".join(src) if isinstance(src, list) else src, f"cell {n}")
    return out


# ---------------------------------------------------------------- the tree at one ref

def _listing(repo: Path, ref: str | None) -> tuple[set[str], set[str]]:
    """(files, submodule paths) at ref; worktree: tracked entries present on disk (symlinks
    included) plus untracked, not ignored. Paths containing a newline are dropped."""
    files, subs = set(), set()
    if ref is None:
        out = gitio.git(repo, "ls-files", "-z", "--cached", "--stage").decode("utf-8", "replace")
        for rec in out.split("\0"):
            if rec:
                meta, path = rec.split("\t", 1)
                if meta.startswith("160000"):
                    subs.add(path)
                elif os.path.lexists(repo / path):
                    files.add(path)
        out = gitio.git(repo, "ls-files", "-z", "--others", "--exclude-standard").decode("utf-8", "replace")
        files |= {p for p in out.split("\0") if p}
    else:
        out = gitio.git(repo, "ls-tree", "-r", "-z", "--full-tree", ref).decode("utf-8", "replace")
        for rec in out.split("\0"):
            if rec:
                meta, path = rec.split("\t", 1)
                (subs if meta.split()[1] == "commit" else files).add(path)
    return {f for f in files if "\n" not in f}, {x for x in subs if "\n" not in x}


class Tree:
    """Tracked files at a ref (worktree: tracked plus untracked, not ignored), read lazily."""

    def __init__(self, repo: Path, ref: str | None, d: dict, arch_roots: list[str] | None = None):
        self.repo, self.ref = repo, ref
        self.files, subs = _listing(repo, ref)
        self.dirs = {posixpath.dirname(p) for p in self.files} | subs
        for p in list(self.dirs):
            while p:
                p = posixpath.dirname(p)
                self.dirs.add(p)
        self.dirnames = {c for p in self.files for c in p.split("/")[:-1]} | {
            c for x in subs for c in x.split("/")}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for p in self.files:
            self.by_name[posixpath.basename(p)].append(p)
        roots = d.get("python_roots") or arch_roots or ["src", "."]
        self.roots = sorted((r.strip("/") if r not in (".", "./", "") else "" for r in roots),
                            key=len, reverse=True)                  # longest root first
        self.packages: dict[str, str] = {}           # top-level name -> root
        for r in self.roots:
            pre = f"{r}/" if r else ""
            for p in self.files:
                if not p.startswith(pre) or not p.endswith(".py"):
                    continue
                rest = p[len(pre):].split("/")
                if len(rest) == 1:
                    self.packages.setdefault(rest[0][:-3], r)
                elif len(rest) == 2 and rest[1] == "__init__.py":
                    self.packages.setdefault(rest[0], r)
        self.known = set(self.packages)              # + the other side's packages (compare)
        self._texts: dict[str, str | None] = {}
        self._mods: dict[str, dict | None] = {}
        self._ignored: dict[str, bool] = {}
        self._cat: subprocess.Popen | None = None
        self.cache: dict = {}                       # (Ref, doc dir) -> resolution

    def text(self, path: str) -> str | None:
        if path not in self._texts:
            self._texts[path] = self._read(path) if path in self.files else None
        return self._texts[path]

    def _read(self, path: str) -> str | None:
        if self.ref is None:
            try:
                return (self.repo / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
        if self._cat is None:
            self._cat = subprocess.Popen(["git", "-C", str(self.repo), "cat-file", "--batch"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self._cat.stdin.write(f"{self.ref}:{path}\n".encode())
        self._cat.stdin.flush()
        header = self._cat.stdout.readline().split()
        if not header or header[-1] == b"missing":
            return None
        data = self._cat.stdout.read(int(header[2]) + 1)[:-1]
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        if self._cat is not None:
            self._cat.stdin.close()
            self._cat.wait()
            self._cat = None

    def preload(self, paths: list[str]) -> None:
        need = [p for p in paths if p not in self._texts and p in self.files]
        self._texts.update({p: None for p in need})
        self._texts.update(gitio.read_files(self.repo, self.ref, need))

    def nlines(self, path: str) -> int:
        t = self.text(path)
        return 0 if t is None else t.count("\n") + (1 if t and not t.endswith("\n") else 0)

    def ignored(self, paths: list[str]) -> set[str]:
        """Repo-relative paths the worktree's .gitignore rules ignore (approximation for other
        refs). Paths leaving the repo are never sent (git would reject the whole batch)."""
        need = [p for p in dict.fromkeys(paths) if p not in self._ignored and p and not p.startswith("..")]
        while need:
            r = subprocess.run(["git", "-C", str(self.repo), "check-ignore", "--no-index", "--stdin", "-z"],
                               input="\0".join(need) + "\0", capture_output=True, text=True)
            if r.returncode in (0, 1):
                hit = set(r.stdout.split("\0"))
                self._ignored.update({p: p in hit for p in need})
                break
            # git rejects the whole batch for one bad path (beyond a symlink, ...): drop it, retry
            bad = [p for p in need if f"'{p}'" in r.stderr]
            if not bad:
                raise gitio.GitError(f"git check-ignore: {r.stderr.strip()}")
            self._ignored.update({p: False for p in bad})
            need = [p for p in need if p not in bad]
        return {p for p in paths if self._ignored.get(p)}


# ---------------------------------------------------------------- classification and resolution

@dataclass(frozen=True)
class Ref:
    kind: str                 # path | anchor | node | python
    text: str                 # the candidate text as written
    target: str               # normalised path or dotted name
    lines: tuple = ()         # anchor line list [(a, b)]
    nodes: tuple = ()         # pytest node names
    hard: bool = True         # path shape alone makes it a reference
    bare: bool = False        # a file name without directory
    fragile: bool = False
    link: bool = False        # Markdown link target: relative to the doc ("/x": repo root)


def _split_lines(s: str) -> tuple[str, tuple]:
    m = _LINES.search(s)
    if not m:
        return s, ()
    spans = []
    for part in m.group(1).split(","):
        a, _, b = part.partition("-")
        spans.append((int(a), int(b or a)))
    return s[:m.start()], tuple(spans)


def classify(text: str, link: bool, tree: Tree) -> Ref | None:
    """The reference shape of a candidate, or None when it is not a reference."""
    raw = text.strip()
    if link:
        if re.match(r"^[A-Za-z][\w+.-]*:", raw) or raw.startswith(("#", "//")):
            return None                                   # URL, mail, fragment-only
        t = raw.split("#", 1)[0].split("?", 1)[0]
        body = t[1:] if t.startswith("/") else t
        if not t or not _CHARS.match(body):
            return None
        ext = posixpath.splitext(body.rstrip("/"))[1]
        return Ref("path", text, t, hard=ext in EXTS or t.endswith("/"), link=True)
    s = _PREFIX.sub("", raw).split("#", 1)[0]
    node_part = ()
    if "::" in s and s.split("::", 1)[0].endswith(".py"):
        s, rest = s.split("::", 1)
        node_part = tuple(re.sub(r"\[.*$", "", n) for n in rest.split("::") if n)
    s, lines = _split_lines(s)
    if not link and s.endswith("()") and _PY.match(s):
        s = s[:-2]
    if not s or not _CHARS.match(s) or s.startswith("/"):
        m = _ENTRY.match(raw)
        if not m or link:
            return None
        s = f"{m.group(1)}.{m.group(2)}"
    ext = posixpath.splitext(s.rstrip("/"))[1]
    first = s.split(".", 1)[0]
    if _PY.match(s) and first in tree.known and ext not in EXTS - {".py"}:
        name = s[:-2] if s.endswith("()") else s
        if not name.endswith(".py"):
            return Ref("python", text, name, fragile=any(p.startswith("_") and p not in IMPLICIT
                                                         for p in name.split(".")))
    if "/" not in s and ext not in EXTS:
        return None
    hard = ext in EXTS or s.endswith("/")
    kind = "node" if node_part else "anchor" if lines else "path"
    return Ref(kind, text, s, lines=lines, nodes=node_part, hard=hard,
               bare="/" not in s.rstrip("/"), fragile=kind == "anchor")


def _dot(p: str) -> str:
    while p.startswith("./"):
        p = p[2:]
    return p


def _resolve_path(ref: Ref, doc: str, tree: Tree) -> tuple[list[str], str, tuple]:
    """(matching paths, skip reason or '', repo-relative candidates for the gitignore check)."""
    p = ref.target
    is_dir = p.endswith("/")
    docdir = posixpath.dirname(doc)
    if ref.link:                          # GitHub semantics: relative to the doc, "/" = repo root
        t = posixpath.normpath(p.lstrip("/") if p.startswith("/") else posixpath.join(docdir, p))
        if t.startswith(".."):
            return [], "outside the repo", ()
        if (t in tree.dirs) if is_dir else (t in tree.files or t in tree.dirs):
            return [t], "", ()
        return [], "maybe-ignored", (t,)
    p = p.rstrip("/")
    tries = [posixpath.normpath(p), posixpath.normpath(posixpath.join(docdir, p))]
    tries += [posixpath.normpath(posixpath.join(r, pkg, p) if r else posixpath.join(pkg, p))
              for pkg, r in tree.packages.items()]
    tries = [t for t in tries if not t.startswith("..")]
    pool = tree.dirs if is_dir else tree.files
    for t in tries:
        if t in pool:
            return [t], "", ()
    if not is_dir and ref.kind == "path":
        for t in tries[:2]:
            if t in tree.dirs:                       # a directory named without trailing slash
                return [t], "", ()
    if not is_dir:
        if ref.bare:
            hits = tree.by_name.get(p, [])
        else:
            hits = [f for f in tree.by_name.get(posixpath.basename(p), []) if f.endswith("/" + _dot(p))]
        if hits:
            return sorted(hits), "", ()
    elif any(d.endswith("/" + _dot(p)) for d in tree.dirs):
        return ["(dir)"], "", ()
    if posixpath.normpath(posixpath.join(docdir, p)).startswith(".."):
        return [], "outside the repo", ()
    if not ref.bare and _dot(p).split("/")[0] not in tree.dirnames | {".", ".."}:
        return [], "not a repo directory", ()
    return [], "maybe-ignored", tuple(dict.fromkeys(tries[:2]))


def _defines(text: str, name: str) -> bool:
    """A def, class or assignment of name; `name…`, `name...`, `name*` match as a prefix."""
    name = re.sub(r"\(.*$", "", name).strip()
    stem = re.sub(r"(…|\.\.\.|\*)$", "", name)
    word = re.escape(stem) + (r"\w*" if stem != name else r"\b")
    return re.search(rf"^\s*(?:(?:async\s+)?def|class)\s+{word}|^\s*{word}\s*(?::[^=\n]*)?=",
                     text, re.M) is not None


# ---- python names

def _module_file(tree: Tree, parts: list[str]) -> str | None:
    root = tree.packages.get(parts[0])
    if root is None:
        return None
    base = "/".join(([root] if root else []) + parts)
    for cand in (base + ".py", base + "/__init__.py"):
        if cand in tree.files:
            return cand
    return None


def _ns_dir(tree: Tree, parts: list[str]) -> bool:
    """A namespace package directory (no __init__.py) under the package's root."""
    root = tree.packages.get(parts[0])
    return root is not None and "/".join(([root] if root else []) + parts) in tree.dirs


def _resolve_nsdir(tree: Tree, parts: list[str], rest: list[str], hops: int) -> bool:
    if not rest:
        return True
    nxt = parts + [rest[0]]
    f = _module_file(tree, nxt)
    if f:
        return _resolve_in(tree, f, rest[1:], hops)
    return _ns_dir(tree, nxt) and _resolve_nsdir(tree, nxt, rest[1:], hops)


def _module_parts(tree: Tree, path: str) -> list[str]:
    for r in tree.roots:
        pre = f"{r}/" if r else ""
        if path.startswith(pre):
            parts = path[len(pre):-3].split("/")
            return parts[:-1] if parts[-1] == "__init__" else parts
    return path[:-3].split("/")


def _namespace(tree: Tree, path: str) -> dict | None:
    if path in tree._mods:
        return tree._mods[path]
    text = tree.text(path)
    ns = None
    if text is not None:
        try:
            mod = ast.parse(text)
        except (SyntaxError, ValueError):
            mod = None
            ns = {"__names__": {}, "__open__": True}       # unparsable: resolve leniently
        if mod is not None:
            ns = {"__names__": {}, "__open__": False}
            here = _module_parts(tree, path)
            pkg = here if path.endswith("__init__.py") else here[:-1]
            _bind_body(mod.body, ns, pkg)
    tree._mods[path] = ns
    return ns


def _bind_targets(t: ast.AST, names: dict) -> None:
    for n in ast.walk(t):
        if isinstance(n, ast.Name):
            names[n.id] = ("value", None)


def _bind_body(body: list, ns: dict, pkg: list[str]) -> None:
    names = ns["__names__"]
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names[node.name] = ("def", None)
            if node.name == "__getattr__":
                ns["__open__"] = True
        elif isinstance(node, ast.ClassDef):
            names[node.name] = ("class", node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                _bind_targets(t, names)
        elif type(node).__name__ == "TypeAlias":
            _bind_targets(node.name, names)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            if not isinstance(node, ast.While):
                _bind_targets(node.target, names)
            _bind_body(node.body + node.orelse, ns, pkg)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    names[a.asname] = ("module", a.name.split("."))
                else:
                    names[a.name.split(".")[0]] = ("module", a.name.split(".")[:1])
        elif isinstance(node, ast.ImportFrom):
            src = (pkg[:len(pkg) - node.level + 1] if node.level else []) + (node.module.split(".") if node.module else [])
            for a in node.names:
                if a.name == "*":
                    ns.setdefault("__star__", []).append(src)
                else:
                    names[a.asname or a.name] = ("from", (src, a.name))
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith)) or type(node).__name__ == "TryStar":
            for sub in ("body", "orelse", "finalbody"):
                _bind_body(getattr(node, sub, []) or [], ns, pkg)
            for h in getattr(node, "handlers", []):
                _bind_body(h.body, ns, pkg)


def _resolve_module(tree: Tree, parts: list[str], rest: list[str], hops: int) -> bool:
    if hops > 5:
        return True
    for k in range(len(parts), 0, -1):
        f = _module_file(tree, parts[:k])
        if f:
            return _resolve_in(tree, f, parts[k:] + rest, hops)
        if _ns_dir(tree, parts[:k]):
            return _resolve_nsdir(tree, parts[:k], parts[k:] + rest, hops)
    return parts[0] not in tree.known               # outside the repo: anything resolves


def _resolve_in(tree: Tree, path: str, rest: list[str], hops: int) -> bool:
    if not rest:
        return True
    ns = _namespace(tree, path)
    if ns is None:
        return False
    name = rest[0]
    if name in IMPLICIT:
        return True
    entry = ns["__names__"].get(name)
    if entry is None:
        if path.endswith("__init__.py"):
            sub = _module_parts(tree, path) + [name]
            if _module_file(tree, sub):
                return _resolve_module(tree, sub, rest[1:], hops)
            if _ns_dir(tree, sub):
                return _resolve_nsdir(tree, sub, rest[1:], hops)
        if ns["__open__"]:
            return True
        for src in ns.get("__star__", []):
            if not src or src[0] not in tree.known or _resolve_module(tree, src, rest, hops + 1):
                return True
        return False
    kind, data = entry
    if kind == "class":
        return _resolve_class(tree, data, rest[1:], ns)
    if kind == "module":
        return _resolve_module(tree, data, rest[1:], hops + 1)
    if kind == "from":
        src, attr = data
        return _resolve_module(tree, src, [attr] + rest[1:], hops + 1)
    return True                                       # function or value: cannot look deeper


def _class_body(body: list) -> list:
    """Class-body statements with if/try/with branches inlined."""
    out = []
    for n in body:
        if isinstance(n, (ast.If, ast.Try, ast.With)) or type(n).__name__ == "TryStar":
            for sub in ("body", "orelse", "finalbody"):
                out += _class_body(getattr(n, sub, []) or [])
            for h in getattr(n, "handlers", []):
                out += _class_body(h.body)
        else:
            out.append(n)
    return out


def _class_members(cls: ast.ClassDef) -> set[str]:
    out = set()
    for n in _class_body(cls.body):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    out.add(t.id)
    for n in ast.walk(cls):
        if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store) and \
                isinstance(n.value, ast.Name) and n.value.id in ("self", "cls"):
            out.add(n.attr)
    return out


def _resolve_class(tree: Tree, cls: ast.ClassDef, rest: list[str], ns: dict, depth: int = 0) -> bool:
    """Members of the class, its nested classes and its in-module bases; a base from elsewhere
    (or a metaclass) resolves anything; `object` adds nothing."""
    if not rest or rest[0] in IMPLICIT or depth > 5:
        return True
    if rest[0] in _class_members(cls):
        sub = next((n for n in _class_body(cls.body) if isinstance(n, ast.ClassDef) and n.name == rest[0]), None)
        return _resolve_class(tree, sub, rest[1:], ns, depth + 1) if sub is not None else True
    if cls.keywords:
        return True
    for b in cls.bases:
        if isinstance(b, ast.Name) and b.id == "object":
            continue
        entry = ns["__names__"].get(b.id) if isinstance(b, ast.Name) else None
        if entry is None or entry[0] != "class":
            return True                               # base outside this module: not followed
        if _resolve_class(tree, entry[1], rest, ns, depth + 1):
            return True
    return False


def _resolve_python(ref: Ref, tree: Tree) -> bool:
    parts = ref.target.split(".")
    return _resolve_module(tree, parts, [], 0)


# ---- one occurrence

@dataclass
class Occ:
    doc: str
    line: int
    where: str
    ref: Ref
    status: str               # ok | broken | skip
    reason: str = ""
    paths: tuple = ()


def resolve(ref: Ref, doc: str, tree: Tree) -> tuple[str, str, tuple]:
    """(status, reason, matched paths); cached per (reference, doc directory)."""
    key = (ref, posixpath.dirname(doc))
    if key not in tree.cache:
        tree.cache[key] = _resolve(ref, doc, tree)
    st = tree.cache[key]
    if st[0] == "unsettled":                       # outside analyse: settle now
        st = ("skip", "gitignored", ()) if tree.ignored(list(st[2])) else _unignored(ref)
        tree.cache[key] = st
    return st


def _unignored(ref: Ref) -> tuple[str, str, tuple]:
    if not ref.hard:
        return "skip", "not found", ()
    return "broken", "no such file" if not ref.target.endswith("/") else "no such directory", ()


def _resolve(ref: Ref, doc: str, tree: Tree) -> tuple[str, str, tuple]:
    if ref.kind == "python":
        return ("ok", "", ()) if _resolve_python(ref, tree) else ("broken", "name not found", ())
    hits, skip, cands = _resolve_path(ref, doc, tree)
    if not hits:
        if skip == "maybe-ignored":
            return "unsettled", skip, cands
        if skip:
            return "skip", skip, ()
        return _unignored(ref)
    if ref.kind == "anchor":
        hits_ok = [h for h in hits if h != "(dir)" and
                   all(b <= tree.nlines(h) for _, b in ref.lines)]
        if not hits_ok:
            n = max((tree.nlines(h) for h in hits if h != "(dir)"), default=0)
            return "broken", f"line {max(b for _, b in ref.lines)} beyond end ({n} lines)", tuple(hits)
        return "ok", "", tuple(hits_ok)
    if ref.kind == "node":
        for h in hits:
            t = tree.text(h) or ""
            if all(_defines(t, n) for n in ref.nodes):
                return "ok", "", (h,)
        return "broken", f"no {'::'.join(ref.nodes)} in {hits[0]}", tuple(hits)
    return "ok", "", tuple(hits)


def analyse_doc(doc: str, text: str, tree: Tree, settle: bool = True) -> list[Occ]:
    out = []
    for c in candidates(doc, text):
        ref = classify(c.text, c.kind == "link", tree)
        if ref is None:
            continue
        key = (ref, posixpath.dirname(doc))
        if key not in tree.cache:
            tree.cache[key] = _resolve(ref, doc, tree)
        st, why, paths = tree.cache[key]
        out.append(Occ(doc, c.line, c.where, ref, st, why, paths))
    if settle:
        _settle(out, tree)
    return out


def _settle(occs: list[Occ], tree: Tree) -> None:
    """Resolve the gitignore question for unresolved paths in one git call."""
    todo = [o for o in occs if o.status == "unsettled"]
    ign = tree.ignored(sorted({c for o in todo for c in o.paths}))
    for o in todo:
        st = ("skip", "gitignored", ()) if any(c in ign for c in o.paths) else _unignored(o.ref)
        o.status, o.reason, o.paths = st
        tree.cache[(o.ref, posixpath.dirname(o.doc))] = st


@dataclass
class Result:
    docs: dict = field(default_factory=dict)          # doc -> [Occ]
    tree: Tree | None = None


def analyse(repo: Path, ref: str | None, cfg: config.Config, d: dict | None = None) -> Result | None:
    d = cfg.docs if d is None else d
    if not d:
        return None
    probs = check_config(d)
    if probs:
        raise ValueError(f"{cfg.source}: " + "; ".join(probs))
    return analyse_tree(Tree(repo, ref, d, (cfg.architecture or {}).get("roots")), d)


def analyse_tree(tree: Tree, d: dict) -> Result:
    docs = sorted(p for p in tree.files if is_doc(p, d))
    tree.preload(docs)
    res = Result(tree=tree)
    for doc in docs:
        res.docs[doc] = analyse_doc(doc, tree.text(doc) or "", tree, settle=False)
    _settle([o for occs in res.docs.values() for o in occs], tree)
    return res


def stats(res: Result, d: dict) -> dict:
    by = Counter()
    for doc, occs in res.docs.items():
        rec = "record" if is_record(doc, d) else "living"
        for o in occs:
            by[(rec, o.ref.kind, o.status, o.reason if o.status != "ok" else "")] += 1
    return by


# ---------------------------------------------------------------- diffs and line maps

def hunks(repo: Path, base: str, head: str | None, bpath: str | None, hpath: str | None) -> list[tuple]:
    """[(old_start, old_len, new_start, new_len)] of one file between base and head."""
    paths = [p for p in dict.fromkeys([bpath, hpath]) if p]
    args = ["diff", *DIFF_OPTS, "-M", base] + ([head] if head else []) + ["--"] + paths
    out = []
    for ln in gitio.git(repo, *args).decode(errors="replace").splitlines():
        m = _HUNK.match(ln)
        if m:
            a, b, c, e = m.groups()
            out.append((int(a), 1 if b is None else int(b), int(c), 1 if e is None else int(e)))
    return out


def map_line(n: int, hs: list[tuple], forward: bool = True) -> int | None:
    """Line n of one side mapped to the other side; None when it lies in a changed hunk."""
    shift = 0
    for h in hs:
        a, la, c, lc = h if forward else (h[2], h[3], h[0], h[1])
        if la == 0:                      # pure insertion after line a
            if a < n:
                shift += lc
        elif n < a:
            continue
        elif n <= a + la - 1:
            return None
        else:
            shift += lc - la
    return n + shift


def range_changed(a: int, b: int, hs: list[tuple]) -> bool:
    for oa, ol, _, _ in hs:
        if ol == 0 and a <= oa < b:
            return True
        if ol and oa <= b and a <= oa + ol - 1:
            return True
    return False


# ---------------------------------------------------------------- ratchet

@dataclass
class Outcome:
    failures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    review: list = field(default_factory=list)       # (doc, target) to review
    notes: list = field(default_factory=list)


def _never_existed(repo: Path, base: str, path: str) -> bool:
    """No commit up to base touched a file at this path or with this path suffix."""
    p = _dot(path.rstrip("/").lstrip("/"))
    try:
        return not gitio.git(repo, "log", "-1", "--format=%H", base, "--", p, f":(glob)**/{p}",
                             f":(glob)**/{p}/**").strip()
    except gitio.GitError:
        return True


def _never_mentioned(repo: Path, base: str, word: str) -> bool:
    """No commit up to base added or removed this identifier in a Python file (pickaxe)."""
    try:
        return not gitio.git(repo, "log", "-1", "--format=%H", "-S", word, base, "--", "*.py").strip()
    except gitio.GitError:
        return True


def _fragile(o: Occ) -> bool:
    """Anchors are fragile only in their line part; a missing file is a path break."""
    return o.reason.startswith("line") if o.ref.kind == "anchor" else o.ref.fragile


def compare(repo: Path, base: str, head: str | None, cfg: config.Config, rot: str = "fail") -> Outcome | None:
    """Ratchet base against head (None: worktree) with the base config's [docs]."""
    d = cfg.docs
    if not d:
        return None
    probs = check_config(d)
    if probs:
        raise ValueError(f"{cfg.source}: " + "; ".join(probs))
    roots = (cfg.architecture or {}).get("roots")
    tb, th = Tree(repo, base, d, roots), Tree(repo, head, d, roots)
    tb.known |= th.known                 # a package renamed or removed still classifies
    th.known |= tb.known
    try:
        return _compare(repo, base, head, d, rot, analyse_tree(tb, d), analyse_tree(th, d))
    finally:
        tb.close()
        th.close()


def _compare(repo: Path, base: str, head: str | None, d: dict, rot: str, before: Result,
             after: Result) -> Outcome:
    ch = gitio.changes(repo, base, head)
    touched = {c[2]: c[1] for c in ch if c[2]}
    touched_base = {c[1] for c in ch if c[1]}
    renamed_from = {c[2]: c[1] for c in ch if c[1] and c[2]}
    base_broken = {o.ref.text for occs in before.docs.values() for o in occs if o.status == "broken"}
    out = Outcome()
    anchor_moves: dict[str, list[str]] = defaultdict(list)
    target_hunks: dict[str, list] = {}
    for doc, occs in after.docs.items():
        record = is_record(doc, d)
        bdoc = renamed_from.get(doc, doc) if doc in touched else doc
        b_occs = before.docs.get(bdoc, [])
        doc_hs = hunks(repo, base, head, bdoc, doc) if doc in touched and bdoc in before.docs else None
        b_index = defaultdict(list)
        for o in b_occs:
            b_index[(o.where, o.line, o.ref.text)].append(o)
        b_texts = {o.ref.text for o in b_occs}
        for o in occs:
            if bdoc not in before.docs:
                bline = None
            elif doc_hs is None:
                bline = o.line
            elif doc.endswith(".ipynb"):
                bline = o.line if b_index.get((o.where, o.line, o.ref.text)) else None
            else:
                bline = map_line(o.line, doc_hs, forward=False)
            prev = b_index.get((o.where, bline, o.ref.text)) if bline is not None else None
            where = f"{doc}:{o.line}" + (f" ({o.where})" if o.where else "")
            if prev:
                p = prev[0]
                if not record and p.status == "ok" and o.status != "ok":
                    msg = f"docs: {where} `{o.ref.text}` no longer resolves ({o.reason or 'not found'})"
                    (out.warnings if _fragile(o) or rot == "warn" else out.failures).append(msg)
                elif not record and o.status == "ok" and p.status == "ok":
                    _drift_and_review(repo, base, head, o, p, touched, touched_base, before.tree,
                                      after.tree, target_hunks, anchor_moves, out)
                continue
            if (not record and o.status != "ok" and o.ref.text in b_texts
                    and resolve(o.ref, bdoc, before.tree)[0] == "ok"):
                # an edited line (or a renumbered notebook cell) that kept a reference the doc
                # already had: the reference rotted all the same
                msg = f"docs: {where} `{o.ref.text}` no longer resolves ({o.reason or 'not found'})"
                (out.warnings if _fragile(o) or rot == "warn" else out.failures).append(msg)
                continue
            if o.status != "broken":
                continue
            if o.ref.bare or o.ref.text in base_broken:
                out.notes.append(f"{where} `{o.ref.text}` does not resolve")
                continue
            ok_at_base = resolve(o.ref, bdoc, before.tree)[0] == "ok"
            if ok_at_base or _fragile(o):
                new_fail = False
            elif o.ref.kind == "python":
                new_fail = _never_mentioned(repo, base, o.ref.target.rsplit(".", 1)[-1])
            elif o.ref.kind == "node" and o.paths:
                new_fail = _never_mentioned(repo, base, o.ref.nodes[-1])
            else:
                new_fail = _never_existed(repo, base, o.ref.target)
            if new_fail:
                (out.failures if d.get("new", "warn") == "fail" else out.warnings).append(f"docs: {where} new reference `{o.ref.text}` does not resolve "
                                    f"({o.reason})")
            else:
                out.notes.append(f"{where} `{o.ref.text}` does not resolve ({o.reason})")
    sev = out.failures if d.get("anchors", "warn") == "fail" else out.warnings
    for target, items in sorted(anchor_moves.items()):
        sev.append(f"docs: {len(items)} anchor(s) into {target} drifted: " + "; ".join(items[:4])
                   + (" ..." if len(items) > 4 else ""))
    return out


def _drift_and_review(repo, base, head, o: Occ, p: Occ, touched, touched_base, btree, htree,
                      target_hunks, anchor_moves, out) -> None:
    targets = [t for t in o.paths if t in touched and t != "(dir)"]
    if o.ref.kind == "python":
        mod = _python_file(htree, o.ref.target)
        targets = [mod] if mod and mod in touched else []
    if not targets:
        return
    out.review.append((o.doc, targets[0]))
    if o.ref.kind != "anchor" or len(o.paths) != 1 or len(p.paths) != 1:
        return
    tgt = targets[0]
    btgt = touched.get(tgt) or tgt
    if btgt not in btree.files:
        return
    if tgt not in target_hunks:
        target_hunks[tgt] = hunks(repo, base, head, btgt, tgt)
    hs = target_hunks[tgt]
    for a, b in o.ref.lines:
        if a == b:
            n = map_line(a, hs)
            if n == a:
                continue
            if n is None:
                n = _unique_match(btree.text(btgt), htree.text(tgt), a)
            anchor_moves[tgt].append(f"{o.doc}:{o.line} :{a} -> " + (f":{n}" if n else "changed code"))
        elif range_changed(a, b, hs):
            anchor_moves[tgt].append(f"{o.doc}:{o.line} :{a}-{b} -> changed code")
        else:
            na, nb = map_line(a, hs), map_line(b, hs)
            if (na, nb) != (a, b):
                anchor_moves[tgt].append(f"{o.doc}:{o.line} :{a}-{b} -> :{na}-{nb}")


def _unique_match(btext: str | None, htext: str | None, n: int) -> int | None:
    if not btext or not htext:
        return None
    blines = btext.split("\n")
    if n > len(blines) or not blines[n - 1].strip():
        return None
    want = blines[n - 1].strip()
    hits = [i for i, ln in enumerate(htext.split("\n"), 1) if ln.strip() == want]
    return hits[0] if len(hits) == 1 else None


def _python_file(tree: Tree, name: str) -> str | None:
    parts = name.split(".")
    for k in range(len(parts), 0, -1):
        f = _module_file(tree, parts[:k])
        if f:
            return f
    return None
