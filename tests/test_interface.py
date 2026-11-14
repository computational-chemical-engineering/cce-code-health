import json

import pytest
from test_gate import CONFIG, MOD
from test_git import commit, git

from cce_code_health.cli import main
from cce_code_health.gate import _public_api
from cce_code_health.interface import caller_problems, public_api_problems

PLAN_PATH = ".codehealth/interface/adr.toml"
PLAN = """
id = "adr-1"
approved_by = "reviewer"
reason = "ADR 1: drop legacy, rename old_name, drop compat"
removed = ["pkg/mod.py:legacy", "pkg/compat.py"]
renamed = {"pkg/mod.py:old_name" = "pkg/mod.py:new_name"}
callers = ["tests/*"]
"""
BASE = MOD + '''

__all__ = ["calc", "legacy", "old_name", "LABEL"]


def legacy(y):
    return y


def old_name(z, k=1):
    return z * k
'''
HEAD = MOD + '''

__all__ = ["calc", "new_name", "LABEL"]


def new_name(z, k=1):
    return z * k
'''


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": CONFIG, PLAN_PATH: PLAN,
                      "pkg/__init__.py": "", "pkg/mod.py": BASE, "pkg/compat.py": "def shim():\n    return 1\n",
                      "frozen/ident.py": "X = 1\n",
                      "tests/test_x.py": "from pkg.mod import old_name\n\n\ndef test():\n    assert old_name(2) == 2\n"})
    return tmp_path


def gate(repo, *extra, plan=PLAN_PATH):
    return main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log"),
                 "--mode", "interface", "--plan", plan, *extra])


def apply(repo, head=HEAD, compat=False, tests=True):
    (repo / "pkg/mod.py").write_text(head)
    if not compat:
        (repo / "pkg/compat.py").unlink()
    if tests:
        (repo / "tests/test_x.py").write_text(
            "from pkg.mod import new_name\n\n\ndef test():\n    assert new_name(2) == 2\n")


def test_exact_plan_passes_and_logs_interface(repo, capsys):
    apply(repo)
    gj = repo.parent / "g.json"
    assert gate(repo, "--json", str(gj)) == 0
    out = capsys.readouterr().out
    assert "ok   plan" in out and "ok   public_api" in out and "ok   literals" in out
    meta = json.loads(gj.read_text())["meta"]
    assert meta["mode"] == "interface" and meta["plan"]["id"] == "adr-1"
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "adr step")
    runs = repo.parent / "interface-runs.jsonl"
    assert main(["log", "--repo", str(repo), "--gate-json", str(gj), "--campaign", "c1",
                 "--target", "pkg/mod.py::old_name", "--recipe", "adr", "--out", str(runs)]) == 0
    rec = json.loads(runs.read_text())
    assert rec["class"] == "interface" and rec["result"] == "accepted"


def test_listed_change_not_made_fails(repo, capsys):
    apply(repo, compat=True)
    assert gate(repo) == 1
    assert "plan removed pkg/compat.py: the module was not removed by this step" in capsys.readouterr().out


def test_caller_edit_needs_callers(repo, capsys):
    (repo / PLAN_PATH).write_text(PLAN.replace('callers = ["tests/*"]\n', ""))
    git(repo, "commit", "-qam", "plan without callers")
    apply(repo)
    assert gate(repo) == 1
    out = capsys.readouterr().out
    assert "FAIL forbid" in out and "tests/test_x.py: outside the scope or forbidden" in out


def test_plan_is_read_at_base_and_validated(repo, capsys):
    (repo / ".codehealth/interface/new.toml").write_text(PLAN)
    assert gate(repo, plan=".codehealth/interface/new.toml") == 2
    assert "does not exist at the base" in capsys.readouterr().err
    assert gate(repo, plan=".codehealth/restructure/x.toml") == 2
    assert "--plan must be a .toml file under .codehealth/interface/" in capsys.readouterr().err


def check(base: dict, head: dict, plan: dict) -> list[str]:
    full = {"removed": [], "renamed": {}, "changed": [], "added": [], "new_files": [], "callers": [], **plan}
    py = sorted({(p if p in base else None, p if p in head else None) for p in {*base, *head}},
                key=str)
    return public_api_problems(full, py, base, head, _public_api)[0]


def test_rule_unplanned_removal_and_addition():
    base = {"m.py": "def a():\n    pass\n\n\ndef b():\n    pass\n"}
    head = {"m.py": "def a():\n    pass\n\n\ndef c():\n    pass\n"}
    probs = check(base, head, {"removed": ["m.py:b"]})
    assert probs == ["m.py: c added (not in the plan)"]
    assert check(base, head, {"removed": ["m.py:b"], "added": ["m.py:c"]}) == []
    assert check(base, head, {"renamed": {"m.py:b": "m.py:c"}}) == []


def test_rule_rename_must_keep_api_and_members():
    base = {"m.py": "class A:\n    def run(self, x):\n        pass\n"}
    assert check(base, {"m.py": "class B:\n    def run(self, x):\n        pass\n"},
                 {"renamed": {"m.py:A": "m.py:B"}}) == []
    probs = check(base, {"m.py": "class B:\n    def run(self, x, y):\n        pass\n"},
                  {"renamed": {"m.py:A": "m.py:B"}})
    assert probs == ["plan renamed m.py:A = m.py:B: member A.run differs or is missing"]
    probs = check(base, {"m.py": "class B:\n    def run(self, x):\n        pass\n\n    def go(self):\n        pass\n"},
                  {"renamed": {"m.py:A": "m.py:B"}})
    assert probs == ["m.py: B.go added to renamed B (not in the plan)"]


def test_rule_signature_change_and_unmade_entries_fail():
    base = {"m.py": "def a(x):\n    pass\n"}
    assert check(base, {"m.py": "def a(x, y):\n    pass\n"}, {"added": ["m.py:z"]}) == [
        "plan added m.py:z: not new at head", "m.py: a(x) -> (x, y) (not in the plan's changed)"]
    assert check(base, base, {"removed": ["m.py:a"]}) == [
        "plan removed m.py:a: still bound at head (or the file is not touched)"]


def test_rule_module_rename_and_all():
    base = {"p/a.py": '__all__ = ["f"]\n\n\ndef f():\n    pass\n'}
    head = {"q/a.py": '__all__ = ["f"]\n\n\ndef f():\n    pass\n'}
    assert check(base, head, {"renamed": {"p/a.py": "q/a.py"}}) == []
    probs = check(base, {"q/a.py": "def f(x):\n    pass\n"}, {"renamed": {"p/a.py": "q/a.py"}})
    assert len(probs) == 1 and probs[0].startswith("plan renamed p/a.py = q/a.py: the API differs")
    base = {"m.py": '__all__ = ["f", "g"]\n\n\ndef f():\n    pass\n\n\ndef g():\n    pass\n'}
    head = {"m.py": '__all__ = ["f", "h"]\n\n\ndef f():\n    pass\n\n\ndef g():\n    pass\n'}
    assert "m.py: __all__ changed beyond the plan's names for this file: ['g', 'h']" in check(
        base, head, {"added": ["m.py:f"]})


# -- regressions from the think review (2026-11-14): each probe passed before the fix ---------

@pytest.mark.parametrize("base,head,plan,expect", [
    ({"a.py": "class Solver:\n    pass\n"},
     {"a.py": "from core.solver import Solver\n", "core/solver.py": "class Solver:\n    pass\n"},
     {"renamed": {"a.py:Solver": "core/solver.py:Solver"}}, "Solver is still bound in a.py"),
    ({"m.py": "def f():\n    pass\n\n\ndef g():\n    pass\n"},
     {"m.py": "def g():\n    pass\n\n\ndef __getattr__(n):\n    return g\n"},
     {"removed": ["m.py:f"]}, "m.py: __getattr__ added (not in the plan)"),
    ({"m.py": "def f():\n    pass\n"}, {"m.py": "if True:\n    def f():\n        pass\n"},
     {"removed": ["m.py:f"]}, "plan removed m.py:f: still bound"),
    ({"m.py": "def f():\n    pass\n\n\ndef old():\n    pass\n"},
     {"m.py": "def f():\n    pass\n\n\ntry:\n    def extra():\n        pass\nexcept Exception:\n    pass\n"},
     {"removed": ["m.py:old"]}, "m.py: extra added (not in the plan)"),
    ({"m.py": "class A:\n    def run(self):\n        pass\n"},
     {"m.py": "class A:\n    def _run(self):\n        pass\n    run = _run\n"},
     {"removed": ["m.py:A.run"]}, "plan removed m.py:A.run: still bound"),
    ({}, {"core/b.py": "_x = 1\n"}, {"renamed": {"b.py": "core/b.py"}}, "b.py was not removed by this step"),
    ({"m.py": '__all__ = ["f"]\n\n\ndef f():\n    pass\n\n\ndef old():\n    pass\n'},
     {"m.py": 'def f():\n    pass\n'}, {"removed": ["m.py:old"]}, "m.py: __all__ removed (not in the plan)"),
    ({"x.py": '__all__ = ["Solver", "h"]\n\n\nclass Solver:\n    pass\n\n\ndef h():\n    pass\n',
      "a.py": "def Solver():\n    pass\n"},
     {"x.py": '__all__ = ["h"]\n\n\nclass Solver:\n    pass\n\n\ndef h():\n    pass\n',
      "a.py": "def Solver2():\n    pass\n"},
     {"renamed": {"a.py:Solver": "a.py:Solver2"}}, "x.py: __all__ changed beyond the plan's names"),
    ({"pkg/__init__.py": "from .a import Solver\n"}, {"pkg/__init__.py": "from .b import helper as Solver2\n"},
     {"renamed": {"pkg/__init__.py:Solver": "pkg/__init__.py:Solver2"}}, "API differs"),
    ({"m.py": "TOL = 1e-8\n"}, {"m.py": "TOLERANCE = 'x'\n"},
     {"renamed": {"m.py:TOL": "m.py:TOLERANCE"}}, "API differs: const = 1e-08 -> const = 'x'"),
])
def test_review_probes_fail(base, head, plan, expect):
    probs = check(base, head, plan)
    assert any(expect in p for p in probs), probs


def test_class_rename_with_listed_member_and_changed():
    base = {"m.py": "class A:\n    def run(self):\n        pass\n\n\ndef go(x):\n    pass\n"}
    head = {"m.py": "class B:\n    def run(self):\n        pass\n\n    def go(self):\n        pass\n\n\n"
                    "def go(x, report):\n    pass\n"}
    assert check(base, head, {"renamed": {"m.py:A": "m.py:B"}, "added": ["m.py:B.go"], "changed": ["m.py:go"]}) == []
    assert check(base, head, {"renamed": {"m.py:A": "m.py:B"}, "added": ["m.py:B.go"]}) == [
        "m.py: go(x) -> (x, report) (not in the plan's changed)"]
    assert check(base, base, {"changed": ["m.py:go"]}) == [
        "plan changed m.py:go: its API did not change (or the file is not touched)"]


def test_interface_needs_public_api(repo, capsys):
    (repo / ".codehealth/config.toml").write_text(CONFIG.replace("public_api = true", "public_api = false"))
    git(repo, "commit", "-qam", "public_api off")
    apply(repo)
    assert gate(repo) == 2
    assert "--mode interface needs [gate] public_api = true" in capsys.readouterr().err


def test_callers_may_only_follow_the_plan(repo, capsys):
    apply(repo, tests=False)
    (repo / "tests/test_x.py").write_text("def test():\n    pass\n")                  # weakened
    assert gate(repo) == 1
    assert "FAIL callers" in capsys.readouterr().out
    apply(repo, compat=True)                                                         # exact follow
    (repo / "tests/conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
    assert gate(repo) == 1
    out = capsys.readouterr().out
    assert "tests/conftest.py: a caller file may not be added" in out


def test_callers_rename_import_and_changed_call():
    from cce_code_health.interface import caller_problems
    plan = {"removed": ["src/pkg/m.py:legacy"], "renamed": {"src/pkg/m.py:old": "src/pkg/core/n.py:new"},
            "changed": ["src/pkg/m.py:run"], "added": [], "callers": ["tests/*"]}
    base = ("from pkg.m import old, legacy, run\n\n\ndef test_a():\n    assert old(1) == 1\n\n\n"
            "def test_b():\n    assert legacy() == 2\n\n\ndef test_c():\n    assert run(1) == 3\n")
    head = ("from pkg.core.n import new\nfrom pkg.m import run\n\n\ndef test_a():\n    assert new(1) == 1\n\n\n"
            "def test_c():\n    assert run(1, report=None) == 3\n")
    cp = [("M", "tests/t.py", "tests/t.py")]
    assert caller_problems(plan, cp, {"tests/t.py": base}, {"tests/t.py": head}, ["src"]) == []
    weak = head.replace("assert run(1, report=None) == 3", "run(1, report=None)")
    assert caller_problems(plan, cp, {"tests/t.py": base}, {"tests/t.py": weak}, ["src"])
    shim = head.replace("from pkg.core.n import new", "from unittest.mock import MagicMock as new")
    assert any("beyond the plan" in p for p in caller_problems(plan, cp, {"tests/t.py": base},
                                                              {"tests/t.py": shim}, ["src"]))


def test_plan_validation(repo, capsys):
    bad = PLAN.replace('callers = ["tests/*"]', 'callers = ["**"]') + 'changed = ["pkg/compat.py:shim"]\n'
    (repo / PLAN_PATH).write_text(bad)
    git(repo, "commit", "-qam", "bad plan")
    assert gate(repo) == 2
    err = capsys.readouterr().err
    assert "callers entry '**' is too broad" in err
    assert "names a module the plan removes or renames" in err


def test_gate_json_records_api_text(repo):
    apply(repo)
    gj = repo.parent / "g-record.json"
    assert gate(repo, "--json", str(gj)) == 0
    rec = next(c for c in json.loads(gj.read_text())["checks"] if c["name"] == "public_api")["interface"]
    assert rec["renamed"] == [{"old": "pkg/mod.py:old_name", "new": "pkg/mod.py:new_name", "api": "(z, k=1)"}]
    assert {"name": "pkg/compat.py"} in rec["removed"]


# -- regressions from the second think review (callers rule, 2026-11-14) -------------------------

from cce_code_health.interface import caller_record  # noqa: E402

P = {"removed": ["src/pkg/m.py:legacy"], "renamed": {"src/pkg/m.py:old": "src/pkg/core/n.py:new"},
     "changed": ["src/pkg/m.py:run"], "added": [], "callers": ["tests/**"]}
H = "import pytest\nfrom pkg.m import run\n\n"


def cpb(base, head, plan=P, path="tests/t.py"):
    return caller_problems(plan, [("M", path, path)], {path: base}, {path: head}, ["src"])


@pytest.mark.parametrize("base,head,plan,path", [
    (H + "def test_c():\n    assert run(1) == 3\n", H + "@pytest.mark.skip\ndef test_c():\n    run\n", P, "tests/t.py"),
    (H + "class TestR:\n    def test_c(self):\n        assert run(1) == 3\n", H + "class TestR(object):\n    run\n",
     P, "tests/t.py"),
    (H + "def test_c():\n    assert run(1) == 3\n", H + "def test_c():\n    assert run(1) or True\n", P, "tests/t.py"),
    (H + "def solver():\n    return run(make())\n", H + "def solver():\n    return type('M', (), {'f': run})()\n",
     P, "tests/t.py"),
    (H + "from pkg.m import legacy\nclass TestAll:\n    def test_a(self):\n        assert run(1) == 3\n"
         "    def test_b(self):\n        assert legacy() == 2\n", H, P, "tests/t.py"),
    (H + "def test_e():\n    try:\n        f()\n    except KeyError:\n        pass\n",
     H + "def test_e():\n    try:\n        f()\n    except BaseException:\n        pass\n",
     {**P, "removed": [], "renamed": {}, "changed": []}, "tests/t.py"),
    ("run: 3.14159\n", "run: 2.0\n", P, "tests/data/expected.txt"),
    (H + "import subprocess\ndef test_s():\n    assert subprocess.run(['true']).returncode == 0\n",
     H + "import subprocess\ndef test_s():\n    assert subprocess.run(['false']) or 1\n", P, "tests/t.py"),
    ("def test_h(client):\n    assert client.api.status == 200\n    assert client.ok\n",
     "def test_h(client):\n    assert client.ok\n", {**P, "removed": ["src/pkg/api.py"]}, "tests/t.py"),
    ("from tests.helpers import legacy\ndef test_h():\n    assert legacy(3) == 9\n", "", P, "tests/t.py"),
    ("from pkg.m import run\ndef test_a():\n    assert run(1) == 3\ndef test_f():\n    from tests.fakes import run\n"
     "    assert run(1) == 0\n",
     "from pkg.m import run\nfrom tests.fakes import run\ndef test_a():\n    assert run(1) == 3\ndef test_f():\n"
     "    assert run(1) == 0\n", P, "tests/t.py"),
    ("from pkg.core.n import new\n\ndef test_a():\n    assert new(1) == 1\n",
     "from unittest.mock import MagicMock as new\n\ndef test_a():\n    assert new(1) == 1\n", P, "tests/t.py"),
])
def test_callers_weakening_fails(base, head, plan, path):
    assert cpb(base, head, plan, path)


PR = {**P, "renamed": {"src/pkg/m.py:run": "src/pkg/m.py:execute"}, "changed": []}
PM = {**P, "renamed": {"src/pkg/b.py": "src/pkg/core/b.py", "src/pkg/b.py:old": "src/pkg/core/b.py:new"}}
PC = {**P, "renamed": {"src/pkg/a.py:LocalApplication": "src/pkg/b.py:LocalApplication"}, "changed": [], "removed": []}


@pytest.mark.parametrize("base,head,plan", [
    ("import subprocess\nfrom pkg.m import run\ndef test():\n    subprocess.run(['true'])\n    assert run(1) == 3\n",
     "import subprocess\nfrom pkg.m import execute\ndef test():\n    subprocess.run(['true'])\n    assert execute(1) == 3\n",
     PR),
    ("from unittest import mock\ndef test():\n    with mock.patch('pkg.m.old'):\n        pass\n",
     "from unittest import mock\ndef test():\n    with mock.patch('pkg.core.n.new'):\n        pass\n", P),
    ("from pkg.m import legacy as L\ndef test():\n    assert L() == 2\ndef test_z():\n    pass\n",
     "def test_z():\n    pass\n", P),
    ("import pkg.b\ndef test():\n    assert pkg.b.old() == 1\n",
     "import pkg.core.b\ndef test():\n    assert pkg.core.b.new() == 1\n", PM),
    (H + "def t():\n    assert run(1, 2) == 3\n", H + "def t():\n    assert run(\n        1,\n        2,\n    ) == 3\n", P),
    (H + "def t():\n    assert run(1) == 3\n", H + "def t():\n    assert run(1, report=None) == 3\n", P),
    # patch-following (OpenFlowsheet Q-F5): the patch target follows a moved class or module
    ("from pkg.a import LocalApplication\n\ndef test(monkeypatch):\n"
     "    monkeypatch.setattr(LocalApplication, '_run_result', lambda s: 1)\n",
     "from pkg.b import LocalApplication\n\ndef test(monkeypatch):\n"
     "    monkeypatch.setattr(LocalApplication, '_run_result', lambda s: 1)\n", PC),
    ("import pkg.a as A\n\ndef test(monkeypatch):\n    monkeypatch.setattr(A, 'LocalApplication', None)\n",
     "import pkg.b as B\n\ndef test(monkeypatch):\n    monkeypatch.setattr(B, 'LocalApplication', None)\n", PC),
    ("from unittest import mock\n\ndef test():\n    with mock.patch.object(__import__('x'), 'y'):\n        pass\n"
     "    with mock.patch('pkg.a.LocalApplication._run_result'):\n        pass\n",
     "from unittest import mock\n\ndef test():\n    with mock.patch.object(__import__('x'), 'y'):\n        pass\n"
     "    with mock.patch('pkg.b.LocalApplication._run_result'):\n        pass\n", PC),
])
def test_callers_legitimate_follow_passes(base, head, plan):
    assert cpb(base, head, plan) == []


def test_callers_patch_target_must_follow_exactly():
    base = "from pkg.a import LocalApplication\n\ndef test(mp):\n    mp.setattr(LocalApplication, '_run_result', 1)\n"
    head = "from pkg.b import LocalApplication\n\ndef test(mp):\n    mp.setattr(LocalApplication, '_other', 1)\n"
    assert cpb(base, head, PC)


def test_callers_binary_and_record():
    assert cpb("\x00�1", "\x00�2", P, "tests/data/x.npy") == ["tests/data/x.npy: binary caller file may not change"]
    base = H + "from pkg.m import legacy\n\ndef test_b():\n    assert legacy() == 2\n\n\ndef test_c():\n    assert run(1) == 3\n"
    head = H + "\ndef test_c():\n    assert run(1, report=None) == 3\n"
    probs, rec = caller_record(P, [("M", "tests/t.py", "tests/t.py")], {"tests/t.py": base}, {"tests/t.py": head}, ["src"])
    assert probs == [] and rec["changed_calls"] == 1
    assert rec["deleted"] == ["tests/t.py:6 def test_b():"]


def test_retargeted_public_api():
    base = {"old.py": "def X():\n    pass\n\n\ndef keep():\n    pass\n"}
    head = {"old.py": "def keep():\n    pass\n"}
    homes = {"home.py": ("def X():\n    pass\n", "def X():\n    pass\n")}
    full = {"removed": [], "renamed": {}, "changed": [], "added": [], "new_files": [], "callers": [],
            "retargeted": {"old.py:X": "home.py:X"}}
    py = [("old.py", "old.py")]
    assert public_api_problems(full, py, base, head, _public_api, homes)[0] == []
    other = {"home.py": ("def X(a):\n    pass\n", "def X(a):\n    pass\n")}
    assert public_api_problems(full, py, base, head, _public_api, other)[0][0].startswith("plan retargeted old.py:X")
    shim = {"old.py": "from home import X\n\n\ndef keep():\n    pass\n"}
    assert "X is still bound in old.py" in public_api_problems(full, py, base, shim, _public_api, homes)[0][0]


LIT = '''
[[literals_added]]
file = "pkg/mod.py"
literal = "LIQUID"
reason = "private copy of the verifier's K evaluation (R-016)"
'''


def lit_plan(repo, extra=LIT):
    (repo / PLAN_PATH).write_text(PLAN + extra)
    git(repo, "commit", "-qam", "plan with literals")


def test_declared_literals_exactly(repo, capsys):
    lit_plan(repo)
    copy = '\n\ndef _phase(x):\n    return "LIQUID" if x else None\n'
    apply(repo, head=HEAD + copy)
    gj = repo.parent / "g.json"
    assert gate(repo, "--json", str(gj)) == 0
    assert "ok   literals" in capsys.readouterr().out
    d = json.loads(gj.read_text())
    lit = next(c for c in d["checks"] if c["name"] == "literals")
    assert lit["detail"] == ("as planned: added 'LIQUID' x1 in pkg/mod.py "
                             "(private copy of the verifier's K evaluation (R-016))")
    assert d["meta"]["plan"]["literals_added"][0]["literal"] == "LIQUID"
    apply(repo, head=HEAD + copy + '\n\ndef _status():\n    return "ok"\n', compat=True)   # one undeclared
    assert gate(repo) == 1
    assert "added 'ok' x1 not in the plan's literals_added" in capsys.readouterr().out
    apply(repo, compat=True)                                                        # declared, not made
    assert gate(repo) == 1
    out = capsys.readouterr().out
    assert "pkg/mod.py: 'LIQUID' changed by +0 in that file, plan says +1" in out
    assert "plan's literals_added lists 'LIQUID' x1 more than the step added" in out


def test_declared_literal_must_be_in_its_file(repo, capsys):
    lit_plan(repo, LIT.replace("pkg/mod.py", "pkg/__init__.py"))
    apply(repo, head=HEAD + '\n\ndef _phase():\n    return "LIQUID"\n')
    assert gate(repo) == 1
    assert "pkg/__init__.py: 'LIQUID' changed by +0 in that file" in capsys.readouterr().out


def test_declared_literals_validation(repo, capsys):
    bad = ('\n[[literals_added]]\nfile = "pkg/*.py"\nliteral = "X"\ncount = 0\n'
           '\n[[literals_removed]]\nfile = "pkg/mod.py"\nliteral = 3\nreason = "r"\nwhy = "x"\n')
    lit_plan(repo, bad)
    assert gate(repo) == 2
    err = capsys.readouterr().err
    for msg in ("literals_added[1]: file must be one path/to/file.py (no glob)",
                "literals_added[1]: count must be a positive integer",
                "literals_added[1]: reason must be a non-empty string",
                "literals_removed[1]: literal must be a string", "literals_removed[1]: unknown key 'why'"):
        assert msg in err
