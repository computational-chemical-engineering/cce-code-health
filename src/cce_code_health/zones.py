"""Zones: debt reported per layer of a repo (design: docs/CPP1_LAYERS.md). Reporting only:
pass/fail never depends on a zone, and without zones every output is unchanged byte for byte.

The config key (config.ZONES_KEY) is an ordered array of rules, each with a `name`, optional
`paths` (globs, config.match semantics; absent = every file) and optional `markers` (C++
identifiers). Several rules may share a name. A file's zone is the name of the first rule without
markers whose paths match. A function's zone is the name of the first rule whose paths match its
file and, if the rule has markers, one of whose markers occurs in the function's head: the lines
from the first declaration line (template and return-type lines above the name, as
cpp_backend._head_lines finds them) to the opening brace, comments blanked. Markers apply to C++
functions only; a Python function is matched as if it had an empty head. Anything no rule
matches is in zone "-" (shown, never dropped).
"""
from __future__ import annotations

import re

from . import config

IDENT = re.compile(r"[A-Za-z_]\w*")

UNMATCHED = "-"
RULE_KEYS = {"name", "paths", "markers"}
CATCH_ALL = {"*", "**", "**/*"}       # a paths entry that matches every file


def check_config(rules) -> tuple[list[str], list[str]]:
    """(info lines, problems) for the zones array of a config."""
    where = f"[[{config.ZONES_KEY}]]"
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        return [], [f"{where} must be an array of tables"]
    if not rules:
        return [], [f"{where} is empty: remove it or add a rule"]
    bad = []
    for i, r in enumerate(rules):
        at = f"{where} entry {i + 1}"
        bad += [f"{at}: unknown key {k!r} (known: {sorted(RULE_KEYS)})" for k in sorted(set(r) - RULE_KEYS)]
        if not isinstance(r.get("name"), str) or not r.get("name") or r.get("name") == UNMATCHED:
            bad.append(f"{at}: name must be a non-empty string other than {UNMATCHED!r}")
        paths = r.get("paths", ["*"])
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            bad.append(f"{at}: paths must be a non-empty list of glob strings")
        markers = r.get("markers", ["x"])
        if not isinstance(markers, list) or not markers or not all(
                isinstance(m, str) and IDENT.fullmatch(m) for m in markers):
            bad.append(f"{at}: markers must be a non-empty list of C++ identifiers")
    if bad:
        return [], bad
    names = list(dict.fromkeys(r["name"] for r in rules))
    marked = sum("markers" in r for r in rules)
    info = [f"zones: {len(rules)} rule(s){f' ({marked} with markers, C++ only)' if marked else ''}, "
            f"zone(s) {', '.join(names)}"]
    catch = [i for i, r in enumerate(rules)
             if "markers" not in r and ("paths" not in r or set(r["paths"]) & CATCH_ALL)]
    if catch and catch[0] < len(rules) - 1:
        info.append(f"WARNING {where}: entry {catch[0] + 1} matches every file, so the "
                    f"{len(rules) - 1 - catch[0]} rule(s) after it never apply")
    if not catch:
        info.append(f"WARNING {where}: no catch-all rule (one without paths); files no rule matches "
                    f"are reported in zone {UNMATCHED!r}")
    return info, []


def function_zone(rules: list[dict], path: str, head: set[str]) -> str:
    """Zone of a function of `path` whose head holds the identifiers `head`."""
    for r in rules:
        if (any(config.match(path, p) for p in r.get("paths", ["*"]))
                and ("markers" not in r or head & set(r["markers"]))):
            return r["name"]
    return UNMATCHED


def file_zone(rules: list[dict], path: str) -> str:
    """Zone of the file itself: rules with markers never match a file."""
    return function_zone([r for r in rules if "markers" not in r], path, set())


def head_identifiers(lines: list[str], line: int, end: int) -> set[str]:
    """Identifiers in a C++ function's head (`lines`: the source with comments blanked)."""
    from .cpp_backend import _head_lines  # noqa: PLC0415 (C++ only)
    first, brace = _head_lines(lines, line, end)
    head = lines[first - 1:brace]
    if head:
        head[-1] = head[-1].split("{", 1)[0]
    return set(IDENT.findall("\n".join(head)))


def annotate(reports: dict[str, dict], rules: list[dict], texts: dict[str, str]) -> None:
    """Add `zone` to every file record and every function record of `reports` (in place).
    `texts`: the sources by path, read only for C++ files when some rule has markers."""
    marked = any("markers" in r for r in rules)
    for path, r in reports.items():
        r["zone"] = file_zone(rules, path)
        lines = None
        if marked and path in texts and config.EXTENSIONS.get(path[path.rfind("."):]) == "cpp":
            from .cpp_backend import blank_comments  # noqa: PLC0415 (C++ only)
            lines = blank_comments(texts[path]).splitlines()
        for f in r["functions"]:
            head = head_identifiers(lines, f["line"], f["end_line"]) if lines is not None else set()
            f["zone"] = function_zone(rules, path, head) if marked else r["zone"]


def _names(rules: list[dict], reports: dict[str, dict]) -> list[str]:
    """Zone names in rule order, then "-" when some file or function is unmatched."""
    names = list(dict.fromkeys(r["name"] for r in rules))
    used = {r["zone"] for r in reports.values()} | {f["zone"] for r in reports.values() for f in r["functions"]}
    return names + ([UNMATCHED] if UNMATCHED in used else [])


def zone_totals(reports: dict[str, dict], rules: list[dict]) -> dict[str, dict]:
    """Per zone: function figures from the functions in the zone (debt per kNLOC over their
    nloc), file figures from the files whose file zone it is (health weighted by nloc)."""
    out = {}
    for z in _names(rules, reports):
        fns = [f for r in reports.values() for f in r["functions"] if f["zone"] == z]
        files = [r for r in reports.values() if r["zone"] == z]
        fn_nloc = sum(f["nloc"] for f in fns)
        debt = sum(f["debt"] for f in fns)
        nloc = sum(r["nloc"] for r in files)
        out[z] = {"functions": len(fns), "fn_nloc": fn_nloc, "debt": round(debt, 2),
                  "debt_per_kloc": round(1000 * debt / fn_nloc, 2) if fn_nloc else 0.0,
                  "functions_with_debt": sum(f["debt"] > 0 for f in fns),
                  "brain_functions": sum(f["brain"] for f in fns),
                  "files": len(files), "nloc": nloc,
                  "health": round(sum(r["health"] * r["nloc"] for r in files) / nloc, 2) if nloc else 10.0,
                  "file_smells": sum(len(r["file_smells"]) for r in files),
                  "errors": sum(r["error"] is not None for r in files)}
    return out


def table(zt: dict[str, dict]) -> list[str]:
    """Scan's zone table."""
    rows = [f"{'zone':20} {'files':>5} {'nloc':>6} {'fns':>5} {'fn_nloc':>7} {'debt':>8} "
            f"{'/kNLOC':>7} {'w/debt':>6} {'brain':>5} {'health':>6}"]
    rows += [f"{z[-20:]:20} {x['files']:5} {x['nloc']:6} {x['functions']:5} {x['fn_nloc']:7} "
             f"{x['debt']:8.1f} {x['debt_per_kloc']:7.1f} {x['functions_with_debt']:6} "
             f"{x['brain_functions']:5} {x['health']:6.1f}" for z, x in zt.items()]
    return rows


def compare(rb: dict[str, dict], rh: dict[str, dict], rules: list[dict]) -> dict[str, dict]:
    """Per zone over the touched files: debt before and after, and the head's new brain
    functions (a brain whose name no base function had as a brain, as diff and gate judge it)."""
    base_brains = {f["name"] for r in rb.values() for f in r["functions"] if f["brain"]}
    out = {}
    for z in list(dict.fromkeys(_names(rules, rb) + _names(rules, rh))):
        before = sum(f["debt"] for r in rb.values() for f in r["functions"] if f["zone"] == z)
        after = sum(f["debt"] for r in rh.values() for f in r["functions"] if f["zone"] == z)
        new = sum(f["brain"] and f["name"] not in base_brains
                  for r in rh.values() for f in r["functions"] if f["zone"] == z)
        out[z] = {"debt_before": round(before, 3), "debt_after": round(after, 3), "new_brains": new}
    return out


def compare_text(zc: dict[str, dict]) -> str:
    """One line for the gate's debt detail: zone debt before -> after (new brains)."""
    return "zones " + ", ".join(f"{z} {x['debt_before']:.1f} -> {x['debt_after']:.1f}"
                                + (f" (+{x['new_brains']} brain)" if x["new_brains"] else "")
                                for z, x in zc.items())
