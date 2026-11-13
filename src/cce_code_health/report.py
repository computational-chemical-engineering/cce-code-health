"""Debt, smells and the display score for analysed files."""
from __future__ import annotations

from .profiles import Profile
from .python_backend import FileMetrics, FunctionMetrics

# Display score only (same recipe as the 2026-11-11 prototype baseline).
_DISPLAY_PENALTY = {"complex": (0.5, 2.0), "brain": (1.0, 3.0), "long": (0.3, 1.5),
                    "deep_nesting": (0.5, 2.0), "bumpy_road": (0.5, 1.5), "many_args": (0.2, 1.0)}


def allowed(allows, metric: str) -> bool:
    return any(a.valid and (metric in a.smells or "all" in a.smells) for a in allows)


def function_debt(fn: FunctionMetrics, p: Profile) -> tuple[float, dict]:
    parts = {}
    for m in p.metrics:
        excess = fn.value(m) - p.thresholds[m]
        if excess > 0 and not allowed(fn.allows, m):
            parts[m] = round(p.weights[m] * excess, 3)
    return round(sum(parts.values()), 3), parts


def is_brain(fn: FunctionMetrics, p: Profile) -> bool:
    return (fn.value(p.brain_metric) > p.brain_threshold and fn.nloc > p.brain_nloc
            and not allowed(fn.allows, p.brain_metric))


def _display_smells(fn: FunctionMetrics, p: Profile) -> list[str]:
    t, bm = p.thresholds, p.brain_metric
    return [s for s, hit in (
        ("brain", is_brain(fn, p)), ("complex", fn.value(bm) > t[bm]),
        ("long", fn.nloc > t["nloc"]), ("deep_nesting", fn.nesting > t["nesting"]),
        ("bumpy_road", "bumps" in p.metrics and fn.bumps > t["bumps"]),
        ("many_args", fn.params > t["params"])) if hit]


def file_report(fm: FileMetrics, dup_lines: int, p: Profile) -> dict:
    file_allows = [a for a in fm.allows if a.file_level]
    smells = []
    if fm.nloc > p.huge_file_nloc and not allowed(file_allows, "huge_file"):
        smells.append("huge_file")
    elif fm.nloc > p.large_file_nloc and not allowed(file_allows, "large_file"):
        smells.append("large_file")
    dup_frac = dup_lines / max(fm.nloc, 1)
    if dup_frac > p.dup_fraction and not allowed(file_allows, "duplication"):
        smells.append("duplication")
    functions, counts = [], {}
    for fn in fm.functions:
        debt, parts = function_debt(fn, p)
        ds = _display_smells(fn, p)
        for s in ds:
            counts[s] = counts.get(s, 0) + 1
        functions.append({"name": fn.name, "qualname": f"{fm.path}::{fn.name}", "line": fn.line,
                          "end_line": fn.end, "nloc": fn.nloc,
                          **{m: fn.value(m) for m in p.metrics if m != "nloc"},
                          "debt": debt, "debt_parts": parts, "brain": is_brain(fn, p)})
    penalty = sum(min(w * counts.get(s, 0), cap) for s, (w, cap) in _DISPLAY_PENALTY.items())
    penalty += {"huge_file": 2.0, "large_file": 1.0}.get(next((s for s in smells if "file" in s), ""), 0.0)
    penalty += 1.0 if "duplication" in smells else 0.0
    return {"path": fm.path, "nloc": fm.nloc, "error": fm.error,
            "debt": round(sum(f["debt"] for f in functions), 3),
            "file_smells": smells, "dup_fraction": round(dup_frac, 3),
            "health": round(max(1.0, 10.0 - penalty), 2),
            "functions": functions,
            "suppressions": [{"line": a.line, "smells": list(a.smells), "reason": a.reason,
                              "valid": a.valid, "file_level": a.file_level} for a in fm.allows]}
