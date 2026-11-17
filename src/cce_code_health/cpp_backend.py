"""C++ backend (experimental): per-function metrics via lizard, nesting computed here.

lizard (optional extra `cpp`) gives each function's qualified name, line span, NLOC,
cyclomatic complexity (CCN: 1 + if, for, while, case, catch, &&, ||, ?) and parameter
count, and the file's NLOC. Lambdas and local classes count towards their enclosing
function, as nested functions do in the Python backend.

Nesting is not taken from lizard's `nd` extension: in lizard 1.26 it counts && and ||
and drops levels (if > for > while > if gives 3). It is computed here from a token
scan of the function's lines, with the Python backend's rules: if, for, while, do,
switch and try each open one level, braced or not; else, catch and the case labels of
a switch stay at the level of their statement; `else if` does not nest. A function
whose first or last line it shares with another function may see the other's tokens.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from .profiles import CPP_METRICS
from .python_backend import FileMetrics, parse_allows

ALLOW_RE = re.compile(r"//\s*codehealth:\s*allow(-file)?\s+([\w,\s]+?)\s*(?:--\s*(.*\S))?\s*$")
SMELL_NAMES = frozenset(CPP_METRICS) | {"all", "large_file", "huge_file", "duplication"}
CONTROL = {"if", "for", "while", "do", "switch", "try"}
CONTINUATION = {"else", "catch"}          # open a body at the level of their statement

_TOKEN = re.compile(r"""
    (?P<comment>//(?:[^\n\\]|\\.)*|/\*.*?\*/)
  | (?P<raw>R"(?P<delim>[^()\\\s]{0,16})\(.*?\)(?P=delim)")
  | (?P<string>(?:u8|[uUL])?"(?:[^"\\\n]|\\.)*")
  | (?P<char>(?:u8|[uUL])?'(?:[^'\\\n]|\\.)*')
  | (?P<number>\.?\d(?:[eEpP][+-]|'?[\w.])*)
  | (?P<word>[A-Za-z_]\w*)
  | (?P<pp>^[ \t]*\#(?:[^\n\\]|\\.)*)
  | (?P<punct>\S)
""", re.VERBOSE | re.DOTALL | re.MULTILINE)


@dataclass
class CppFunctionMetrics:
    name: str          # qualified name, e.g. "ns::Class::method"
    line: int          # lizard's start line (the line of the name)
    end: int
    nloc: int
    ccn: int
    nesting: int
    params: int
    allows: list = field(default_factory=list)

    def value(self, metric: str) -> int:
        return getattr(self, metric)


def tokens(source: str) -> tuple[list[tuple[int, str]], str]:
    """(line, token) for code tokens (no comments or preprocessor lines; literals become
    '0'), and the source with comments blanked (newlines kept)."""
    out, blanked, pos, line = [], [], 0, 1
    for m in _TOKEN.finditer(source):
        kind, text = m.lastgroup, m.group()
        blanked.append(source[pos:m.start()])
        blanked.append(re.sub(r"[^\n]", " ", text) if kind == "comment" else text)
        line += source.count("\n", pos, m.start())
        if kind in ("raw", "string", "char", "number"):
            out.append((line, "0"))
        elif kind in ("word", "punct"):
            out.append((line, text))
        line += text.count("\n")
        pos = m.end()
    blanked.append(source[pos:])
    return out, "".join(blanked)


def blank_comments(source: str) -> str:
    """Source with comments replaced by spaces, for duplication detection."""
    return tokens(source)[1]


def _skip_parens(toks: list[str], i: int) -> int:
    """Index after the balanced (...) starting at toks[i], or i if toks[i] is not '('."""
    if i >= len(toks) or toks[i] != "(":
        return i
    depth = 0
    for j in range(i, len(toks)):
        depth += {"(": 1, ")": -1}.get(toks[j], 0)
        if depth == 0:
            return j + 1
    return len(toks)


_SAME_LEVEL = {("else", "if"), ("if", "constexpr")}   # cpp@0: the second keyword opens no level
_LOOP_TAGS = {"do": "do", "for": "loop", "while": "loop"}


def _constexpr_head(toks: list[str], i: int, after: str | None) -> int | None:
    """cpp@1: if toks[i] starts `if constexpr (...)` or the else of one, the index after its
    keywords (such a body opens no level); otherwise None. The else of an if constexpr
    followed by a runtime `if` is an ordinary else-if (None)."""
    j = i + 1 if toks[i] == "else" and after == "cx" else i
    if toks[j:j + 2] == ["if", "constexpr"]:
        return j + 2
    return j if j > i and toks[j:j + 1] != ["if"] else None


class _NestingScan:
    """State of nesting_depth's token scan. Stack entries (kind, tag): kind "{" plain brace,
    "C{" braced control body, "C" braceless control body; tag "do" for a do body (its
    trailing `while (...)` is skipped), "loop" for a for or while body, "cx" for an
    if-constexpr body or its else (no level), else None. `after` is the tag of the control
    body that ended just before this token."""

    def __init__(self, toks: list[str], flat_constexpr: bool, loop_credit: bool):
        self.toks, self.flat_constexpr, self.loop_credit = toks, flat_constexpr, loop_credit
        self.stack: list[tuple[str, str | None]] = []
        self.best, self.after = 0, None

    def level(self) -> int:
        """Open control bodies on the current path; with loop_credit (cpp@1) one less when
        the path holds two loops or more."""
        tags = [g for k, g in self.stack if k != "{" and g != "cx"]
        return len(tags) - (self.loop_credit and sum(g in ("do", "loop") for g in tags) >= 2)

    def close_statement(self) -> str | None:
        """Pop braceless bodies whose statement just ended; the tag of the last one popped."""
        tag = None
        while self.stack and self.stack[-1][0] == "C":
            tag = self.stack.pop()[1]
        return tag

    def _head(self, i: int) -> tuple[int, str | None] | None:
        """(index of the last keyword of the control head at toks[i], tag), or None."""
        toks, t = self.toks, self.toks[i]
        if self.flat_constexpr and t in ("if", "else"):
            head = _constexpr_head(toks, i, self.after)
            if head is not None:
                return head - 1, "cx"
            if toks[i:i + 3] == ["else", "if", "constexpr"]:
                return i + 2, None          # a runtime else holding an if constexpr: an else-if
        if t not in CONTROL and t not in CONTINUATION:
            return None
        return i + (tuple(toks[i:i + 2]) in _SAME_LEVEL), _LOOP_TAGS.get(t)

    def control(self, i: int) -> int | None:
        """If toks[i] opens a control body, push it and return the index after its head."""
        head = self._head(i)
        if head is None:
            return None
        i, tag = head
        i = _skip_parens(self.toks, i + 1)
        braced = self.toks[i:i + 1] == ["{"]
        self.stack.append(("C{" if braced else "C", tag))
        self.best = max(self.best, self.level())
        return i + braced

    def punct(self, t: str) -> None:
        if t == "{":
            self.stack.append(("{", None))
        elif t == "}":
            self.close_statement()
            kind, tag = self.stack.pop() if self.stack else ("{", None)
            if kind == "C{":
                self.after = self.close_statement() or tag
        elif t == ";":
            self.after = self.close_statement()


def nesting_depth(toks: list[str], flat_constexpr: bool = False,
                  loop_credit: bool = False) -> int:
    """Maximum depth of nested control statements in a token sequence. cpp@1 options:
    flat_constexpr, `if constexpr`, `else if constexpr` and their else open no level;
    loop_credit, a path that holds two loops or more (for, while, do) counts one less."""
    scan, i = _NestingScan(toks, flat_constexpr, loop_credit), 0
    while i < len(toks):
        if toks[i] == "while" and scan.after == "do":
            scan.after = None
            i = _skip_parens(toks, i + 1)
            continue
        j = scan.control(i)
        scan.after = None
        if j is not None:
            i = j
            continue
        scan.punct(toks[i])
        i += 1
    return scan.best


def _head_lines(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """First line of the declaration (template and return-type lines above the name) and
    the line holding the body's opening brace, both 1-based."""
    first = start
    while first > 1:
        prev = lines[first - 2].strip()
        if (not prev or prev.startswith(("//", "/*", "*", "#")) or prev.endswith((";", "{", "}", ":"))):
            break
        first -= 1
    brace = next((k for k in range(start, end + 1) if "{" in lines[k - 1]), start)
    return first, brace


def _lizard():
    try:
        import lizard  # noqa: PLC0415 (optional dependency)
    except ImportError:
        raise RuntimeError("C++ files are in scope but lizard is not installed: "
                           "pip install 'cce-code-health[cpp]'") from None
    return lizard


def analyze(path: str, source: str, profile=None) -> FileMetrics:
    """Metrics of one C++ file. A profile with the "preprocess" rule (cpp@1) measures the
    view of cpp_view (one branch per #if group, `if constexpr` not a decision); lines,
    head lines and suppressions always come from the source, which the view keeps aligned."""
    lizard = _lizard()
    allows = parse_allows(source, ALLOW_RE, SMELL_NAMES)
    rules = profile.cpp_rules if profile is not None else frozenset()
    flat_constexpr, loop_credit = "preprocess" in rules, "loop_credit" in rules
    shown = measured = source
    if flat_constexpr:
        from .cpp_view import select_branches, unmark_constexpr  # noqa: PLC0415 (import cycle)
        shown = select_branches(source)
        measured = unmark_constexpr(shown)
    try:
        # The reader is chosen by suffix; lizard's C/C++ reader handles every C++ suffix.
        info = lizard.FileAnalyzer(lizard.get_extensions([])).analyze_source_code(
            path + ".cpp", measured)
    except Exception as exc:  # noqa: BLE001 (lizard raises various errors on odd input)
        return FileMetrics(path, 0, [], allows, error=f"lizard: {exc}")
    toks, _ = tokens(shown)
    tok_lines = [ln for ln, _ in toks]
    lines = source.splitlines()
    funcs = []
    for f in info.function_list:
        first, brace = _head_lines(lines, f.start_line, f.end_line)
        lo, hi = bisect_left(tok_lines, f.start_line), bisect_right(tok_lines, f.end_line)
        funcs.append(CppFunctionMetrics(
            name=re.sub(r"\s+(?=\W)|(?<=\W)\s+", "", f.name), line=f.start_line,
            end=f.end_line, nloc=f.nloc, ccn=f.cyclomatic_complexity,
            nesting=nesting_depth([t for _, t in toks[lo:hi]], flat_constexpr, loop_credit),
            params=f.parameter_count,
            allows=[a for a in allows if not a.file_level and first - 1 <= a.line <= brace]))
    return FileMetrics(path, info.nloc, funcs, allows)
