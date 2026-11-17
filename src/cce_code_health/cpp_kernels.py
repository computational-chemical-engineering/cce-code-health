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
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .cpp_backend import _TOKEN

MAX_SPECIFIERS = 12
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
