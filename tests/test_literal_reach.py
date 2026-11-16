"""String-literal drops: a consolidation passes, deleting one copy of a duplicated check fails."""
from collections import Counter

from cce_code_health.gate import _string_literals, literal_drops

CHECK = '    if bc["a"] < 0:\n        raise ValueError("bad bc")\n'


def judge(tb: dict, th: dict):
    lb = sum((_string_literals(t) for t in tb.values()), Counter())
    lh = sum((_string_literals(t) for t in th.values()), Counter())
    problems, allowed = literal_drops(lb - lh, lh, tb, th)
    return problems, allowed, lh - lb


BASE = {"m.py": f"def f(bc):\n{CHECK}    return 1\n\n\ndef g(bc):\n{CHECK}    return 2\n"}


def test_t1_consolidation_into_helper_passes():
    head = {"m.py": f"def _check(bc):\n{CHECK}\n\ndef f(bc):\n    _check(bc)\n    return 1\n\n\n"
                    "def g(bc):\n    _check(bc)\n    return 2\n"}
    problems, allowed, new = judge(BASE, head)
    assert problems == [] and not new
    assert any("'bad bc'" in a and "f" in a and "g" in a for a in allowed)


def test_t2_deleting_one_copy_fails_naming_the_scope():
    head = {"m.py": f"def f(bc):\n    return 1\n\n\ndef g(bc):\n{CHECK}    return 2\n"}
    problems, _, _ = judge(BASE, head)
    assert problems and all("no longer reached from f" in p for p in problems)


def test_t3_removed_private_twin_checks_its_callers():
    twin = "def _f{x}(bc):\n" + CHECK + "    return 0\n\n\n"
    base = {"m.py": twin.format(x="a") + twin.format(x="b") + "def p(bc):\n    return _fa(bc)\n\n\n"
                    "def q(bc):\n    return _fb(bc)\n"}
    ok = {"m.py": twin.format(x="a") + "def p(bc):\n    return _fa(bc)\n\n\ndef q(bc):\n    return _fa(bc)\n"}
    bad = {"m.py": twin.format(x="a") + "def p(bc):\n    return _fa(bc)\n\n\ndef q(bc):\n    return 0\n"}
    assert judge(base, ok)[0] == []
    assert any("no longer reached from q" in p for p in judge(base, bad)[0])


def test_t4_callback_passed_not_called_counts_as_reaching():
    base = {"m.py": f"def f(bc):\n{CHECK}    return newton(lambda c: c, bc)\n\n\ndef g(bc):\n{CHECK}    return 2\n"}
    head = {"m.py": f"def _residual(bc):\n{CHECK}\n\ndef f(bc):\n    return newton(_residual, bc)\n\n\n"
                    "def g(bc):\n    _residual(bc)\n    return 2\n"}
    assert judge(base, head)[0] == []


def test_t5_helper_moved_to_another_file_passes():
    head = {"m.py": "from .h import _check\n\n\ndef f(bc):\n    _check(bc)\n    return 1\n\n\n"
                    "def g(bc):\n    _check(bc)\n    return 2\n",
            "h.py": f"def _check(bc):\n{CHECK}"}
    assert judge(BASE, head)[0] == []


def test_t6_new_or_risen_literal_and_last_copy_still_fail():
    head = {"m.py": BASE["m.py"].replace("return 2", 'return "x"')}
    assert judge(BASE, head)[2] == Counter({"'x'": 1})
    gone = {"m.py": "def f(bc):\n    return 1\n\n\ndef g(bc):\n    return 2\n"}
    assert judge(BASE, gone)[0] == ["removed 'a' x2", "removed 'bad bc' x2"]


def test_t7_residual_scope_with_helper_and_own_deletion_passes():
    # known residual: g drops its own check but already calls a helper carrying the same literal;
    # the allowed drop is listed in the detail for the reviewer
    base = {"m.py": f"def _check(bc):\n{CHECK}\n\ndef g(bc):\n    _check(bc)\n{CHECK}    return 2\n"}
    head = {"m.py": f"def _check(bc):\n{CHECK}\n\ndef g(bc):\n    _check(bc)\n    return 2\n"}
    problems, allowed, _ = judge(base, head)
    assert problems == [] and allowed
