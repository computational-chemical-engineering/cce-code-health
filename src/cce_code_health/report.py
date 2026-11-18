"""Debt, smells and the display score for analysed files."""
from __future__ import annotations

from collections import Counter

from .profiles import PROFILES, Profile
from .python_backend import FileMetrics, FunctionMetrics

# Display score only (same recipe as the 2026-11-11 prototype baseline).
_DISPLAY_PENALTY = {"complex": (0.5, 2.0), "brain": (1.0, 3.0), "long": (0.3, 1.5),
                    "deep_nesting": (0.5, 2.0), "bumpy_road": (0.5, 1.5), "many_args": (0.2, 1.0),
                    "many_captures": (0.2, 1.0)}
CLASS_STATE_PENALTY = (1.0, 2.0)   # python@1 display: per flagged class, cap


def class_smells(classes: list[dict], p: Profile) -> list[dict]:
    """python@1: classes whose state_writes exceed the profile's class_state_threshold. A list of
    its own, not file_smells, so that moving a class to a new file is not a new file smell."""
    return [{"name": c["name"], "line": c["line"], "smell": "class_state", "state_writes": c["state_writes"]}
            for c in classes if c.get("state_writes", 0) > p.class_state_threshold]


def allowed(allows, metric: str) -> bool:
    return any(a.valid and (metric in a.smells or "all" in a.smells) for a in allows)


def _is_table(fn: FunctionMetrics, p: Profile) -> bool:
    return bool(p.table_functions) and fn.name.rsplit("::", 1)[-1].rsplit(".", 1)[-1] in p.table_functions


def function_debt(fn: FunctionMetrics, p: Profile) -> tuple[float, dict]:
    parts = {}
    table = _is_table(fn, p)
    for m in p.metrics:
        if table and m == "nloc":
            continue
        excess = fn.value(m) - p.thresholds[m]
        if excess > 0 and not allowed(fn.allows, m):
            parts[m] = round(p.weights[m] * excess, 3)
    return round(sum(parts.values()), 3), parts


def is_brain(fn: FunctionMetrics, p: Profile) -> bool:
    return (not _is_table(fn, p) and fn.value(p.brain_metric) > p.brain_threshold
            and fn.nloc > p.brain_nloc
            and not allowed(fn.allows, p.brain_metric))


def _display_smells(fn: FunctionMetrics, p: Profile) -> list[str]:
    t, bm = p.thresholds, p.brain_metric
    return [s for s, hit in (
        ("brain", is_brain(fn, p)), ("complex", fn.value(bm) > t[bm]),
        ("long", fn.nloc > t["nloc"]), ("deep_nesting", fn.nesting > t["nesting"]),
        ("bumpy_road", "bumps" in p.metrics and fn.bumps > t["bumps"]),
        ("many_args", fn.params > t["params"]),
        ("many_captures", "captures" in p.metrics and fn.value("captures") > t["captures"])) if hit]


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
        if getattr(fn, "public", None) is not None:     # python@1
            functions[-1]["public"] = fn.public
        if getattr(fn, "introducer", None):     # cpp@1 kernel lambda: its macro, a zone marker
            functions[-1]["introducer"] = fn.introducer
    penalty = sum(min(w * counts.get(s, 0), cap) for s, (w, cap) in _DISPLAY_PENALTY.items())
    penalty += {"huge_file": 2.0, "large_file": 1.0}.get(next((s for s in smells if "file" in s), ""), 0.0)
    penalty += 1.0 if "duplication" in smells else 0.0
    state = {}
    if "state" in p.py_rules:
        cs = class_smells(fm.classes, p)
        w, cap = CLASS_STATE_PENALTY
        penalty += min(w * len(cs), cap)
        state = {"class_smells": cs}
    return {"path": fm.path, "nloc": fm.nloc, "error": fm.error,
            "debt": round(sum(f["debt"] for f in functions), 3),
            "file_smells": smells, **state, "dup_fraction": round(dup_frac, 3), "dup_lines": dup_lines,
            "health": round(max(1.0, 10.0 - penalty), 2),
            "functions": functions,
            "classes": fm.classes,
            "max_lcom4": max((c["lcom4"] for c in fm.classes if c["lcom4"] is not None), default=None),
            "suppressions": [{"line": a.line, "smells": list(a.smells), "reason": a.reason,
                              "valid": a.valid, "file_level": a.file_level} for a in fm.allows]}


SIZE_PENALTY = {"huge_file": 2, "large_file": 1}


def size_smells(reports: dict[str, dict]) -> int:
    """Size-smell weight over the reports (huge_file 2, large_file 1, as in display health)."""
    return sum(SIZE_PENALTY.get(s, 0) for r in reports.values() for s in r["file_smells"])


def added_smells(old: list[str] | None, new: list[str]) -> list[str]:
    """File smells of `new` that `old` lacked. The size smells are one ordered family
    (none < large_file < huge_file): a downgrade, huge -> large, is not new."""
    old = old or []
    size_old = max((SIZE_PENALTY.get(x, 0) for x in old), default=0)
    return sorted(x for x in set(new) - set(old) if x not in SIZE_PENALTY or SIZE_PENALTY[x] > size_old)


def new_file_smells(rb: dict[str, dict], rh: dict[str, dict], changes) -> tuple[list[str], list[str]]:
    """(errors, notes) for head files with a file smell their base counterpart lacked.

    A new `duplication` smell is a note, not an error, when the duplicated lines over the touched
    files did not rise: the duplicates already existed and were concentrated into fewer lines (a
    file split, or code deleted around them), not added. `rb`, `rh`: reports of the touched files."""
    counterpart = {h: b for _, b, h in changes if h}
    dup_rose = (sum(r.get("dup_lines", 0) for r in rh.values())
                > sum(r.get("dup_lines", 0) for r in rb.values()))
    errors, notes = [], []
    for path, r in sorted(rh.items()):
        old = rb.get(counterpart.get(path) or "")
        new = added_smells(old["file_smells"] if old else None, r["file_smells"])
        if "duplication" in new and not dup_rose:
            new.remove("duplication")
            notes.append(f"{path}: duplication {r['dup_fraction']:.0%} of nloc {r['nloc']}, concentrated "
                         f"not added (duplicated lines over the touched files did not rise)")
        if new:
            errors.append(f"{path}: new file smell(s) {', '.join(new)} (nloc {r['nloc']})")
    return errors, notes


def new_brains(rb: dict[str, dict], rh: dict[str, dict], changes) -> list[tuple[str, dict]]:
    """(head path, function) for each new brain function over the touched files.

    Brains are matched by name as a multiset, not per file: a brain that moves between touched
    files (a split, a rename) is not new, but a head brain beyond the base's count for its name
    is, so a repeated name (C++ overloads, a `run::lambda#1` in another file) cannot hide one.
    Within a name, head brains in the file that had it at base (or that file's rename
    counterpart) are matched first, so the excess is reported where it was added; within one
    file the first in source order is matched, so a new overload above an old one reports the old
    one's line."""
    counterpart = {h: b for _, b, h in changes if h}
    left = Counter(f["name"] for r in rb.values() for f in r["functions"] if f["brain"])
    had = {(p, f["name"]) for p, r in rb.items() for f in r["functions"] if f["brain"]}
    head = [(p, f) for p, r in sorted(rh.items()) for f in r["functions"] if f["brain"]]
    head.sort(key=lambda pf: (counterpart.get(pf[0], pf[0]), pf[1]["name"]) not in had)
    out = []
    for p, f in head:
        if left[f["name"]]:
            left[f["name"]] -= 1
        else:
            out.append((p, f))
    return sorted(out, key=lambda pf: (pf[0], pf[1]["line"]))


def _new_writes(old: dict | None, new: dict) -> str:
    """'m1 writes a, b; m2 writes c': (method, channel) pairs of `new` channels not in `old`."""
    old = old or {}
    by: dict[str, list[str]] = {}
    for a, ws in new.items():
        for m in ws:
            if m not in old.get(a, ()):
                by.setdefault(m, []).append(a)
    return "; ".join(f"{m} writes {', '.join(sorted(a))}" for m, a in sorted(by.items()))


def class_state_rises(rb: dict[str, dict], rh: dict[str, dict], changes) -> list[str]:
    """python@1 class-state ratchet over the touched files (raw values; allow comments do not
    count): a message for each class of the base touched files whose state_writes rose, and,
    when none did, one for a rise of the total. Empty for profiles without class state."""
    cls_b = [(p, c) for p, r in rb.items() for c in r.get("classes", []) if "state_writes" in c]
    cls_h = [(p, c) for p, r in rh.items() for c in r.get("classes", []) if "state_writes" in c]
    if not cls_b and not cls_h:
        return []
    counterpart = {h: b for _, b, h in changes if h}
    base = {(p, c["name"]): c for p, c in cls_b}
    by_name: dict[str, list] = {}
    for p, c in cls_b:
        by_name.setdefault(c["name"], []).append(c)
    out = []
    for p, c in sorted(cls_h, key=lambda x: (x[0], x[1]["line"])):
        old = base.get((counterpart.get(p), c["name"]))
        if old is None and len(by_name.get(c["name"], [])) == 1:    # moved to another file
            old = by_name[c["name"]][0]
        if old is not None and c["state_writes"] > old["state_writes"]:
            out.append(f"class state rose: {c['name']} {old['state_writes']} -> {c['state_writes']} "
                       f"({_new_writes(old['channels'], c['channels'])})")
    sb = sum(c["state_writes"] for _, c in cls_b)
    sh = sum(c["state_writes"] for _, c in cls_h)
    # Seed allowance: a step may introduce new classes holding up to class_state_threshold
    # state_writes in total (a method object made from one long function's locals) when every
    # base class still exists; their later growth is caught by the per-class rule above.
    head_names = {c["name"] for _, c in cls_h}
    new_sum = sum(c["state_writes"] for _, c in cls_h if c["name"] not in by_name)
    if (sh > sb and not out and set(by_name) <= head_names
            and new_sum <= PROFILES["python@1"].class_state_threshold):
        return out
    if sh > sb and not out:
        new = [f"{c['name']} {c['state_writes']}" for _, c in cls_h
               if c["state_writes"] and c["name"] not in by_name]
        out.append(f"class state rose over the touched files: {sb} -> {sh}"
                   + (f" (new: {', '.join(new[:5])})" if new else ""))
    return out
