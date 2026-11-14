"""Size and duplication budget for agent docs (`codehealth docs --agent`, and a ratchet in diff).

Agent docs (CLAUDE.md, AGENTS.md, .claude/ agents and skills) are loaded into a model's context,
often on every turn: their length is a recurring cost, and text repeated across them drifts
apart. Keys in `[docs]`:

    agent = ["CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "**/AGENTS.md", ".claude/**/*.md", "**/SKILL.md"]  # default
    agent_words = 2500        # budget per agent doc (words; about 1.3 tokens each)
    agent_budget = "warn"     # "warn" (default) or "fail" in `codehealth diff`

Ratchet, as for debt: a doc over its budget may not grow, a doc under it may not cross it, a new
doc must fit. A paragraph of 40+ words that appears in two agent docs and did not at base is a
warning (say it once, point to it from the other place).
"""
from __future__ import annotations

import re

from . import config

DEFAULT_AGENT = ["CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "**/AGENTS.md", ".claude/**/*.md", "**/SKILL.md"]
DEFAULT_WORDS = 2500
DUP_WORDS = 40


def check(d: dict) -> list[str]:
    probs = []
    a = d.get("agent", DEFAULT_AGENT)
    if not (isinstance(a, list) and all(isinstance(x, str) and x for x in a)):
        probs.append("[docs] agent must be a list of globs")
    w = d.get("agent_words", DEFAULT_WORDS)
    if not (isinstance(w, int) and not isinstance(w, bool) and w > 0):
        probs.append("[docs] agent_words must be a positive integer")
    if d.get("agent_budget", "warn") not in ("warn", "fail"):
        probs.append("[docs] agent_budget must be \"warn\" or \"fail\"")
    return probs


def is_agent(path: str, d: dict) -> bool:
    return path.endswith(".md") and any(config.match(path, g) for g in d.get("agent", DEFAULT_AGENT))


def words(text: str) -> int:
    return len(text.split())


def paragraphs(text: str) -> list[str]:
    """Normalised paragraphs of DUP_WORDS+ words (code fences included, whitespace collapsed)."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        norm = " ".join(block.split()).lower()
        if len(norm.split()) >= DUP_WORDS:
            out.append(norm)
    return out


def duplicates(texts: dict[str, str]) -> dict[str, list[str]]:
    """paragraph -> agent docs it appears in (two or more)."""
    where: dict[str, set] = {}
    for path, t in texts.items():
        for p in set(paragraphs(t)):
            where.setdefault(p, set()).add(path)
    return {p: sorted(ds) for p, ds in where.items() if len(ds) > 1}


def report(texts: dict[str, str], d: dict) -> dict:
    budget = d.get("agent_words", DEFAULT_WORDS)
    docs = [{"path": p, "words": words(t), "lines": t.count("\n") + (0 if t.endswith("\n") or not t else 1),
             "over": max(0, words(t) - budget)} for p, t in sorted(texts.items())]
    docs.sort(key=lambda x: -x["words"])
    dups = duplicates(texts)
    return {"budget": budget, "docs": docs, "total_words": sum(x["words"] for x in docs),
            "duplicates": [{"docs": ds, "words": len(p.split()), "start": p[:80]}
                           for p, ds in sorted(dups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]}


def compare(base: dict[str, str], head: dict[str, str], renames: dict[str, str], d: dict) -> tuple[list, list]:
    """(budget findings, duplication warnings) of head agent docs against base.
    renames: {head path: base path}."""
    budget = d.get("agent_words", DEFAULT_WORDS)
    over = []
    for p, t in sorted(head.items()):
        wh = words(t)
        bp = renames.get(p, p)
        wb = words(base[bp]) if bp in base else 0
        if wh > budget and wh > wb:
            what = "new agent doc" if bp not in base else f"grew {wb} -> {wh} words"
            over.append(f"{p}: {what}, over the agent-doc budget of {budget} words ({wh})")
    old = {p for p in duplicates(base)}
    dup = [f"agent docs {', '.join(ds)} now repeat a {len(p.split())}-word paragraph (\"{p[:60]}...\"): "
           f"keep it in one place and point to it" for p, ds in sorted(duplicates(head).items()) if p not in old]
    return over, dup
