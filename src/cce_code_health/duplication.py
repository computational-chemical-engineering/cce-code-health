"""Language-independent duplicated-line detection over normalised line windows."""
from __future__ import annotations

import hashlib
from collections import defaultdict

SKIP = {'"""', "'''", ")", "]", "}", "),", "],", "},", "else:", "try:", "{", "};"}


def normalise(line: str) -> str | None:
    s = line.strip()
    if (not s or s.startswith(("#", "//")) or s in SKIP
            or s.startswith(("import ", "from ", "#include"))):
        return None
    return " ".join(s.split())


def duplicated_lines(texts: dict[str, str], window: int) -> dict[str, int]:
    """Number of normalised lines per file that lie in a window repeated elsewhere."""
    where = defaultdict(list)
    for path, text in texts.items():
        lines = [n for n in map(normalise, text.splitlines()) if n is not None]
        for k in range(len(lines) - window + 1):
            h = hashlib.blake2b("\n".join(lines[k:k + window]).encode(), digest_size=12).digest()
            where[h].append((path, k))
    hit = defaultdict(set)
    for locs in where.values():
        if len(locs) > 1:
            for path, k in locs:
                hit[path].update(range(k, k + window))
    return {p: len(s) for p, s in hit.items()}
