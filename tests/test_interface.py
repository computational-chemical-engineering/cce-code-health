import json

import pytest
from test_gate import CONFIG, MOD
from test_git import commit, git

from cce_code_health.cli import main
from cce_code_health.gate import _public_api
from cce_code_health.interface import caller_problems, public_api_problems
from cce_code_health.gitio import BinaryText, decode

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
    assert cpb(decode(b"\x00\x011"), decode(b"\x00\x012"), P, "tests/data/x.npy") == [
        "tests/data/x.npy: binary caller file may not change"]
    assert cpb(decode(b"\xff1"), decode(b"\xff2"), P, "tests/data/x.bin") == [
        "tests/data/x.bin: binary caller file may not change"]
    # a literal U+FFFD in valid UTF-8 is text: the caller rules apply, and an unchanged line passes
    text = decode("def test():\n    assert '\ufffd' == '\ufffd'\n".encode())
    assert not isinstance(text, BinaryText) and cpb(text, text + "\n", P, "tests/test_u.py") == []
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


def merge_check(repo, *extra):
    return main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log"), "--behaviour-only", *extra])


def test_behaviour_only_merge_check_runs_tiers_without_static_checks(repo, capsys):
    apply(repo)                                                # the whole lane: names removed, tests edited
    assert main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log")]) == 1
    capsys.readouterr()
    gj = repo.parent / "mc.json"
    assert merge_check(repo, "--json", str(gj)) == 0
    out = capsys.readouterr().out
    assert "ok   unit" in out and "ok   golden" in out and "(behaviour only)" in out
    assert "public_api" not in out and "forbid" not in out and "not gated: --behaviour-only" in out
    assert "tier_config" not in out
    data = json.loads(gj.read_text())
    assert data["meta"]["behaviour_only"] is True
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lane tip")
    assert main(["log", "--repo", str(repo), "--gate-json", str(gj), "--campaign", "c1",
                 "--target", "pkg/mod.py", "--recipe", "lane", "--out", str(repo.parent / "r.jsonl")]) == 2
    assert "behaviour-only" in capsys.readouterr().err


def test_behaviour_only_still_fails_on_behaviour_and_notes_tier_config(repo, capsys):
    (repo / "pkg/mod.py").write_text(BASE.replace("x * 3 + 1", "x * 3 + 2"))
    cfg = (repo / ".codehealth/config.toml")
    cfg.write_text(cfg.read_text().replace('name = "slow"', 'name = "slower"'))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lane")
    assert merge_check(repo, "--base", "HEAD~1") == 1
    out = capsys.readouterr().out
    assert "FAIL unit" in out and "ok   tier_config" in out


def test_behaviour_only_refuses_plan_modes_and_gain(repo, capsys):
    assert merge_check(repo, "--mode", "interface", "--plan", PLAN_PATH) == 2
    assert merge_check(repo, "--require-gain") == 2
    assert "merge-check" in capsys.readouterr().err


def test_rule_planned_all_added_and_removed():
    src = "from x import y\n\n\ndef a():\n    pass\n\n\ndef _h():\n    pass\n"
    base = {"m.py": src}
    head = {"m.py": src + '\n__all__ = ["a", "y"]\n'}
    assert check(base, head, {}) == ["m.py: __all__ added (not in the plan)"]
    assert check(base, head, {"added": ["m.py:__all__"]}) == []
    assert check(head, base, {"removed": ["m.py:__all__"]}) == []
    assert check(head, base, {}) == ["m.py: __all__ removed (not in the plan)"]
    bad = {"m.py": src + '\n__all__ = ["a", "nope"]\n'}
    assert check(base, bad, {"added": ["m.py:__all__"]}) == [
        "plan added m.py:__all__: names not bound in the module at head: ['nope']"]
    dyn = {"m.py": src + '\n__all__ = [n for n in dir() if n[0] != "_"]\n'}
    assert check(base, dyn, {"added": ["m.py:__all__"]})[0].startswith(
        "plan added m.py:__all__: it must be a literal list")
    new_name = {"m.py": src + '\n\ndef b():\n    pass\n\n__all__ = ["a", "b"]\n'}
    assert check(base, new_name, {"added": ["m.py:__all__"]}) == ["m.py: b added (not in the plan)"]
    assert check(base, new_name, {"added": ["m.py:__all__", "m.py:b"]}) == []


def test_gate_accepts_planned_all_on_existing_module(repo, capsys):
    (repo / ".codehealth/interface/all.toml").write_text(
        'id = "all-1"\napproved_by = "reviewer"\nreason = "declare the API"\nadded = ["pkg/compat.py:__all__"]\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "plan")
    (repo / "pkg/compat.py").write_text('__all__ = ["shim"]\n\n\ndef shim():\n    return 1\n')
    assert gate(repo, plan=".codehealth/interface/all.toml") == 0
    out = capsys.readouterr().out
    assert "ok   public_api" in out and "ok   literals" in out


OWN_BASE = {"app/types.py": "class ApiError(Exception):\n    pass\n\n\ndef fail():\n    raise ApiError('x')\n"}
OWN_PLAN = {"renamed": {"app/types.py:ApiError": "app/errors.py:ApiError"}, "new_files": ["app/errors.py"]}
ERRORS = "class ApiError(Exception):\n    pass\n"


@pytest.mark.parametrize("imp,ok", [
    ("from app.errors import ApiError\n", True),                 # used by the old module's own code
    ("from .errors import ApiError\n", True),                    # relative form
    ("from app.errors import ApiError as ApiError\n", False),    # explicit re-export
    ("from app.errors import ApiError\n__all__ = ['ApiError', 'fail']\n", False),   # listed
    ("from app.other import ApiError\n", False),                 # not the rename's target module
])
def test_rename_old_module_may_import_what_it_uses(imp, ok):
    head = {"app/types.py": imp + "\n\ndef fail():\n    raise ApiError('x')\n", "app/errors.py": ERRORS}
    probs = check_reexport(OWN_BASE, head, OWN_PLAN)
    assert (probs == []) == ok, probs
    assert check(OWN_BASE, head, OWN_PLAN) != []           # without [gate] no_implicit_reexport: a shim


def check_reexport(base, head, plan):
    full = {"removed": [], "renamed": {}, "changed": [], "added": [], "new_files": [], "callers": [], **plan}
    py = sorted({(p if p in base else None, p if p in head else None) for p in {*base, *head}}, key=str)
    return public_api_problems(full, py, base, head, _public_api, None, ["src", "."], own_use_imports=True)[0]


USE = "\n\ndef fail():\n    raise ApiError('x')\n"


@pytest.mark.parametrize("head_types", [
    "from app.errors import ApiError\n__all__ = ['fail']\n__all__.append('ApiError')\n" + USE,
    "from app.errors import ApiError\n__all__ = ['fail']\n__all__ += ['ApiError']\n" + USE,
    "from typing import TYPE_CHECKING\nfrom app.errors import ApiError\nif TYPE_CHECKING:\n"
    "    from app.errors import ApiError as ApiError\n" + USE,
    "from errors import ApiError\n" + USE,                    # an unrelated top-level `errors` module
    "from app.errors import *\n" + USE,
    "from app.errors import ApiError\n\n\ndef fail():\n    raise ValueError('x')\n",   # unused: a shim
])
def test_rename_reexport_probes_stay_bound(head_types):
    head = {"app/types.py": head_types, "app/errors.py": ERRORS}
    assert any("still bound" in p for p in check_reexport(OWN_BASE, head, OWN_PLAN))


def test_rename_unused_import_and_package_init_stay_shims():
    head = {"app/types.py": "from app.errors import ApiError\n\n\ndef fail():\n    raise ValueError('x')\n",
            "app/errors.py": ERRORS}
    assert any("still bound" in p for p in check_reexport(OWN_BASE, head, OWN_PLAN))
    base = {"app/__init__.py": OWN_BASE["app/types.py"]}
    head = {"app/__init__.py": "from app.errors import ApiError\n\n\ndef fail():\n    raise ApiError('x')\n",
            "app/errors.py": ERRORS}
    plan = {"renamed": {"app/__init__.py:ApiError": "app/errors.py:ApiError"}, "new_files": ["app/errors.py"]}
    assert check_reexport(base, head, plan)


def test_callers_import_inside_a_code_string_may_follow_the_rename():
    code = ("SRC = '''\nimport sys\nfrom pkg.m import old\nprint(old(1))\n'''\n\n\n"
            "def test():\n    run(SRC)\n")
    head = code.replace("from pkg.m import old\nprint(old(1))", "from pkg.core.n import new\nprint(new(1))")
    assert cpb(code, head) == []
    other = code.replace("print(old(1))", "print(old(2))")              # beyond the plan inside the string
    assert cpb(code, other)


@pytest.mark.parametrize("old,new", [
    ("import pkg.m\n", "import pkg.other\n"),          # an import-time script now imports another module
    ("import pkg.m\n", ""),                             # import deleted
    ("x = 1  # noqa: E225\n\n", "x = 1\n"),            # comment and layout
])
def test_callers_code_string_other_changes_fail(old, new):
    code = "SRC = '''\nimport sys\n" + old + "print(1)\n'''\n\n\ndef test():\n    run(SRC)\n"
    assert cpb(code, code.replace(old, new))


def test_worktree_crlf_reads_as_lf():
    assert decode(b"a\r\nb\rc\n", newlines=True) == "a\nb\nc\n"
    assert decode(b"a\r\nb\n") == "a\r\nb\n"


from cce_code_health.interface import old_path_importers  # noqa: E402

MOVE = {"renamed": {"src/app/types.py:ApiError": "src/app/errors.py:ApiError"}}


@pytest.mark.parametrize("text,hit", [
    ("from app.types import ApiError\n", True),
    ("from app.types import ApiError as E\n", True),
    ("from app import types\nx = types.ApiError\n", True),
    ("from app import types as T\nx = T.ApiError\n", True),
    ("import app.types\nx = app.types.ApiError\n", True),
    ("import app.types as T\n\n\ndef f():\n    raise T.ApiError()\n", True),
    ("from app.errors import ApiError\n", False),
    ("from app import types\nx = types.other\n", False),
    ("from app import types\n\n\ndef test(mp):\n    mp.setattr(types, 'ApiError', 1)\n", False),   # patch target
    ("from unittest import mock\n\n\ndef test():\n    mock.patch('app.types.ApiError')\n", False),
])
def test_old_path_importers(text, hit):
    probs = old_path_importers(MOVE, {"tests/t.py": text}, ["src", "."], set())
    assert bool(probs) == hit, probs
    if hit:
        assert probs[0].startswith("tests/t.py:") and "takes ApiError through its old module app.types" in probs[0]


def test_old_path_importers_relative_and_exempt():
    rel = {"src/app/other.py": "from .types import ApiError\n"}
    assert old_path_importers(MOVE, rel, ["src", "."], set())
    assert old_path_importers(MOVE, rel, ["src", "."], {"src/app/other.py"}) == []


def test_gate_no_implicit_reexport_scans_untouched_importers(tmp_path, capsys):
    repo = tmp_path / "r"
    repo.mkdir()
    cfg = CONFIG.replace("public_api = true", "public_api = true\nno_implicit_reexport = true")
    plan = ('id = "mv"\napproved_by = "reviewer"\nreason = "move E"\n'
            'renamed = {"pkg/a.py:E" = "pkg/b.py:E"}\nnew_files = ["pkg/b.py"]\n')
    git(repo, "init", "-q", "-b", "main")
    commit(repo, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": cfg, ".codehealth/interface/mv.toml": plan,
                  "pkg/__init__.py": "", "pkg/mod.py": MOD, "frozen/ident.py": "X = 1\n",
                  "pkg/a.py": "class E(Exception):\n    pass\n\n\ndef f():\n    raise E()\n",
                  "tests/test_y.py": "from pkg.a import E\n\n\ndef test():\n    assert E\n"})
    (repo / "pkg/b.py").write_text("class E(Exception):\n    pass\n")
    (repo / "pkg/a.py").write_text("from pkg.b import E\n\n\ndef f():\n    raise E()\n")
    args = ["gate", "--repo", str(repo), "--log", str(tmp_path / "g.log"), "--mode", "interface",
            "--plan", ".codehealth/interface/mv.toml"]
    assert main(args) == 1
    assert "tests/test_y.py:1: takes E through its old module pkg.a" in capsys.readouterr().out
    commit(repo, {"tests/test_y.py": "from pkg.b import E\n\n\ndef test():\n    assert E\n"})   # migrated earlier
    (repo / "pkg/b.py").write_text("class E(Exception):\n    pass\n")
    (repo / "pkg/a.py").write_text("from pkg.b import E\n\n\ndef f():\n    raise E()\n")
    assert main(args) == 0, capsys.readouterr().out


# Text callers: a one-line `from M import a, b` may follow the plan's moves (OpenFlowsheet S19 I-12a).
TP = {"removed": [], "changed": [], "added": [], "callers": ["README.md", "docs/**", "examples/**"],
      "renamed": {"src/app/types.py:Change": "src/app/transaction_types.py:Change",
                  "src/app/types.py:Edit": "src/app/transaction_types.py:Edit",
                  "src/app/types.py:Port": "src/app/ports.py:Port",
                  "src/app/types.py:Old": "src/app/ports.py:New"}}
NB_OK = '    "from app.types import Port, Change\\n",\n    "x = 1\\n"\n'
NB_SPLIT = ('    "from app.transaction_types import Change\\n",\n    "from app.ports import Port\\n",\n'
            '    "x = 1\\n"\n')


@pytest.mark.parametrize("base,head,path,ok", [
    ("Use:\n\n    from app.types import Change, Edit\n", "Use:\n\n    from app.transaction_types import Change, Edit\n",
     "README.md", True),
    ("from app.types import Change as C, Edit\n", "from app.transaction_types import Change as C, Edit\n",
     "README.md", True),
    ("from app.types import Change, Edit  # see\n", "from app.transaction_types import Change, Edit  # see\n",
     "README.md", True),
    ('    "from app.types import Change, Edit\\n",\n', '    "from app.transaction_types import Change, Edit\\n",\n',
     "examples/a.ipynb", True),
    ("from app.types import Change, Port\n", "from app.transaction_types import Change\nfrom app.ports import Port\n",
     "docs/q.md", True),
    (NB_OK, NB_SPLIT, "examples/a.ipynb", True),
    (">>> from app.types import Change, Port\n",
     ">>> from app.transaction_types import Change\n>>> from app.ports import Port\n", "docs/q.md", True),
    ("from app.types import Old\nOld()\n", "from app.ports import New\nNew()\n", "docs/q.md", True),
    ("from app.types import Keep, Change\n", "from app.types import Keep\nfrom app.transaction_types import Change\n",
     "docs/q.md", True),
    ("see app.types.Change\n", "see app.transaction_types.Change\n", "docs/q.md", True),
    # refused
    ("from app.types import Change, Port\n", "from app.ports import Port\nfrom app.transaction_types import Change\n",
     "docs/q.md", False),                                                     # not the plan's order
    ('    "from app.types import Change, Port"\n',
     '    "from app.transaction_types import Change"\n    "from app.ports import Port"\n',
     "examples/a.ipynb", False),                                              # would break the JSON
    ("from app.types import Change, Port  # x\n",
     "from app.transaction_types import Change  # x\nfrom app.ports import Port  # x\n", "docs/q.md", False),
    ("from app.types import Change\n", "from app.transaction_types import Change, Edit\n", "docs/q.md", False),
    ("from app.types import Change, Edit\n", "from app.transaction_types import Change\n", "docs/q.md", False),
    ("from app.other import Change\n", "from app.transaction_types import Change\n", "docs/q.md", False),
    ("from app.types import Change, Edit\n", "from app.types import Change, Edit\nx\n", "docs/q.md", False),
])
def test_text_caller_from_import_follows_moves(base, head, path, ok):
    probs = caller_problems(TP, [("M", path, path)], {path: base}, {path: head}, ["src"])
    assert (probs == []) == ok, probs


def test_text_caller_from_import_keeps_spacing_and_tabs():
    base, head = "from app.types import Old\tas  O,Edit\n", "from app.ports import New\tas  O,Edit\n"
    assert caller_problems(TP, [("M", "d.md", "d.md")], {"d.md": base}, {"d.md": head}, ["src"]) != []  # 2 modules
    base, head = "from app.types import Old\tas  O,Port\n", "from app.ports import New\tas  O,Port\n"
    assert caller_problems(TP, [("M", "d.md", "d.md")], {"d.md": base}, {"d.md": head}, ["src"]) == []


# A moved private name (not in the public API) may be planned as `renamed` (OpenFlowsheet S19 I-12c).
PRIV_B = {"run.py": "def _c(x):\n    return x\n\n\ndef run():\n    return _c(1)\n",
          "replay.py": "def replay():\n    return 2\n"}
PRIV_H = {"run.py": "from replay import replay\n\n\ndef run():\n    return replay()\n",
          "replay.py": "def _c(x):\n    return x\n\n\ndef replay():\n    return _c(2)\n"}


def test_private_rename_accepted():
    assert check(PRIV_B, PRIV_H, {"renamed": {"run.py:_c": "replay.py:_c"}}) == []


@pytest.mark.parametrize("head,why", [
    ({**PRIV_H, "run.py": PRIV_H["run.py"] + "\n\ndef _c(x):\n    return x\n"}, "still bound"),
    ({**PRIV_H, "run.py": "from replay import replay, _c\n\n\ndef run():\n    return replay()\n"}, "still bound"),
    ({**PRIV_H, "replay.py": "def replay():\n    return 2\n"}, "is not new in"),
])
def test_private_rename_refused(head, why):
    probs = check(PRIV_B, head, {"renamed": {"run.py:_c": "replay.py:_c"}})
    assert any(why in p for p in probs), probs


def test_private_rename_needs_base_binding():
    probs = check(PRIV_B, PRIV_H, {"renamed": {"run.py:_zz": "replay.py:_c"}})
    assert any("not bound" in p or "still bound" in p for p in probs), probs


def test_private_rename_caller_follows():
    plan = {"removed": [], "changed": [], "added": [], "callers": ["tests/**"],
            "renamed": {"src/app/run.py:_c": "src/app/replay.py:_c"}}
    base = "from app.run import _c, run\n\n\ndef test():\n    assert _c(1) == run()\n"
    head = "from app.replay import _c\nfrom app.run import run\n\n\ndef test():\n    assert _c(1) == run()\n"
    t = "tests/t.py"
    assert caller_problems(plan, [("M", t, t)], {t: base}, {t: head}, ["src"]) == []
