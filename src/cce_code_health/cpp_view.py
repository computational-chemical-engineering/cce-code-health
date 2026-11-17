"""cpp@1 preprocessed view of a C++ source: what lizard and the nesting scan measure.

select_branches keeps one branch of every #if/#ifdef/#ifndef group and blanks the other
branches and all of the group's directives. `#if 0`, `#if false` and `#elif 0` are dead; after
`#if 1` or `#elif 1` the later branches are dead. Of the live branches the one with the most
decision tokens (if for while catch case ? && ||, nested groups by their own choice) is kept,
ties to the first; if its brace balance differs from the first live branch's, the first live
branch is kept instead, so a function that opens a brace in each branch stays one function.

unmark_constexpr blanks the `if` of `if constexpr`: a compile-time branch is not a decision.

Both keep every character offset and newline, so lines and suppressions map to the source.
Design: docs/CPP1_LAYERS.md, rule (1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cpp_backend import _TOKEN     # cpp_backend imports this module lazily (no cycle)

DECISIONS = frozenset({"if", "for", "while", "catch", "case", "?"})
_DIRECTIVE = re.compile(r"[ \t]*#\s*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b(.*)",
                        re.DOTALL)
_COMMENT = re.compile(r"/\*.*?(?:\*/|$)|//.*", re.DOTALL)
_FALSE = re.compile(r"\(?\s*(?:0|false)\s*\)?")     # the whole condition, comments removed
_TRUE = re.compile(r"\(?\s*(?:1|true)\s*\)?")


@dataclass
class _Branch:
    start: int                                  # offset after its directive
    end: int = -1                               # offset of the group's next directive
    dead: bool = False
    items: list = field(default_factory=list)   # (offset, token) pairs and nested _Groups


@dataclass
class _Group:
    directives: list                            # (start, end) offsets
    branches: list = field(default_factory=list)
    settled: bool = False                       # a `#if 1` / `#elif 1` branch was seen
    choice: tuple | None = None                 # (kept branch or None, its decisions)


def _open_branch(group: _Group, at: int, name: str, cond: str, branches: list) -> None:
    dead = group.settled or (name in ("if", "elif") and _FALSE.fullmatch(cond) is not None)
    group.settled |= name in ("if", "elif") and _TRUE.fullmatch(cond) is not None
    group.branches.append(_Branch(at, dead=dead))
    branches.append(group.branches[-1])


def _directive(m: re.Match, span: tuple, branches: list, groups: list) -> None:
    name, cond = m.group(1), _COMMENT.sub(" ", m.group(2)).strip()
    if name in ("if", "ifdef", "ifndef"):
        group = _Group([span])
        branches[-1].items.append(group)
        groups.append(group)
    elif not groups:
        return                                  # stray #elif/#else/#endif: left as it is
    else:
        group = groups[-1]
        branches.pop().end = span[0]
        group.directives.append(span)
        if name == "endif":
            groups.pop()
            return
    _open_branch(group, span[1], name, cond, branches)


def _is_token(item, text: str) -> bool:
    return isinstance(item, tuple) and item[1] == text


def _parse(source: str) -> _Branch:
    root = _Branch(0)
    branches, groups = [root], []
    for m in _TOKEN.finditer(source):
        kind = m.lastgroup
        if kind == "pp":
            d = _DIRECTIVE.match(m.group())
            if d:
                _directive(d, m.span(), branches, groups)
        elif kind in ("word", "punct"):
            items = branches[-1].items
            if m.group() == "constexpr" and items and _is_token(items[-1], "if"):
                items[-1] = (items[-1][0], "if constexpr")   # not a decision (unmark_constexpr)
            else:
                items.append((m.start(), m.group()))
    while len(branches) > 1:                    # unterminated groups end at the end of file
        branches.pop().end = len(source)
    return root


def _decisions(branch: _Branch) -> int:
    count, prev = 0, (-2, "")
    for item in branch.items:
        if isinstance(item, _Group):
            count += _choose(item)[1]
            prev = (-2, "")
            continue
        at, tok = item
        if tok in DECISIONS:
            count += 1
        elif tok in "&|" and prev == (at - 1, tok):
            count += 1                          # && and || arrive as two adjacent tokens
            item = (-2, "")
        prev = item
    return count


def _balance(branch: _Branch) -> int:
    total = 0
    for item in branch.items:
        if isinstance(item, _Group):
            live = [b for b in item.branches if not b.dead]
            total += _balance(live[0]) if live else 0
        else:
            total += {"{": 1, "}": -1}.get(item[1], 0)
    return total


def _choose(group: _Group) -> tuple:
    if group.choice is None:
        live = [b for b in group.branches if not b.dead]
        best = max(live, key=_decisions, default=None)   # max keeps the first of equals
        if best is not None and _balance(best) != _balance(live[0]):
            best = live[0]
        group.choice = (best, _decisions(best) if best else 0)
    return group.choice


def _blank(chars: list, start: int, end: int) -> None:
    for k in range(start, end):
        if chars[k] != "\n":
            chars[k] = " "


def _apply(branch: _Branch, chars: list) -> None:
    for item in branch.items:
        if not isinstance(item, _Group):
            continue
        for start, end in item.directives:
            _blank(chars, start, end)
        keep = _choose(item)[0]
        for b in item.branches:
            if b is keep:
                _apply(b, chars)
            else:
                _blank(chars, b.start, b.end)


def select_branches(source: str) -> str:
    chars = list(source)
    _apply(_parse(source), chars)
    return "".join(chars)


def unmark_constexpr(source: str) -> str:
    chars, prev = list(source), None
    for m in _TOKEN.finditer(source):
        if m.lastgroup in ("comment", "pp"):
            continue
        if m.group() == "constexpr" and prev is not None and prev.group() == "if":
            _blank(chars, *prev.span())
        prev = m
    return "".join(chars)
