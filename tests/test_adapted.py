"""Interface plans: private `changed` names and `adapted` caller defs (OpenFlowsheet S19 shape)."""
from test_interface import check, gate
from test_git import commit, git

from cce_code_health.interface import caller_record

REGION_B = "def _solve_region(flowsheet, state, warm_start=None):\n    return state\n"
REGION_H = "def _solve_region(call):\n    return call.state\n"
PLAN = {"removed": [], "renamed": {}, "changed": ["src/pkg/region.py:_solve_region"], "added": [],
        "callers": ["tests/*"], "adapted": ["tests/t.py:test_w.refusing"]}
SPY_B = '''import pkg.region as region_module


def test_w(monkeypatch):
    original = region_module._solve_region

    def refusing(**kwargs):
        if kwargs["warm_start"] is not None:
            raise RuntimeError("refused")
        return original(**kwargs)
    monkeypatch.setattr(region_module, "_solve_region", refusing)
    assert run() == 1
'''
SPY_H = SPY_B.replace("refusing(**kwargs)", "refusing(call)").replace('kwargs["warm_start"]', "call.warm_start") \
    .replace("original(**kwargs)", "original(call)")
MODS = ({"src/pkg/region.py": REGION_B}, {"src/pkg/region.py": REGION_H})


def callers(base, head, plan=PLAN, path="tests/t.py"):
    return caller_record(plan, [("M", path, path)], {**MODS[0], path: base}, {**MODS[1], path: head}, ["src"])


def test_private_changed_checks_the_def_signature():
    plan = {"changed": ["src/pkg/region.py:_solve_region"]}
    assert check({"src/pkg/region.py": REGION_B}, {"src/pkg/region.py": REGION_H}, plan) == []
    same = "def _solve_region(flowsheet, state, warm_start=None):\n    return None\n"
    assert check({"src/pkg/region.py": REGION_B}, {"src/pkg/region.py": same}, plan) == [
        "plan changed src/pkg/region.py:_solve_region: its signature did not change"]
    gone = "def _other(call):\n    return call\n"
    assert "exactly one def" in check({"src/pkg/region.py": REGION_B}, {"src/pkg/region.py": gone}, plan)[0]
    meth_b = {"m.py": "class A:\n    def _go(self, a, b):\n        pass\n"}
    meth_h = {"m.py": "class A:\n    def _go(self, ctx):\n        pass\n"}
    assert check(meth_b, meth_h, {"changed": ["m.py:A._go"]}) == []
    cls = {"m.py": "class _A:\n    pass\n"}
    assert "private class" in check(cls, {"m.py": "class _A(int):\n    pass\n"}, {"changed": ["m.py:_A"]})[0]


def test_monkeypatch_fake_may_read_a_bundle():
    probs, rec = callers(SPY_B, SPY_H)
    assert probs == []
    (a,) = rec["adapted"]
    assert a["name"] == "tests/t.py:test_w.refusing" and a["linked_to"] == "src/pkg/region.py:_solve_region"
    assert "kwargs" in a["old"] and "call.warm_start" in a["new"]


def test_fake_semantics_may_not_change():
    for bad in (SPY_H.replace('raise RuntimeError("refused")', "return None"),
                SPY_H.replace("call.warm_start", "call.state"),
                SPY_H.replace("assert run() == 1", "assert run() or True")):
        probs, _ = callers(SPY_B, bad)
        assert any("beyond the plan" in p for p in probs), bad


def test_wrapper_positional_reads_follow_the_signature():
    mods = ({"src/pkg/ex.py": "def _rev(plan, region, flowsheet):\n    pass\n"},
            {"src/pkg/ex.py": "def _rev(ctx, region):\n    pass\n"})
    plan = {**PLAN, "changed": ["src/pkg/ex.py:_rev"], "adapted": ["bench/run.py:_timed"], "callers": ["bench/*"]}
    base = ("import pkg.ex as ex\n\nORIG = ex._rev\n\n\ndef _timed(*args, **kwargs):\n    t = args[2]\n"
            "    print(t)\n    return ORIG(*args, **kwargs)\n\n\nsetattr(ex, '_rev', _timed)\n")
    head = base.replace("args[2]", "args[0].flowsheet")
    p = "bench/run.py"
    assert caller_record(plan, [("M", p, p)], {**mods[0], p: base}, {**mods[1], p: head}, ["src"])[0] == []
    wrong = base.replace("args[2]", "args[0].plan")
    assert caller_record(plan, [("M", p, p)], {**mods[0], p: base}, {**mods[1], p: wrong}, ["src"])[0]


def test_adapted_hygiene():
    assert callers(SPY_B, SPY_B)[0] == ["plan adapted tests/t.py:test_w.refusing: the def did not change"]
    unlinked = SPY_B.replace('    monkeypatch.setattr(region_module, "_solve_region", refusing)\n', "")
    probs, _ = callers(unlinked, unlinked.replace("refusing(**kwargs)", "refusing(call)"))
    assert any("linked to no changed name" in p for p in probs)
    probs, _ = callers(SPY_B, SPY_H, plan={**PLAN, "adapted": ["tests/t.py:test_w.missing"]})
    assert any("exactly one def" in p for p in probs)
    probs, _ = callers(SPY_B, SPY_H, plan={**PLAN, "adapted": [*PLAN["adapted"], "tests/u.py:f"]})
    assert "plan adapted tests/u.py:f: the file is not a touched caller" in probs


CONFIG = """
schema = 1
[scope]
include = ["src/*", "tests/*"]
[gate]
forbid = ["tests/*"]
literals = true
public_api = true
[[gate.tier]]
name = "unit"
kind = "command"
cmd = "true"
"""
TOML = """
id = "s19-1"
approved_by = "reviewer"
reason = "bundle the region keywords"
changed = ["src/pkg/region.py:_solve_region"]
callers = ["tests/*"]
adapted = ["tests/t.py:test_w.refusing"]
"""


def test_gate_end_to_end_with_literals_in_scope(tmp_path, capsys):
    git(tmp_path, "init", "-q", "-b", "main")
    files = {".gitignore": "__pycache__/\n", ".codehealth/config.toml": CONFIG,
             ".codehealth/interface/s19.toml": TOML, "src/pkg/__init__.py": "",
             "src/pkg/region.py": REGION_B, "tests/t.py": SPY_B}
    commit(tmp_path, files)
    (tmp_path / "src/pkg/region.py").write_text(REGION_H)
    (tmp_path / "tests/t.py").write_text(SPY_H)
    assert gate(tmp_path, plan=".codehealth/interface/s19.toml") == 0, capsys.readouterr().out
    bad = TOML.replace('adapted = ["tests/t.py:test_w.refusing"]', 'adapted = ["other/t.py:f"]')
    (tmp_path / ".codehealth/interface/s19.toml").write_text(bad)
    git(tmp_path, "commit", "-qam", "bad plan")
    assert gate(tmp_path, plan=".codehealth/interface/s19.toml") == 2
    assert "is not in a file the plan's callers cover" in capsys.readouterr().err


def test_direct_call_of_a_private_changed_name_may_take_new_arguments():
    base = "from pkg.region import _solve_region\n\n\ndef test_d():\n    assert _solve_region(1, 2) == 2\n"
    head = "from pkg.region import _solve_region\n\n\ndef test_d():\n    assert _solve_region(C(1, 2)) == 2\n"
    plan = {**PLAN, "adapted": []}
    probs, rec = callers(base, head, plan=plan)
    assert probs == [] and rec["changed_calls"] == 1
    assert callers(base, head.replace("== 2", "== 3"), plan=plan)[0]


# -- review probes (think, 2026-11-19): a fake may not change what it does --------------------

FAKE = ('import pkg.region as region_module\n\n\ndef test_w(monkeypatch):\n    original = region_module._solve_region\n\n'
        '    def refusing({params}):\n{body}\n    monkeypatch.setattr(region_module, "_solve_region", refusing)\n')


def fake(params, body):
    return FAKE.format(params=params, body="\n".join("        " + x for x in body.splitlines()))


def test_review_probes_fail():
    positional = {**PLAN, "changed": ["src/pkg/region.py:_solve_region"]}
    base = fake("flowsheet, state", "return original(flowsheet, state)")
    for head in (fake("flowsheet, state, /", "return original(state, flowsheet)"),
                 fake("flowsheet, state", "return original(flowsheet, flowsheet)")):
        assert callers(base, head, plan=positional)[0], head
    base = fake("**kwargs", 'if kwargs["warm_start"] is not None:\n    raise RuntimeError("x")\nreturn original(**kwargs)')
    dummy = fake("call, dummy=None", 'if dummy.warm_start is not None:\n    raise RuntimeError("x")\n'
                 "return original(call, dummy)")
    assert callers(base, dummy)[0]
    base = fake("warm_start, **kwargs", "g = lambda warm_start: warm_start\nreturn g(1)")
    head = fake("call", "g = lambda warm_start: call.warm_start\nreturn g(1)")
    assert callers(base, head)[0]


def test_nested_def_is_not_a_changed_name():
    base = {"m.py": "def run(x):\n    def helper(a, b):\n        pass\n"}
    head = {"m.py": "def run(x):\n    def helper(c):\n        pass\n"}
    assert "not a nested def" in check(base, head, {"changed": ["m.py:run.helper"]})[0]
