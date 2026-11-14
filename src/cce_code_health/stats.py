"""Playbook statistics from campaign run logs (`codehealth stats`).

Per recipe: attempts, accepted, accepted-neutral, no-gain, gate-fails (by tier), review-rejects
(a `review-reject` record, or a `revert` record naming the recipe), accepted steps later reverted,
gate-fails superseded by a re-run (`log --rerun-of`; counted as reruns, not tries),
acceptance rate, distinct files, median debt drop of accepted steps, and the median `relocated`
share where the log has function data. The refactor-loop skill's playbook rules become hints:
promote after 3 kept accepts in 2 files, retire below 40 % acceptance after 5 tries, and add a
"when not" after any gate-fail.

Sources (deduplicated): every `*.jsonl` under `<git-common-dir>/codehealth/runs/` (the live log)
and `.codehealth/runs/` in the working tree (logs copied at campaign end), or the files given.
Records written by hand or by older versions may lack fields; they are counted as far as they go.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

ATTEMPT_RESULTS = ("accepted", "accepted-neutral", "no-gain", "gate-fail", "review-reject")


def read_logs(paths: list[Path]) -> tuple[list[dict], list[str]]:
    """Records from the given .jsonl files (directories: every *.jsonl in them), deduplicated;
    (records, warnings)."""
    files = []
    for p in paths:
        if p.is_dir():
            files += sorted(p.glob("*.jsonl"))
        elif p.is_file():
            files.append(p)
    seen, out, warnings = set(), [], []
    for f in files:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                warnings.append(f"{f}:{i}: not JSON, skipped")
                continue
            if not isinstance(r, dict):
                continue
            key = (r.get("campaign"), r.get("step"), r.get("result"), r.get("recipe"),
                   r.get("head_tree") or r.get("head_sha"), r.get("base_sha"), r.get("target"))
            if key not in seen:
                seen.add(key)
                out.append(r)
    return out, warnings


def _file(target) -> str:
    return str(target or "").split("::", 1)[0]


def _med(xs):
    return round(statistics.median(xs), 2) if xs else None


def recipe_stats(records: list[dict]) -> dict[str, dict]:
    reverted = {s[:10] for r in records if r.get("class") == "revert" for s in (r.get("reverts") or [])}
    # a gate-fail that a later record re-ran (`log --rerun-of`, e.g. a flaky tier) is not a try
    superseded = {(r.get("campaign"), r["rerun_of"]) for r in records if r.get("rerun_of") is not None}
    by: dict[str, dict] = {}
    for r in records:
        recipe = r.get("recipe") or "(none)"
        s = by.setdefault(recipe, {"attempts": 0, "accepted": 0, "accepted_neutral": 0, "no_gain": 0,
                                   "gate_fail": 0, "review_reject": 0, "reverted": 0, "fail_tiers": {},
                                   "classes": set(), "files": set(), "drops": [], "relocated": [],
                                   "campaigns": set(), "reruns": 0})
        res = r.get("result")
        if res == "gate-fail" and (r.get("campaign"), r.get("step")) in superseded:
            s["reruns"] += 1
            continue
        if r.get("class") == "revert":
            if res == "review-reject":
                s["review_reject"] += 1
            continue
        if res not in ATTEMPT_RESULTS:
            continue
        s["attempts"] += 1
        s["classes"].add(r.get("class") or "?")
        s["campaigns"].add(r.get("campaign") or "?")
        if res == "accepted":
            if str(r.get("head_sha") or "")[:10] in reverted and r.get("head_sha"):
                s["reverted"] += 1
                continue
            s["accepted"] += 1
            s["files"].add(_file(r.get("target")))
            if isinstance(r.get("debt_before"), (int, float)) and isinstance(r.get("debt_after"), (int, float)):
                s["drops"].append(r["debt_before"] - r["debt_after"])
            rel = (r.get("target_debt") or {}).get("relocated")
            if isinstance(rel, (int, float)):
                s["relocated"].append(rel)
        elif res == "accepted-neutral":
            s["accepted_neutral"] += 1
        elif res == "no-gain":
            s["no_gain"] += 1
        elif res == "gate-fail":
            s["gate_fail"] += 1
            tier = r.get("failed_tier") or "?"
            s["fail_tiers"][tier] = s["fail_tiers"].get(tier, 0) + 1
        elif res == "review-reject":
            s["review_reject"] += 1
    out = {}
    for recipe, s in by.items():
        tries = s["attempts"] - s["accepted_neutral"]           # neutral steps are not recipe tries
        rate = s["accepted"] / tries if tries else None
        hints = []
        if s["accepted"] >= 3 and len(s["files"]) >= 2:
            hints.append("promote")
        if tries >= 5 and rate is not None and rate < 0.4:
            hints.append("retire?")
        if s["gate_fail"]:
            hints.append("add when-not")
        if s["relocated"] and _med(s["relocated"]) >= 0.8:
            hints.append("mostly relocates")
        out[recipe] = {"attempts": s["attempts"], "tries": tries, "accepted": s["accepted"],
                       "accepted_neutral": s["accepted_neutral"], "no_gain": s["no_gain"],
                       "gate_fail": s["gate_fail"], "fail_tiers": dict(sorted(s["fail_tiers"].items())),
                       "review_reject": s["review_reject"], "reverted": s["reverted"],
                       "rate": round(rate, 2) if rate is not None else None, "files": len(s["files"]),
                       "median_drop": _med(s["drops"]), "median_relocated": _med(s["relocated"]),
                       "classes": sorted(s["classes"]), "campaigns": sorted(s["campaigns"]),
                       "reruns": s["reruns"], "hints": hints}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["attempts"], kv[0])))


def table(stats: dict[str, dict]) -> list[str]:
    rows = [f"{'recipe':26} {'tries':>5} {'acc':>4} {'neut':>4} {'nogain':>6} {'gfail':>5} {'rej':>4} "
            f"{'rate':>5} {'files':>5} {'drop':>6} {'reloc':>5}  hints / gate-fails by tier"]
    for name, s in stats.items():
        rate = f"{s['rate']:.0%}" if s["rate"] is not None else "-"
        drop = f"{s['median_drop']:.1f}" if s["median_drop"] is not None else "-"
        rel = f"{s['median_relocated']:.0%}" if s["median_relocated"] is not None else "-"
        tiers = ", ".join(f"{t} {n}" for t, n in s["fail_tiers"].items())
        reruns = f"{s['reruns']} re-run gate-fail(s) not counted" if s.get("reruns") else ""
        note = "; ".join(x for x in (", ".join(s["hints"]), tiers, reruns) if x)
        rows.append(f"{name[:26]:26} {s['tries']:5} {s['accepted']:4} {s['accepted_neutral']:4} {s['no_gain']:6} "
                    f"{s['gate_fail']:5} {s['review_reject'] + s['reverted']:4} {rate:>5} {s['files']:5} "
                    f"{drop:>6} {rel:>5}  {note}")
    return rows
