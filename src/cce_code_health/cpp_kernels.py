"""cpp@1 rule (3): kernel lambdas are scored as functions of their own.

A kernel lambda is a macro lambda (a name in the profile's kernel_macros, e.g. KOKKOS_LAMBDA,
then a balanced (...)) or a bracket lambda (`[...]` right after the `(` or a `,` of a call to a
name in the profile's kernel_calls, e.g. parallel_for), then at most 12 specifier tokens, then
`{`. Only lambdas inside a function lizard reports are split. The parent is scored with the
kernel bodies blanked; each lambda is scored as `void L<params> <body>` with the bodies of the
kernels inside it blanked. The sum of CCN - 1 over a file is unchanged by the split.

A lambda is named `<enclosing>::<label>` when the call it is passed to starts with a string
literal (the Kokkos label), else `<enclosing>::lambda#<k>` (k counts the enclosing function's
unlabelled kernels from 1); a repeated name gets `#2`, `#3`. Never a line number: the gate
matches brain functions by name. Design: docs/CPP1_LAYERS.md, rule (3).

Rule (4), captures: the explicit entries of the capture list (`this` and `*this` count one),
plus, with a default capture (`[=]`, `[&]`, every kernel macro), the names the body uses that
are parameters or earlier locals of the enclosing function and are not listed explicitly,
not the lambda's parameters or the body's own locals, and not after `.`, `->` or `::` or
before `::`. The names of enclosing kernels' parameters count as the enclosing function's.
A macro in the profile's kernel_this_macros (KOKKOS_CLASS_LAMBDA) captures `*this` too. Locals are found by a token heuristic
(`declared`): `x * y(` reads as a declaration, members reached through an implicit this are
not seen.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass

from .cpp_backend import _TOKEN

MAX_SPECIFIERS = 12
_KEYWORDS = frozenset("""alignas alignof and asm auto bool break case catch char class const constexpr
const_cast continue decltype default delete do double else enum explicit extern false float for
friend goto if inline int long mutable namespace new noexcept not nullptr operator or private
protected public return short signed sizeof static static_assert static_cast struct switch template
this throw true try typedef typeid typename union unsigned using virtual void volatile while
size_t""".split())
_NOT_DECL_AFTER = frozenset({"return", "else", "case", "goto", "throw", "new", "delete", "co_return",
                             "typedef", "using", "do"})
_DECL_BEFORE = frozenset({"=", ";", "(", "{", ",", ":", "[", ")"})
_WORD = re.compile(r"[A-Za-z_]\w*$")
_TYPE_STOP = frozenset({";", "{", "}", "(", ")", "="})        # a template scan stops here
_STMT_OPEN = frozenset({";", "{", "}", "(", ","})             # a declaration's type follows these
_QUALIFIERS = frozenset({"const", "constexpr", "static", "volatile", "typename", "inline",
                         "thread_local", "register"})
_CLOSE = {")": "(", "]": "[", "}": "{"}


@dataclass(frozen=True)
class Tok:
    start: int
    end: int
    line: int
    kind: str          # word, punct, string, raw, char, number
    text: str          # literals become '0'


@dataclass(frozen=True)
class Kernel:
    intro: int                 # token index of the macro name or the capture's '['
    params: tuple | None       # token indices of the parameter list's ( and ), if any
    body: tuple                # token indices of the body's { and }
    call: int | None           # token index of the '(' of the call the lambda is passed to
    macro: str | None          # the macro name, or None for a bracket lambda


def code_tokens(text: str) -> list[Tok]:
    """Code tokens with offsets (no comments or preprocessor lines)."""
    out, line, pos = [], 1, 0
    for m in _TOKEN.finditer(text):
        kind, s = m.lastgroup, m.group()
        line += text.count("\n", pos, m.start())
        if kind not in ("comment", "pp"):
            out.append(Tok(m.start(), m.end(), line, kind,
                           "0" if kind in ("raw", "string", "char", "number") else s))
        line += s.count("\n")
        pos = m.end()
    return out


def _partners(toks: list[Tok]) -> tuple[dict, list]:
    """Matching bracket indices, and for each token the index of the innermost open bracket.
    A closing bracket of the wrong kind (a half-written file) is left unpaired."""
    stack, pair, encl = [], {}, [None] * len(toks)
    for i, t in enumerate(toks):
        encl[i] = stack[-1] if stack else None
        if t.text in _CLOSE.values():
            stack.append(i)
        elif t.text in _CLOSE and stack and toks[stack[-1]].text == _CLOSE[t.text]:
            j = stack.pop()
            pair[j], pair[i] = i, j
    return pair, encl


def _body_open(toks: list[Tok], pair: dict, j: int) -> int | None:
    """Index of the `{` that opens a lambda body at or after toks[j], skipping at most
    MAX_SPECIFIERS specifier tokens (mutable, -> T, ...); None if there is none."""
    for k in range(j, min(j + MAX_SPECIFIERS + 1, len(toks))):
        t = toks[k].text
        if t == "{":
            return k if k in pair else None
        if t in (";", ")", ",", "}"):
            return None
    return None


def find_kernels(toks: list[Tok], macros, calls) -> list[Kernel]:
    """Kernel lambdas in token order (an enclosing kernel before the kernels inside it)."""
    pair, encl = _partners(toks)
    out = []
    for i, t in enumerate(toks):
        nxt = toks[i + 1].text if i + 1 < len(toks) else ""
        call = encl[i] if encl[i] is not None and toks[encl[i]].text == "(" else None
        if t.text in macros and nxt == "(" and i + 1 in pair:
            params, after, macro = (i + 1, pair[i + 1]), pair[i + 1] + 1, t.text
        elif (t.text == "[" and nxt != "[" and i > 0 and toks[i - 1].text in ("(", ",")
              and i in pair and call is not None and call > 0 and toks[call - 1].text in calls):
            close = pair[i]
            has_params = close + 1 < len(toks) and toks[close + 1].text == "(" and close + 1 in pair
            params = (close + 1, pair[close + 1]) if has_params else None
            after, macro = (params[1] if params else close) + 1, None
        else:
            continue
        bo = _body_open(toks, pair, after)
        if bo is not None:
            out.append(Kernel(i, params, (bo, pair[bo]), call, macro))
    return out


def blank(text: str, ranges, offset: int = 0) -> str:
    """`text` with the characters in each (start, end) range (offsets into the full source,
    `text` starting at `offset`) replaced by spaces, newlines kept."""
    chars = list(text)
    for a, b in ranges:
        for k in range(max(a - offset, 0), min(b - offset, len(chars))):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


def body_range(toks: list[Tok], k: Kernel) -> tuple[int, int]:
    """Offsets of the body between its braces (the braces stay)."""
    return toks[k.body[0]].end, toks[k.body[1]].start


def inside(k: Kernel, outer: Kernel) -> bool:
    return outer.body[0] < k.intro and k.body[1] < outer.body[1]


def label(toks: list[Tok], source: str, k: Kernel) -> str | None:
    """The string literal that starts the call the kernel is passed to, without quotes and
    with whitespace runs as '_' (None if the call does not start with a plain string)."""
    if k.call is None or k.call + 2 >= len(toks):
        return None
    first, sep = toks[k.call + 1], toks[k.call + 2]
    if first.kind != "string" or sep.text != "," or k.intro <= k.call + 2:
        return None
    m = re.fullmatch(r'(?:u8|[uUL])?"(.*)"', source[first.start:first.end], re.DOTALL)
    text = re.sub(r"\s+", "_", m.group(1).strip()) if m else ""
    return text or None


def synthetic(measured: str, toks: list[Tok], k: Kernel, inner: list[Kernel]) -> str:
    """`void L<params> <body>` on the lines of the original, inner kernel bodies blanked,
    specifiers between the parameters and the body blanked."""
    bo, bc = toks[k.body[0]], toks[k.body[1]]
    if k.params is not None:
        po, pc = toks[k.params[0]], toks[k.params[1]]
        head_line, params = po.line, measured[po.start:pc.end]
        between = measured[pc.end:bo.start]
    else:
        head_line, params, between = bo.line, "()", ""
    body = blank(measured[bo.start:bc.end], [body_range(toks, j) for j in inner], bo.start)
    return "\n" * (head_line - 1) + "void L" + params + re.sub(r"[^\n]", " ", between) + body


@dataclass(frozen=True)
class Lambda:
    name: str
    line: int          # the line of the macro name or the capture's '['
    text: str          # synthetic source for lizard (see synthetic)
    full: str          # the same with the inner kernel bodies kept (its whole nloc)
    body: str          # the shown body, braces included, inner kernel bodies blanked
    parent: tuple      # ("fn", (name, start, end)) or ("lambda", name): the direct parent
    introducer: str | None   # the macro of this kernel or of the nearest enclosing one


def _enclosing(line: int, functions: list[tuple[str, int, int]]) -> tuple | None:
    """The innermost function (name, start, end) whose lines hold `line`."""
    holding = [f for f in functions if f[1] <= line <= f[2]]
    return min(holding, key=lambda f: f[2] - f[1]) if holding else None


def _in_function(toks: list[Tok], k: Kernel, functions: list[tuple[str, int, int]]) -> bool:
    """Whether a function of `functions` holds the kernel. A macro lambda at file scope is
    read by lizard as a function named after the macro, starting on the macro's line: that
    one is the lambda itself, not a parent."""
    line = toks[k.intro].line
    holding = [f for f in functions if f[1] <= line <= f[2]]
    if not holding:
        return False
    name, start, _ = min(holding, key=lambda f: f[2] - f[1])
    return not (k.macro is not None and start == line and name.rsplit("::", 1)[-1] == k.macro)


def kernels(shown: str, macros, calls, functions: list[tuple[str, int, int]]):
    """(tokens of `shown`, kernel lambdas inside one of `functions`)."""
    toks = code_tokens(shown)
    found = find_kernels(toks, frozenset(macros), frozenset(calls))
    return toks, [k for k in found if _in_function(toks, k, functions)]


def lambdas(source: str, shown: str, measured: str, toks: list[Tok], ks: list[Kernel],
            functions: list[tuple[str, int, int]]) -> list[Lambda] | None:
    """One Lambda per kernel; `functions`: (name, start, end) of the parent functions.
    None if a kernel has no enclosing function (the caller then does not split)."""
    names: dict[Kernel, str] = {}
    macros: dict[Kernel, str | None] = {}
    unlabelled: dict[str, int] = {}
    used: set[str] = set()
    out = []
    for k in ks:
        outer = [o for o in ks if o is not k and inside(k, o)]
        direct = max(outer, key=lambda o: o.intro) if outer else None
        if direct is not None:
            parent, key = names[direct], ("lambda", names[direct])
        else:
            fn = _enclosing(toks[k.intro].line, functions)
            if fn is None:
                return None
            parent, key = fn[0], ("fn", fn)
        macros[k] = k.macro or (macros[direct] if direct is not None else None)
        tag = label(toks, source, k)
        if tag is None:
            unlabelled[parent] = unlabelled.get(parent, 0) + 1
            tag = f"lambda#{unlabelled[parent]}"
        name, n = f"{parent}::{tag}", 1
        while name in used:
            n += 1
            name = f"{parent}::{tag}#{n}"
        used.add(name)
        names[k] = name
        inner = [j for j in ks if inside(j, k)]
        bo, bc = toks[k.body[0]], toks[k.body[1]]
        body = blank(shown[bo.start:bc.end], [body_range(toks, j) for j in inner], bo.start)
        out.append(Lambda(name, toks[k.intro].line, synthetic(measured, toks, k, inner),
                          synthetic(measured, toks, k, []) if inner else "", body, key, macros[k]))
    return out


def _pair2(toks: list[Tok], i: int, a: str, b: str) -> bool:
    """Whether toks[i], toks[i + 1] are `a`, `b` (the tokenizer splits -> and ::)."""
    return 0 <= i and i + 1 < len(toks) and toks[i].text == a and toks[i + 1].text == b


def _type_start(toks: list[Tok], j: int) -> int | None:
    """If toks[j] ends a type (a word, or the `>` of `word<...>`), the index where the type
    starts (`::` qualifiers and const, static, ... included), else None."""
    if toks[j].text == ">":
        depth = 0
        while j >= 0 and toks[j].text not in _TYPE_STOP:
            depth += (toks[j].text == ">") - (toks[j].text == "<")
            if depth == 0:
                break
            j -= 1
        if j < 1 or toks[j].text != "<" or not _WORD.match(toks[j - 1].text):
            return None
        j -= 1
    if not _WORD.match(toks[j].text) or toks[j].text in _NOT_DECL_AFTER:
        return None
    while j >= 3 and _pair2(toks, j - 2, ":", ":") and _WORD.match(toks[j - 3].text):
        j -= 3
    while j >= 1 and toks[j - 1].text in _QUALIFIERS:
        j -= 1
    return j


def declared(toks: list[Tok], a: int, b: int) -> set[str]:
    """Names that look declared in toks[a:b]: a word after a type-like word (not a keyword
    such as return) and before one of = ; ( { , : [ ); or a word after a type and a run of
    * & (or after the `>` of `word<...>`) where the type starts a statement or a parameter
    (after ; { } ( ,) and the word is followed by `=` (not `==`), `;`, `{`, `:` (or `(`
    right after `>`). Lizard gives the parameter lists of functions and lambdas."""
    names = set()
    for k in range(max(a, 1), min(b, len(toks) - 1)):
        t, prev, nxt = toks[k].text, toks[k - 1].text, toks[k + 1].text
        if not _WORD.match(t) or t in _KEYWORDS or nxt not in _DECL_BEFORE:
            continue
        if _pair2(toks, k + 1, ":", ":"):
            continue
        if prev in (">", "*", "&"):
            if _pointer_decl(toks, k):
                names.add(t)
        elif _WORD.match(prev) and prev not in _NOT_DECL_AFTER:
            names.add(t)
    return names


def _pointer_decl(toks: list[Tok], k: int) -> bool:
    """`T* x = `, `auto&& x : `, `View<double*> x(`; not `a * dt;`, `i > n)`, `x && m[`."""
    nxt = toks[k + 1].text
    if nxt == "=" and k + 2 < len(toks) and toks[k + 2].text == "=":
        return False
    if nxt not in ("=", ";", "{", ":") and not (nxt == "(" and toks[k - 1].text == ">"):
        return False
    j = k - 1
    while j > 0 and toks[j].text in ("*", "&"):
        j -= 1
    if toks[j].text == ">" and _pair2(toks, j - 1, "-", ">"):
        return False
    start = _type_start(toks, j)
    return start is not None and (start == 0 or toks[start - 1].text in _STMT_OPEN)


def outer_names(toks: list[Tok], k: Kernel, functions: list[tuple[str, int, int]],
                params: dict) -> set[str]:
    """Names a default capture can take: the parameters (`params`, keyed like `functions`) and
    the earlier locals of the function that holds the kernel."""
    fn = _enclosing(toks[k.intro].line, functions)
    if fn is None:
        return set()
    start = bisect_left([t.line for t in toks], fn[1])
    return set(params.get(fn, ())) | declared(toks, start, k.intro)


def _entries(toks: list[Tok], a: int, b: int) -> list[list[str]]:
    """The comma-separated entries of the capture list toks[a] = '[' ... toks[b] = ']'."""
    out, cur, depth = [], [], 0
    for t in toks[a + 1:b]:
        if t.text == "," and depth == 0:
            out.append(cur)
            cur = []
            continue
        depth += (t.text in "([{") - (t.text in ")]}")
        cur.append(t.text)
    return [e for e in out + [cur] if e]


def captures(toks: list[Tok], k: Kernel, outer: set[str], params: set[str],
             this_macros=frozenset()) -> int:
    """Rule (4): the number of names kernel `k` captures (module docstring)."""
    explicit, listed, default = 0, set(), k.macro is not None
    if k.macro is None:
        pair, _ = _partners(toks[k.intro:k.body[0]])
        for e in _entries(toks, k.intro, k.intro + pair[0]):
            if e in (["&"], ["="]):
                default = True
                continue
            explicit += 1
            listed |= {w for w in e[:2] if _WORD.match(w) and w != "this"}
    elif k.macro in this_macros:
        explicit = 1
    if not default:
        return explicit
    bo, bc = k.body
    own = params | declared(toks, bo, bc + 1) | listed
    used = set()
    for j in range(bo + 1, bc):
        t = toks[j].text
        if t not in outer or t in own or _pair2(toks, j + 1, ":", ":"):
            continue
        if toks[j - 1].text == "." or _pair2(toks, j - 2, ":", ":") or _pair2(toks, j - 2, "-", ">"):
            continue
        used.add(t)
    return explicit + len(used)
