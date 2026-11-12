"""Campaign budget (--ref-base), reference cache, confidence-bound speedup, numeric literals,
reference coverage, self-test baseline, config --check, prefix and glob semantics."""
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_git import commit, git

from cce_code_health import config, gate as gatemod
from cce_code_health.cli import main
from cce_code_health.gate import speedup_lower_bound

PY = sys.executable
PINS = '[gate.env]\nOMP_NUM_THREADS = "1"\nOPENBLAS_NUM_THREADS = "1"\nMKL_NUM_THREADS = "1"\n'
MOD = "def calc(x):\n    return (x * 0.1) * 3\n\n\ndef other(x):\n    return x + 1\n"
SCRIPT = """import pathlib, sys
out, repo, counter = sys.argv[1:4]
sys.path.insert(0, repo)
open(counter, "a").write(repo + "\\n")
import pkg.mod as m
pathlib.Path(out, "r.txt").write_text(repr(m.calc(7.0)))
"""


def run(repo, *args, capsys=None):
    rc = main(["gate", "--repo", str(repo), "--log", str(repo.parent / "g.log"), *args])
    return rc, (capsys.readouterr().out if capsys else "")


def sha(repo, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref], capture_output=True,
                          text=True, check=True).stdout.strip()


def ref_config(tmp_path, extra_tier="", tolerance="", coverage=""):
    script = tmp_path.parent / "ref.py"
    script.write_text(SCRIPT)
    counter = tmp_path.parent / f"counter-{tmp_path.name}.txt"
    return f"""
schema = 1
[scope]
include = ["pkg/*"]
{PINS}
[[gate.tier]]
name = "golden"
kind = "reference"
cmd = '{PY} {script} {{out}} {{repo}} {counter}'
outputs = ["r.txt"]
{tolerance}
{coverage}
{extra_tier}
""", counter


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def start(repo, **kw):
    cfg, counter = ref_config(repo, **kw)
    commit(repo, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": cfg,
                  "pkg/__init__.py": "", "pkg/mod.py": MOD})
    return counter


BENCH = f"""[[gate.tier]]
name = "bench"
kind = "benchmark"
cmd = '{PY} -c "pass"'
primary = true
repeat = 3
min_speedup = 0.1
"""


def test_ref_base_blocks_cumulative_drift(repo, capsys):
    start(repo, extra_tier=BENCH, tolerance="tolerance = {rel = 5e-7}")
    a = sha(repo)
    commit(repo, {"pkg/mod.py": MOD.replace("* 3", "* 3.000001")})          # B: 3.3e-7 vs A
    b = sha(repo)
    commit(repo, {"pkg/mod.py": MOD.replace("* 3", "* 3.000002")})          # C: 3.3e-7 vs B, 6.7e-7 vs A
    rc, out = run(repo, "--mode", "optimize", "--base", b, capsys=capsys)
    assert rc == 0 and f"ref-base={b[:10]}" in out
    rc, out = run(repo, "--mode", "optimize", "--base", b, "--ref-base", a, capsys=capsys)
    assert rc == 1 and f"ref-base={a[:10]}" in out and "FAIL golden" in out


def test_reference_cache_is_reused(repo, capsys):
    counter = start(repo)
    commit(repo, {"pkg/mod.py": MOD.replace("(x * 0.1) * 3", "x * 0.1 * 3")})
    assert run(repo, "--base", "HEAD~1")[0] == 0
    n1 = sum("base-" in x for x in counter.read_text().splitlines())
    assert n1 == 2
    assert run(repo, "--base", "HEAD~1")[0] == 0
    assert sum("base-" in x for x in counter.read_text().splitlines()) == n1
    assert list((Path(gatemod._gitdir(repo)) / 'codehealth' / 'cache').glob('*/golden'))


def test_nondeterministic_head_fails(repo, capsys):
    start(repo)
    commit(repo, {"pkg/mod.py": "import random\n\n\ndef calc(x):\n    return random.random()\n"})
    rc, out = run(repo, "--base", "HEAD~1", capsys=capsys)
    assert rc == 1 and "not deterministic at head" in out


def test_lower_bound_cases():
    assert speedup_lower_bound([3.0, 1.0, 2.0, 5.0, 4.0]) == (1.0, False)    # n=5: P(X<=0)=1/32 <= 0.05
    assert speedup_lower_bound([3.0, 1.0, 2.0, 4.0]) == (1.0, True)          # n=4: 1/16 > 0.05
    assert speedup_lower_bound([float(i) for i in range(20, 0, -1)]) == (6.0, False)  # P(X<=5)=0.0207
    assert speedup_lower_bound([1.5] * 10) == (1.5, False)
    assert speedup_lower_bound([2.0, 1.0, 3.0], 0.5)[0] == 2.0               # n=3, P(X<=1)=0.5


def fake_bench(monkeypatch, head_times):
    heads = list(head_times)

    def fake(t, cwd, base, files, env, log):
        return 0, (1.0 if cwd == Path("BASE") else heads.pop(0)), 0.0, ""
    monkeypatch.setattr(gatemod, "_bench_run", fake)
    ctx = SimpleNamespace(repo=Path("HEAD"), ref_base="x", files="", env={}, log=io.StringIO(),
                          mode="optimize")
    return ctx, lambda: Path("BASE")


def test_extended_rounds_when_median_passes_but_bound_does_not(monkeypatch):
    # warm-up 0.5, then n=5 ratios [2,2,2,2,1]: median 2 >= 1.2, bound = min = 1 < 1.2.
    ctx, tree = fake_bench(monkeypatch, [0.5, 0.5, 0.5, 0.5, 0.5, 1.0] + [0.5] * 10)
    t = {"name": "b", "cmd": "x", "primary": True, "min_speedup": 1.2, "repeat": 5}
    ok, detail = gatemod._benchmark(t, ctx, tree)
    assert ok and "extended from 5 to 9 rounds" in detail and "lower bound 2.000" in detail
    ctx, tree = fake_bench(monkeypatch, [0.5, 0.5, 0.5, 0.5, 0.5, 1.0] + [0.5] * 10)
    ok, detail = gatemod._benchmark(dict(t, max_repeat=5), ctx, tree)
    assert not ok and "extended" not in detail
    ctx, tree = fake_bench(monkeypatch, [0.5] + [1.0] * 5)          # median below need: no extension
    ok, detail = gatemod._benchmark(t, ctx, tree)
    assert not ok and "extended" not in detail


def test_numeric_literals_report(repo, capsys):
    cfg = "schema = 1\n[[gate.tier]]\nname = 'c'\ncmd = 'exit 0'\n"
    commit(repo, {".codehealth/config.toml": cfg, "pkg/__init__.py": "", "pkg/mod.py": MOD})
    commit(repo, {"pkg/mod.py": MOD.replace("0.1", "0.2")})
    rep = repo.parent / "rep.json"
    rc, out = run(repo, "--base", "HEAD~1", "--json", str(rep), capsys=capsys)
    assert rc == 0 and "pkg/mod.py: 0.1 -> removed, 0.2 -> added" in out
    checks = {c["name"]: c for c in json.loads(rep.read_text())["checks"]}
    assert checks["numeric-literals"]["ok"] and "0.2 -> added" in checks["numeric-literals"]["detail"]


COV = "coverage = true"


def test_coverage_requires_changed_function_executed(repo, capsys):
    start(repo, coverage=COV)
    commit(repo, {"pkg/mod.py": MOD.replace("x + 1", "x + 2")})            # other(): never called
    rc, out = run(repo, "--base", "HEAD~1", capsys=capsys)
    assert rc == 1 and "reference does not execute: pkg/mod.py::other" in out
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    commit(repo, {"pkg/mod.py": MOD.replace("return (x * 0.1) * 3", "y = (x * 0.1) * 3\n    return y")})
    rc, out = run(repo, "--base", "HEAD~1", capsys=capsys)
    assert rc == 0, out


def test_self_test_fails_when_a_tier_fails_unmutated(repo, capsys):
    cfg = f"""schema = 1
[[gate.tier]]
name = "broken"
cmd = "exit 1"
[[gate.tier]]
name = "unit"
cmd = '{PY} -c "import pkg.mod as m; assert m.calc(3) > 0"'
"""
    commit(repo, {".codehealth/config.toml": cfg, ".gitignore": "__pycache__/\n",
                  "pkg/__init__.py": "", "pkg/mod.py": MOD})
    rc = main(["gate", "--repo", str(repo), "--self-test", "--log", str(repo.parent / "s.log")])
    out = capsys.readouterr().out
    assert rc == 1 and "tier broken fails without mutation" in out


def test_config_check(repo, capsys, tmp_path):
    good = f"schema = 1\n{PINS}[[gate.tier]]\nname = 'a'\ncmd = 'x'\n" \
           "[[gate.tier]]\nname = 'b'\nkind = 'benchmark'\ncmd = 'x'\nprimary = true\nmin_speedup = 1.1\n"
    f = repo.parent / "good.toml"
    f.write_text(good)
    assert main(["config", "--check", "--config", str(f)]) == 0
    bad = ("schema = 1\nbogus = 1\n[scope]\nfoo = 1\n[gate]\nbar = 1\n"
           "[[gate.tier]]\nname = 'r'\nkind = 'reference'\ncmd = 'x'\ntolerance = {rtol = 1}\nzzz = 1\n"
           "[[gate.tier]]\nname = 'b1'\nkind = 'benchmark'\ncmd = 'x'\nprimary = true\nmin_speedup = 1\n"
           "[[gate.tier]]\nname = 'b2'\nkind = 'benchmark'\ncmd = 'x'\nprimary = true\ncadence = 'daily'\nmodes = ['x']\n"
           "[[gate.tier]]\nname = 'k'\nkind = 'weird'\n")
    f.write_text(bad)
    capsys.readouterr()
    assert main(["config", "--check", "--config", str(f)]) == 1
    out = capsys.readouterr().out
    for frag in ("unknown top-level key 'bogus'", "unknown key 'foo' in [scope]", "unknown key 'bar' in [gate]",
                 "unknown key 'zzz'", "reference tier needs outputs", "unknown tolerance key 'rtol'",
                 "need [gate.env]", "exactly one benchmark tier needs primary", "cadence must be",
                 "modes entry 'x'", "kind must be one of", "cmd missing"):
        assert frag in out, frag


def test_trial_runs_frozen_cmd_in_working_tree_when_base_lacks_it(repo, capsys):
    commit(repo, {"pkg/__init__.py": "", "pkg/mod.py": MOD})
    commit(repo, {"tools/frozen.txt": "pkg/__init__.py\n", "pkg/mod.py": MOD.replace("0.1", "0.2")})
    trial = repo.parent / "trial.toml"
    trial.write_text("schema = 1\n[gate]\nfrozen_cmd = 'cat tools/frozen.txt'\n"
                     "[[gate.tier]]\nname = 'c'\ncmd = 'exit 0'\n")
    rc, out = run(repo, "--base", "HEAD~1", "--config", str(trial), capsys=capsys)
    assert "ran in the working tree" in out


def test_prefix_and_placeholders(repo, capsys):
    cfg = """schema = 1
[gate]
prefix = "GD={gitdir}"
[gate.env]
E = "{repo}"
[[gate.tier]]
name = "p"
cmd = 'sh -c "test -d \\$GD && test -d \\$E/pkg"'
"""
    commit(repo, {".codehealth/config.toml": cfg, "pkg/__init__.py": "", "pkg/mod.py": MOD})
    commit(repo, {"pkg/mod.py": MOD + "\n# c\n"})
    rc, out = run(repo, "--base", "HEAD~1", capsys=capsys)
    assert rc == 0, out


def test_glob_double_star_matches_zero_directories(repo, capsys):
    assert config.match("a/x.md", "a/**/*.md") and config.match("a/b/c/x.md", "a/**/*.md")
    assert not config.match("b/x.md", "a/**/*.md")
    cfg = config.Config(profile=config.profiles.get("python@0"), include=["a/**/*.py"])
    assert cfg.in_scope("a/x.py") and cfg.in_scope("a/b/x.py") and not cfg.in_scope("c/x.py")
    conf = "schema = 1\n[gate]\nforbid = ['docs/**/*.md']\n[[gate.tier]]\nname = 'c'\ncmd = 'exit 0'\n"
    commit(repo, {".codehealth/config.toml": conf, "pkg/__init__.py": ""})
    commit(repo, {"docs/x.md": "hi\n"})
    rc, out = run(repo, "--base", "HEAD~1", capsys=capsys)
    assert rc == 1 and "FAIL forbid" in out


def test_coverage_counts_pure_deletion_as_change(repo, capsys):
    start(repo, coverage=COV)
    extra = MOD.replace("def other(x):\n", "def other(x):\n    x = x + 1\n")
    commit(repo, {"pkg/mod.py": extra})
    commit(repo, {"pkg/mod.py": MOD})                                       # delete a line in other()
    rc, out = run(repo, "--base", "HEAD~1", capsys=capsys)
    assert rc == 1 and "reference does not execute: pkg/mod.py::other" in out
