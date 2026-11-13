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


def nesting_depth(toks: list[str]) -> int:
    """Maximum depth of nested control statements in a token sequence."""
    # Stack entries: "{" plain brace, "C{" braced control body, "C" braceless control body.
    # A control body opened by `do` is marked "do" so its trailing `while (...)` is skipped.
    stack: list[tuple[str, bool]] = []
    best, i, n, after_do = 0, 0, len(toks), False

    def close_statement() -> bool:
        """Pop braceless bodies whose statement just ended; True if a do body ended last."""
        was_do = False
        while stack and stack[-1][0] == "C":
            was_do = stack.pop()[1]
        return was_do

    while i < n:
        t = toks[i]
        if t == "while" and after_do:
            after_do = False
            i = _skip_parens(toks, i + 1)
            continue
        after_do = False
        if t in CONTROL or t in CONTINUATION:
            if i + 1 < n and (t, toks[i + 1]) in (("else", "if"), ("if", "constexpr")):
                i += 1                                   # else if: the level of the if
            i = _skip_parens(toks, i + 1)
            braced = i < n and toks[i] == "{"
            stack.append(("C{" if braced else "C", t == "do"))
            best = max(best, sum(1 for k, _ in stack if k != "{"))
            i += braced
            continue
        if t == "{":
            stack.append(("{", False))
        elif t == "}":
            close_statement()
            kind, was_do = stack.pop() if stack else ("{", False)
            if kind == "C{":
                after_do = close_statement() or was_do
        elif t == ";":
            after_do = close_statement()
        i += 1
    return best


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


def analyze(path: str, source: str) -> FileMetrics:
    lizard = _lizard()
    allows = parse_allows(source, ALLOW_RE, SMELL_NAMES)
    try:
        # The reader is chosen by suffix; lizard's C/C++ reader handles every C++ suffix.
        info = lizard.FileAnalyzer(lizard.get_extensions([])).analyze_source_code(
            path + ".cpp", source)
    except Exception as exc:  # noqa: BLE001 (lizard raises various errors on odd input)
        return FileMetrics(path, 0, [], allows, error=f"lizard: {exc}")
    toks, _ = tokens(source)
    tok_lines = [ln for ln, _ in toks]
    lines = source.splitlines()
    funcs = []
    for f in info.function_list:
        first, brace = _head_lines(lines, f.start_line, f.end_line)
        lo, hi = bisect_left(tok_lines, f.start_line), bisect_right(tok_lines, f.end_line)
        funcs.append(CppFunctionMetrics(
            name=re.sub(r"\s+(?=\W)|(?<=\W)\s+", "", f.name), line=f.start_line,
            end=f.end_line, nloc=f.nloc, ccn=f.cyclomatic_complexity,
            nesting=nesting_depth([t for _, t in toks[lo:hi]]), params=f.parameter_count,
            allows=[a for a in allows if not a.file_level and first - 1 <= a.line <= brace]))
    return FileMetrics(path, info.nloc, funcs, allows)
