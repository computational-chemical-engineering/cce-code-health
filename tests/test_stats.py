import json

from cce_code_health.cli import main
from cce_code_health.stats import read_logs, recipe_stats


def rec(step, recipe, result, target="src/a.py::f", **kw):
    return {"campaign": "c1", "step": step, "class": kw.pop("cls", "refactor"), "recipe": recipe,
            "result": result, "target": target, "debt_before": 10.0, "debt_after": 7.0, "head_tree": f"t{step}",
            "base_sha": "b", **kw}


def test_recipe_stats_counts_and_hints(tmp_path):
    rows = [rec(1, "extract-phase", "accepted", head_sha="aaaaaaaaaa11"),
            rec(2, "extract-phase", "accepted", target="src/b.py::g", target_debt={"relocated": 0.9}),
            rec(3, "extract-phase", "accepted", target="src/b.py::h", target_debt={"relocated": 0.95}),
            rec(4, "extract-phase", "accepted", target="src/c.py::k"),
            rec(5, "extract-phase", "gate-fail", failed_tier="literals"),
            rec(6, "review-note", "accepted-neutral"),
            rec(7, "extract-phase", "review-reject", cls="revert", reverts=["aaaaaaaaaa11"]),
            *[rec(10 + i, "table-dispatch", "no-gain") for i in range(5)]]
    log = tmp_path / "c1.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "c1.jsonl").write_text(log.read_text())                     # campaign-end copy: deduplicated
    recs, warnings = read_logs([log, copy])
    assert len(recs) == len(rows) and len(warnings) == 2
    st = recipe_stats(recs)
    ep = st["extract-phase"]
    assert (ep["attempts"], ep["accepted"], ep["reverted"], ep["gate_fail"], ep["review_reject"]) == (5, 3, 1, 1, 1)
    assert ep["files"] == 2 and ep["rate"] == 0.6 and ep["median_drop"] == 3.0 and ep["median_relocated"] == 0.93
    assert ep["hints"] == ["promote", "add when-not", "mostly relocates"]
    assert st["table-dispatch"]["hints"] == ["retire?"] and st["review-note"]["tries"] == 0


def test_stats_command(tmp_path, capsys):
    (tmp_path / "c1.jsonl").write_text(json.dumps(rec(1, "extract-phase", "accepted")) + "\n")
    out = tmp_path / "s.json"
    assert main(["stats", "--repo", str(tmp_path), "--runs", str(tmp_path), "--json", str(out)]) == 0
    assert "STATS" in capsys.readouterr().out
    assert json.loads(out.read_text())["recipes"]["extract-phase"]["accepted"] == 1
