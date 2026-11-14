import json
import subprocess
import sys

import pytest
from test_git import commit, git

from cce_code_health.cli import main
from cce_code_health.gate import _public_api

PY = sys.executable
CONFIG = f"""
schema = 1
[scope]
include = ["pkg/*"]
[gate]
frozen = ["frozen/*"]
forbid = ["tests/*"]
literals = true
public_api = true
[gate.env]
PYTHONHASHSEED = "0"
[[gate.tier]]
name = "unit"
cmd = '{PY} -c "import pkg.mod as m; assert m.calc(3) == 10"'
[[gate.tier]]
name = "golden"
kind = "reference"
cmd = '{PY} -c "import pkg.mod as m, sys; open(sys.argv[1] + \\"/r.txt\\", \\"w\\").write(repr(m.calc(7)))" {{out}}'
outputs = ["r.txt"]
[[gate.tier]]
name = "slow"
cadence = "campaign"
behaviour = false
cmd = "exit 3"
"""
MOD = '''
LABEL = "calc"


def calc(x):
    """Docstrings may change."""
    return x * 3 + 1
'''


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": CONFIG, "pkg/__init__.py": "", "pkg/mod.py": MOD,
                      "frozen/ident.py": "X = 1\n", "tests/test_x.py": "def test(): pass\n"})
    return tmp_path


def gate(repo, *extra):
    return main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log"), *extra])


def edit(repo, path, text):
    (repo / path).write_text(text)


def test_pure_refactor_passes(repo):
    edit(repo, "pkg/mod.py", MOD.replace("return x * 3 + 1", "return _triple(x) + 1")
         .replace("Docstrings may change.", "Changed doc.") + "\n\ndef _triple(x):\n    return 3 * x\n")
    assert gate(repo) == 0


@pytest.mark.parametrize("old,new,why", [
    ("x * 3 + 1", "x * 3 + 2", "unit"),
    ("x * 3 + 1", "x * 3 + 1 if x < 5 else x * 3", "golden"),
    ('"calc"', '"calc2"', "literals"),
    ("def calc(x):", "def calc(x, y=0):", "public_api"),
])
def test_behaviour_or_surface_change_fails(repo, capsys, old, new, why):
    edit(repo, "pkg/mod.py", MOD.replace(old, new))
    assert gate(repo) == 1
    out = capsys.readouterr().out
    assert f"FAIL {why}" in out


def test_new_public_name_fails(repo):
    edit(repo, "pkg/mod.py", MOD + "\n\ndef triple(x):\n    return 3 * x\n")
    assert gate(repo) == 1


def test_frozen_and_forbidden_paths_fail(repo, capsys):
    edit(repo, "frozen/ident.py", "X = 1  # tidy\n")
    assert gate(repo) == 1
    git(repo, "checkout", "-q", "--", "frozen/ident.py")
    edit(repo, "tests/test_x.py", "def test(): assert True\n")
    assert gate(repo) == 1
    assert "FAIL frozen" in capsys.readouterr().out


def test_campaign_cadence_runs_slow_tier(repo):
    edit(repo, "pkg/mod.py", MOD.replace("Docstrings may change.", "Changed doc."))
    assert gate(repo) == 0
    assert gate(repo, "--cadence", "campaign") == 1


def test_nondeterministic_reference_is_flagged(repo, capsys):
    cfg = (repo / ".codehealth/config.toml").read_text().replace(
        "repr(m.calc(7))", "repr(__import__(\\\"os\\\").urandom(8))")
    commit(repo, {".codehealth/config.toml": cfg})
    edit(repo, "pkg/mod.py", MOD.replace("Docstrings may change.", "Changed doc."))
    assert gate(repo) == 1
    assert "not deterministic" in capsys.readouterr().out


def doc_edit(repo):
    edit(repo, "pkg/mod.py", MOD.replace("Docstrings may change.", "Changed doc."))


def set_config(repo, cfg):
    commit(repo, {".codehealth/config.toml": cfg})


def test_out_of_scope_path_fails(repo, capsys):
    doc_edit(repo)
    edit(repo, "README.md", "hi\n")
    assert gate(repo) == 1
    assert "FAIL scope" in capsys.readouterr().out


def test_codehealth_dir_always_forbidden(repo, capsys):
    doc_edit(repo)
    edit(repo, ".codehealth/config.toml", CONFIG + "\n# loosened\n")
    assert gate(repo) == 1
    assert "FAIL forbid" in capsys.readouterr().out


def test_frozen_cmd_runs_at_base(repo, capsys):
    set_config(repo, CONFIG.replace("[gate]\n", '[gate]\nfrozen_cmd = "cat .frozen"\n', 1))
    commit(repo, {".frozen": "pkg/mod.py\n"})
    doc_edit(repo)
    edit(repo, ".frozen", "")                       # the working tree must not choose the list
    assert gate(repo) == 1
    assert "FAIL frozen" in capsys.readouterr().out


def test_empty_diff_fails(repo, capsys):
    assert gate(repo) == 1
    assert "nothing to gate" in capsys.readouterr().out


def test_trial_config_exits_3(repo, capsys):
    trial = repo.parent / "trial.toml"
    trial.write_text(CONFIG)
    doc_edit(repo)
    assert gate(repo, "--config", str(trial)) == 3
    assert "GATE TRIAL" in capsys.readouterr().out
    edit(repo, "pkg/mod.py", MOD.replace("x * 3 + 1", "x * 3 + 2"))
    assert gate(repo, "--config", str(trial)) == 1


def test_refactor_without_reference_or_command_tier_fails(repo, capsys):
    set_config(repo, CONFIG.split("[[gate.tier]]")[0])
    doc_edit(repo)
    assert gate(repo) == 1
    assert "refactor mode needs" in capsys.readouterr().out


def test_self_test_passes(repo, capsys):
    assert main(["gate", "--repo", str(repo), "--self-test", "--log", str(repo.parent / "s.log")]) == 0
    assert "SELF-TEST PASS" in capsys.readouterr().out
    assert main(["gate", "--repo", str(repo), "--self-test", "--mutate", "pkg/mod.py:7",
                 "--log", str(repo.parent / "s.log")]) == 0
    assert not (repo / ".git/codehealth/selftest").exists()


def test_self_test_fails_when_tier_imports_head_checkout(repo, capsys):
    cfg = CONFIG.replace("import pkg.mod as m, sys; open",
                         f'import sys; sys.path.insert(0, \\"{repo}\\"); import pkg.mod as m; open')
    assert str(repo) in cfg
    set_config(repo, cfg)
    assert main(["gate", "--repo", str(repo), "--self-test", "--log", str(repo.parent / "s.log")]) == 1
    assert "SELF-TEST FAIL" in capsys.readouterr().out


def test_removed_module_constant_fails_public_api(repo, capsys):
    edit(repo, "pkg/mod.py", MOD.replace('LABEL = "calc"\n', ""))
    assert gate(repo) == 1
    assert "LABEL removed" in capsys.readouterr().out


def test_public_api_decorator_dataclass_field_all_reexport():
    base = "def f(x):\n    pass\n\n@dataclass\nclass C:\n    a: int\n    b: float = 1.0\n\nK = 1\n"
    head = base.replace("def f", "@cache\ndef f").replace("a: int", "a: str")
    ab, ah = _public_api(base), _public_api(head)
    assert ab["f"] != ah["f"] and "@cache" in ah["f"]
    assert ab["C.a"] != ah["C.a"] and "C.b" in ab and ab["K"] == "const"
    assert "K" not in _public_api(base.replace("K = 1\n", ""))
    assert _public_api('__all__ = ["g", "f"]\n')["__all__"] == "['f', 'g']"
    assert "C.__len__" in _public_api("class C:\n    def __len__(self): return 1\n")
    assert _public_api("from .m import a, _b, d as e\n", "pkg/__init__.py") == {
        "a": "re-export", "e": "re-export"}
    assert _public_api("from .m import a\n", "pkg/x.py") == {}


def test_self_test_with_command_tiers_only(tmp_path, capsys):
    cfg = CONFIG.split("[[gate.tier]]")[0] + f"""
[[gate.tier]]
name = "lint"
behaviour = false
cmd = "exit 0"
[[gate.tier]]
name = "unit"
cmd = '{PY} -c "import pkg.mod as m; assert m.calc(3) == 10"'
"""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, ".gitignore": "__pycache__/\n",
                      "pkg/__init__.py": "", "pkg/mod.py": MOD})
    log = str(tmp_path.parent / "s.log")
    assert main(["gate", "--repo", str(tmp_path), "--self-test", "--log", log]) == 0
    weak = cfg.replace("m.calc(3) == 10", "True")
    commit(tmp_path, {".codehealth/config.toml": weak})
    assert main(["gate", "--repo", str(tmp_path), "--self-test", "--log", log]) == 1


def test_debt_fails_when_analysis_newly_errors(repo, capsys, monkeypatch):
    from cce_code_health import python_backend
    real = python_backend.analyze

    def flaky(path, text, *a, **kw):
        fm = real(path, text, *a, **kw)
        if "BOOM" in text:
            fm.error, fm.functions = "complexipy: boom", []
        return fm
    monkeypatch.setattr("cce_code_health.cli.analyze", flaky)
    edit(repo, "pkg/mod.py", MOD + "# BOOM\n")
    assert gate(repo) == 1
    out = capsys.readouterr().out
    assert "FAIL debt" in out and "pkg/mod.py: analysis error: complexipy: boom" in out
    assert main(["diff", "--repo", str(repo)]) == 1
    assert "FAIL pkg/mod.py: analysis error: complexipy: boom" in capsys.readouterr().out


def test_step_crossing_file_threshold_fails(repo, capsys):
    edit(repo, "pkg/big.py", "".join(f"_x{i} = {i}\n" for i in range(1100)))
    assert gate(repo) == 1
    out = capsys.readouterr().out
    assert "FAIL debt" in out and "pkg/big.py: new file smell(s) large_file (nloc 1100)" in out


def test_campaign_cadence_excuses_docs_only_commits(repo, capsys):
    edit(repo, "pkg/mod.py", MOD.replace("return x * 3 + 1", "return _triple(x) + 1")
         + "\n\ndef _triple(x):\n    return 3 * x\n")
    git(repo, "commit", "-qam", "refactor step")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"], capture_output=True,
                          text=True, check=True).stdout.strip()
    (repo / ".codehealth/playbook.md").write_text("# playbook\n")
    (repo / "docs").mkdir()
    (repo / "docs/notes.md").write_text("notes\n")
    git(repo, "add", ".codehealth/playbook.md", "docs/notes.md")
    git(repo, "commit", "-qm", "docs: playbook")
    assert gate(repo, "--base", base) == 1                           # step cadence: refused
    capsys.readouterr()
    assert gate(repo, "--base", base, "--cadence", "campaign", "--json", str(repo.parent / "camp.json")) in (0, 1)
    checks = {c["name"]: c for c in json.loads((repo.parent / "camp.json").read_text())["checks"]}
    assert checks["forbid"]["ok"] and checks["scope"]["ok"]
    assert "2 path(s) touched only by docs/config commits" in checks["forbid"]["detail"]
    (repo / "docs/notes.md").write_text("changed in the worktree\n")  # not committed: not excused
    assert gate(repo, "--base", base, "--cadence", "campaign", "--json", str(repo.parent / "camp.json")) == 1
    checks = {c["name"]: c for c in json.loads((repo.parent / "camp.json").read_text())["checks"]}
    assert not checks["scope"]["ok"] and "docs/notes.md" in checks["scope"]["detail"]


def _rep(path, nloc, dup, smells):
    return {"path": path, "nloc": nloc, "dup_lines": dup, "dup_fraction": dup / nloc, "file_smells": smells}


def test_concentrated_duplication_is_a_note_added_duplication_fails():
    from cce_code_health.report import new_file_smells
    rb = {"big.h": _rep("big.h", 1003, 59, ["large_file"])}
    changes = [("M", "big.h", "big.h"), ("A", None, "part.h")]
    split = {"big.h": _rep("big.h", 484, 0, []), "part.h": _rep("part.h", 519, 59, ["duplication"])}
    errors, notes = new_file_smells(rb, split, changes)
    assert errors == [] and len(notes) == 1 and "part.h: duplication 11% of nloc 519, concentrated" in notes[0]
    added = {"big.h": _rep("big.h", 484, 0, []), "part.h": _rep("part.h", 519, 70, ["duplication"])}
    errors, notes = new_file_smells(rb, added, changes)
    assert errors == ["part.h: new file smell(s) duplication (nloc 519)"] and notes == []
