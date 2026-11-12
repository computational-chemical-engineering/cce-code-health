import sys

import pytest
from test_git import commit, git

from cce_code_health.cli import main

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
    commit(tmp_path, {".codehealth/config.toml": CONFIG, "pkg/__init__.py": "", "pkg/mod.py": MOD,
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
    assert gate(repo) == 0
    assert gate(repo, "--cadence", "campaign") == 1


def test_nondeterministic_reference_is_flagged(repo, capsys):
    cfg = (repo / ".codehealth/config.toml").read_text().replace(
        "repr(m.calc(7))", "repr(__import__(\\\"os\\\").urandom(8))")
    commit(repo, {".codehealth/config.toml": cfg})
    assert gate(repo) == 1
    assert "not deterministic" in capsys.readouterr().out
