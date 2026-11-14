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


def test_reexport_rebound_by_later_def_fails(repo, capsys):
    move(repo, old=OLD + '\n\ndef calc(x, scale=3):\n    return x * scale + 1\n')
    assert gate(repo) == 1
    assert "pkg/mod.py: calc(x) -> (x, scale=3)" in capsys.readouterr().out


def test_reexport_rebound_by_later_import_fails(repo, capsys):
    move(repo, old=OLD + "from os.path import join as calc\n")
    assert gate(repo) == 1
    assert "pkg/mod.py: calc removed" in capsys.readouterr().out


def test_reexport_matched_by_module_suffix_fails(repo, capsys):
    move(repo, old=OLD.replace("from pkg.calc_impl", "from calc_impl"))
    assert gate(repo) == 1
    assert "pkg/mod.py: calc removed" in capsys.readouterr().out


CLS_BASE = ('LABEL = "calc"\n\n\nclass Calc:\n    def run(self, x):\n        return x * 3 + 1\n\n\n'
            'def calc(x):\n    return Calc().run(x)\n')
CLS_IMPL = ('class Calc:\n    def run(self, x):\n        return x * 3 + 1\n\n'
            '    def extra(self, y):\n        return y\n')
CLS_OLD = 'from pkg.calc_impl import Calc\n\nLABEL = "calc"\n\n\ndef calc(x):\n    return Calc().run(x)\n'
CLS_PLAN = ('id = "c"\napproved_by = "r"\nreason = "move Calc"\nnew_files = ["pkg/calc_impl.py"]\n'
            'new_public = ["pkg/calc_impl.py:Calc"{}]\n')


@pytest.mark.parametrize("extra, rc", [("", 1), (', "pkg/calc_impl.py:Calc.extra"', 0)])
def test_moved_class_gains_member_only_when_listed(repo, capsys, extra, rc):
    plan = ".codehealth/restructure/c.toml"
    commit(repo, {"pkg/mod.py": CLS_BASE, plan: CLS_PLAN.format(extra)})
    move(repo, impl=CLS_IMPL, old=CLS_OLD)
    assert gate(repo, plan=plan) == rc
    out = capsys.readouterr().out
    assert ("Calc.extra added to re-exported Calc" in out) == (rc == 1)


def test_binders_accept_keyword_only_parameters_without_default():
    import ast
    from cce_code_health.restructure import _binders
    for src in ("def f(*, x): pass\n", "def g(a, *, x, y=1): pass\n", "async def h(*, x): pass\n"):
        names, star = _binders(ast.parse(src).body[0])
        assert names == {src.split()[1 if src.startswith("def") else 2].split("(")[0]} and not star


SPLIT_PLAN = """
id = "split-big"
approved_by = "reviewer"
reason = "split a large file"
new_files = ["pkg/part.py"]
new_public = []
"""


def _consts(lo, hi):
    return "".join(f"_x{i} = {i}\n" for i in range(lo, hi))


@pytest.fixture
def big(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": CONFIG,
                      ".codehealth/restructure/big.toml": SPLIT_PLAN, "pkg/__init__.py": "",
                      "pkg/mod.py": MOD + _consts(0, 1100),
                      "frozen/ident.py": "X = 1\n", "tests/test_x.py": "def test(): pass\n"})
    return tmp_path


def test_verbatim_split_of_large_file_is_a_gain(big, capsys):
    (big / "pkg/mod.py").write_text(MOD + _consts(0, 600))
    (big / "pkg/part.py").write_text(_consts(600, 1100))
    gj = big.parent / "g.json"
    assert gate(big, "--require-gain", "--json", str(gj), plan=".codehealth/restructure/big.toml") == 0
    out = capsys.readouterr().out
    assert "ok   gain" in out and "size smells 1 -> 0" in out
    meta = json.loads(gj.read_text())["meta"]
    assert meta["size_smells"] == [1, 0] and meta["debt_before"] == meta["debt_after"]
    git(big, "add", "pkg/mod.py", "pkg/part.py")
    git(big, "commit", "-qm", "split")
    runs = big.parent / "split-runs.jsonl"
    assert main(["log", "--repo", str(big), "--gate-json", str(gj), "--campaign", "c1",
                 "--target", "pkg/mod.py", "--recipe", "split", "--out", str(runs)]) == 0
    assert json.loads(runs.read_text())["result"] == "accepted"


def test_shrinking_below_size_limit_is_no_gain_in_refactor_mode(big, capsys):
    (big / "pkg/mod.py").write_text(MOD + _consts(0, 900))
    assert main(["gate", "--repo", str(big), "--log", str(big.parent / "gate.log"), "--require-gain"]) == 1
    assert "FAIL gain" in capsys.readouterr().out


def test_split_still_needs_size_smell_removed(big, capsys):
    (big / "pkg/mod.py").write_text(MOD + _consts(0, 1050))     # still large
    (big / "pkg/part.py").write_text(_consts(1050, 1100))
    assert gate(big, "--require-gain", plan=".codehealth/restructure/big.toml") == 1
    assert "no debt reduction" in capsys.readouterr().out
