"""Language-independent duplicated-line detection over normalised line windows.

Import statements and `__all__` declarations are not code to deduplicate (modules that use the
same names import them the same way, and a package's `__all__` repeats its submodules'), so they
are left out whole, as CPD's ignore-imports does: `import x` / `from x import y` lines, the name
lists of `from x import (...)` up to the closing parenthesis, `__all__ = [...]` and
`__all__ += [...]` up to the closing bracket, backslash continuations, and C/C++ `#include` lines.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

SKIP = {'"""', "'''", ")", "]", "}", "),", "],", "},", "else:", "try:", "{", "};"}
DECLARATION = re.compile(r"(from\s+\.*[\w.]*\s+import\b|import\s+[\w.]|__all__\s*(\+=|=|:))")


def normalise(line: str) -> str | None:
    s = line.strip()
    if (not s or s.startswith(("#", "//")) or s in SKIP
            or s.startswith(("import ", "from ", "#include"))):
        return None
    return " ".join(s.split())


def _brackets(code: str) -> int:
    return sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")


def code_lines(text: str) -> list[str]:
    """Normalised lines of `text`, import statements and `__all__` declarations (with their
    continuation lines) left out."""
    out: list[str] = []
    depth = 0                                     # open brackets of an import or __all__ statement
    backslash = False                             # inside its backslash continuation
    for line in text.splitlines():
        code = line.split("#", 1)[0].rstrip()
        if depth > 0 or backslash:
            depth += _brackets(code)
            backslash = depth <= 0 and code.endswith("\\")
            continue
        if DECLARATION.match(code.lstrip()):
            depth = _brackets(code)
            backslash = depth <= 0 and code.endswith("\\")
            continue
        n = normalise(line)
        if n is not None:
            out.append(n)
    return out


def duplicated_lines(texts: dict[str, str], window: int) -> dict[str, int]:
    """Number of normalised lines per file that lie in a window repeated elsewhere."""
    where = defaultdict(list)
    for path, text in texts.items():
        lines = code_lines(text)
        for k in range(len(lines) - window + 1):
            h = hashlib.blake2b("\n".join(lines[k:k + window]).encode(), digest_size=12).digest()
            where[h].append((path, k))
    hit = defaultdict(set)
    for locs in where.values():
        if len(locs) > 1:
            for path, k in locs:
                hit[path].update(range(k, k + window))
    return {p: len(s) for p, s in hit.items()}
