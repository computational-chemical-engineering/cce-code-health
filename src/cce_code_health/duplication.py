"""Language-independent duplicated-line detection over normalised line windows.

Import statements are not code to deduplicate (modules that use the same names import them the
same way), so they are left out whole, as CPD's ignore-imports does: `import x` / `from x import y`
lines, the name lists of `from x import (...)` up to the closing parenthesis, backslash
continuations, and C/C++ `#include` lines.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

SKIP = {'"""', "'''", ")", "]", "}", "),", "],", "},", "else:", "try:", "{", "};"}
IMPORT = re.compile(r"(from\s+\.*[\w.]*\s+import\b|import\s+[\w.])")


def normalise(line: str) -> str | None:
    s = line.strip()
    if (not s or s.startswith(("#", "//")) or s in SKIP
            or s.startswith(("import ", "from ", "#include"))):
        return None
    return " ".join(s.split())


def code_lines(text: str) -> list[str]:
    """Normalised lines of `text`, import statements (with their continuation lines) left out."""
    out: list[str] = []
    cont = None                                   # "paren" or "backslash" inside an import
    for line in text.splitlines():
        code = line.split("#", 1)[0].rstrip()
        if cont == "paren":
            if ")" in code:
                cont = None
            continue
        if cont == "backslash":
            if not code.endswith("\\"):
                cont = None
            continue
        if IMPORT.match(code.lstrip()):
            if code.count("(") > code.count(")"):
                cont = "paren"
            elif code.endswith("\\"):
                cont = "backslash"
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
