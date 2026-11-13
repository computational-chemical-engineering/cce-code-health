import json

import pytest
from test_gate import CONFIG, MOD
from test_git import commit, git

from cce_code_health.cli import main

PLAN_PATH = ".codehealth/restructure/split.toml"
PLAN = """
id = "split-calc"
approved_by = "reviewer"
reason = "move calc into its own module"
new_files = ["pkg/calc_impl.py"]
new_public = ["pkg/calc_impl.py:calc"]
"""
IMPL = '''

def calc(x):
    """Docstrings may change."""
    return x * 3 + 1
'''
OLD = 'from pkg.calc_impl import calc\n\nLABEL = "calc"\n'


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": CONFIG, PLAN_PATH: PLAN,
                      "pkg/__init__.py": "", "pkg/mod.py": MOD,
                      "frozen/ident.py": "X = 1\n", "tests/test_x.py": "def test(): pass\n"})
    return tmp_path


def gate(repo, *extra, plan=PLAN_PATH):
    return main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log"),
                 "--mode", "restructure", "--plan", plan, *extra])


def move(repo, impl=IMPL, old=OLD):
    (repo / "pkg/calc_impl.py").write_text(impl)
    (repo / "pkg/mod.py").write_text(old)


def test_move_with_reexport_passes_and_logs(repo, capsys):
    move(repo)
    gj = repo.parent / "g.json"
    assert gate(repo, "--json", str(gj)) == 0
    out = capsys.readouterr().out
    assert "ok   plan" in out and "mode=restructure" in out
    meta = json.loads(gj.read_text())["meta"]
    assert meta["mode"] == "restructure" and meta["plan"]["id"] == "split-calc"
    assert meta["plan"]["path"] == PLAN_PATH and len(meta["plan"]["blob"]) == 40
    runs = repo.parent / "runs.jsonl"
    assert main(["log", "--repo", str(repo), "--gate-json", str(gj), "--campaign", "c1",
                 "--target", "pkg/mod.py::calc", "--recipe", "move", "--out", str(runs)]) == 0
    rec = json.loads(runs.read_text())
    assert rec["class"] == "restructure" and rec["result"] == "no-gain"


def test_refactor_mode_rejects_the_same_move(repo, capsys):
    move(repo)
    assert main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log")]) == 1
    assert "FAIL public_api" in capsys.readouterr().out


def test_unplanned_new_file_fails(repo, capsys):
    move(repo)
    (repo / "pkg/extra.py").write_text("_X = 1\n")
    assert gate(repo) == 1
    assert "unplanned new file: pkg/extra.py" in capsys.readouterr().out


def test_removing_old_name_fails(repo, capsys):
    move(repo, old='import pkg.calc_impl\n\nLABEL = "calc"\n')
    assert gate(repo) == 1
    assert "pkg/mod.py: calc removed" in capsys.readouterr().out


def test_reexport_with_changed_signature_fails(repo, capsys):
    move(repo, impl=IMPL.replace("def calc(x):", "def calc(x, y=0):"))
    assert gate(repo) == 1
    assert "re-exported from pkg/calc_impl.py with a different API" in capsys.readouterr().out


def test_unlisted_new_public_name_fails(repo, capsys):
    move(repo, impl=IMPL + "\n\ndef helper(x):\n    return x\n")
    assert gate(repo) == 1
    assert "pkg/calc_impl.py: helper added (not in the plan's new_public)" in capsys.readouterr().out


def test_listed_name_missing_at_head_fails(repo, capsys):
    commit(repo, {PLAN_PATH: PLAN.replace('"pkg/calc_impl.py:calc"', '"pkg/calc_impl.py:calc", "pkg/calc_impl.py:gone"')})
    move(repo)
    assert gate(repo) == 1
    assert "plan new_public pkg/calc_impl.py:gone: not a public name" in capsys.readouterr().out


def test_plan_missing_at_base_is_usage_error(repo, capsys):
    move(repo)
    (repo / ".codehealth/restructure/new.toml").write_text(PLAN)
    assert gate(repo, plan=".codehealth/restructure/new.toml") == 2
    assert "does not exist at the base" in capsys.readouterr().err
    assert main(["gate", "--repo", str(repo), "--mode", "restructure"]) == 2
    assert main(["gate", "--repo", str(repo), "--plan", PLAN_PATH]) == 2


def test_invalid_plan_is_usage_error(repo, capsys):
    commit(repo, {PLAN_PATH: PLAN + 'extra = 1\napproved_by_2 = ""\n'})
    move(repo)
    assert gate(repo) == 2
    assert "unknown key 'extra'" in capsys.readouterr().err


def test_plan_changed_in_step_is_forbidden(repo, capsys):
    move(repo)
    (repo / PLAN_PATH).write_text(PLAN.replace('new_files = ["pkg/calc_impl.py"]', 'new_files = ["pkg/*"]'))
    assert gate(repo) == 1
    assert "FAIL forbid" in capsys.readouterr().out
