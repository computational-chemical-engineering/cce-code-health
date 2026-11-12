import sys

import pytest
from test_git import commit, git

from cce_code_health.cli import main

PY = sys.executable
CONFIG = f"""
schema = 1
[scope]
include = ["pkg/*"]
[gate.env]
OMP_NUM_THREADS = "1"
OPENBLAS_NUM_THREADS = "1"
MKL_NUM_THREADS = "1"
[[gate.tier]]
name = "golden"
kind = "reference"
cmd = '{PY} -c "import pkg.mod as m, sys; open(sys.argv[1] + \\"/r.txt\\", \\"w\\").write(repr(m.calc(7.0)))" {{out}}'
outputs = ["r.txt"]
tolerance = {{rel = 1e-12}}
[[gate.tier]]
name = "bench"
kind = "benchmark"
cmd = '{PY} -c "import pkg.mod as m, time; time.sleep(m.DELAY)"'
primary = true
repeat = 3
max_slowdown = 0.05
min_speedup = 1.2
"""
MOD = '''
DELAY = 0.2


def calc(x):
    return (x * 0.1) * 3
'''


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": CONFIG, "pkg/__init__.py": "", "pkg/mod.py": MOD})
    return tmp_path


def gate(repo, mode):
    return main(["gate", "--repo", str(repo), "--mode", mode, "--log", str(repo.parent / "g.log")])


def edit(repo, **subs):
    text = MOD
    for old, new in subs.values():
        text = text.replace(old, new)
    (repo / "pkg/mod.py").write_text(text)


def test_roundoff_change_fails_refactor_passes_optimize(repo, capsys):
    edit(repo, a=("(x * 0.1) * 3", "x * (0.1 * 3)"), b=("DELAY = 0.2", "DELAY = 0.05"))
    assert gate(repo, "refactor") == 1
    assert "not bitwise" in capsys.readouterr().out
    assert gate(repo, "optimize") == 0
    assert "within tolerance" in capsys.readouterr().out


def test_large_change_fails_optimize(repo):
    edit(repo, a=("(x * 0.1) * 3", "(x * 0.1) * 3.000001"), b=("DELAY = 0.2", "DELAY = 0.05"))
    assert gate(repo, "optimize") == 1


def test_optimize_needs_speedup_refactor_forbids_slowdown(repo, capsys):
    edit(repo, b=("DELAY = 0.2", "DELAY = 0.2  # same"))  # same behaviour: no speedup
    assert gate(repo, "refactor") == 0
    assert gate(repo, "optimize") == 1
    edit(repo, b=("DELAY = 0.2", "DELAY = 0.4"))          # slower
    assert gate(repo, "refactor") == 1
    assert "speedup" in capsys.readouterr().out


def test_debt_may_not_rise_in_either_mode(repo):
    nested = "def calc(x):\n" + "".join("    " * (i + 1) + f"if x > {i}:\n" for i in range(6)) \
        + "    " * 7 + "return (x * 0.1) * 3\n    return (x * 0.1) * 3\n"
    (repo / "pkg/mod.py").write_text("DELAY = 0.05\n\n\n" + nested)
    assert gate(repo, "optimize") == 1
    assert gate(repo, "refactor") == 1


def set_config(repo, cfg):
    commit(repo, {".codehealth/config.toml": cfg})
    edit(repo, b=("DELAY = 0.2", "DELAY = 0.05"))


def test_benchmark_without_thread_pins_is_config_error(repo, capsys):
    set_config(repo, CONFIG.replace("OMP_NUM_THREADS", "OMP_X"))
    assert gate(repo, "refactor") == 1
    out = capsys.readouterr().out
    assert "FAIL config" in out and "OMP_NUM_THREADS" in out


def test_optimize_without_primary_benchmark_fails(repo, capsys):
    set_config(repo, CONFIG.replace("primary = true\n", ""))
    assert gate(repo, "optimize") == 1
    assert "primary = true" in capsys.readouterr().out


def test_non_primary_benchmark_needs_no_min_speedup(repo):
    second = CONFIG + f"""
[[gate.tier]]
name = "bench2"
kind = "benchmark"
cmd = '{PY} -c "import pkg.mod as m, time; time.sleep(m.DELAY)"'
repeat = 3
"""
    set_config(repo, second)
    assert gate(repo, "optimize") == 0


def test_benchmark_writing_into_tree_fails(repo, capsys):
    cfg = CONFIG.replace('"import pkg.mod as m, time; time.sleep(m.DELAY)"',
                         '"import uuid; open(uuid.uuid4().hex, chr(119))"')
    set_config(repo, cfg)
    assert gate(repo, "refactor") == 1
    assert "wrote into the tree" in capsys.readouterr().out
