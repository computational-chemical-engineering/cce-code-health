"""python@1 profile (docs/py1/design.md): measures, class state, clones, ratchet (gates G5, G8)."""
import ast
import json
import textwrap

import pytest
from test_git import commit, git

from cce_code_health import classstate, config, gitio, pyclones
from cce_code_health.cli import main
from cce_code_health.gate import debt_check
from cce_code_health.profiles import get
from cce_code_health.python_backend import analyze, boolops_conditions, bumps_runs
from cce_code_health.report import file_report

P1 = get("python@1")


def fm(src, p=P1):
    return analyze("x.py", textwrap.dedent(src), p.varargs_params, p.py_rules,
                   p.clone_window, p.clone_min_nodes)


def by_name(src, p=P1):
    return {f.name: f for f in fm(src, p).functions}


def func(src):
    return ast.parse(textwrap.dedent(src)).body[0]


def cls(src):
    return ast.parse(textwrap.dedent(src)).body[0]


# -- profile ------------------------------------------------------------------------------------

def test_profile_numbers():
    assert P1.thresholds == dict(cognitive=9, nloc=70, nesting=3, bumps=1, params=4, boolops=1,
                                 state_writes=0, clones=0)
    assert P1.weights == dict(cognitive=1.0, nloc=0.1, nesting=3.0, bumps=2.0, params=1.0, boolops=1.0,
                              state_writes=0.5, clones=0.2)
    assert P1.class_state_threshold == 8 and (P1.clone_window, P1.clone_min_nodes) == (3, 30)
    assert get("python@0").py_rules == frozenset()


def test_python0_record_has_no_py1_fields():
    r = file_report(fm("class A:\n    def f(self):\n        self.a = 1\n", get("python@0")), 0, get("python@0"))
    assert "class_smells" not in r and "state_writes" not in r["classes"][0]
    assert {"public", "state_writes", "clones"}.isdisjoint(r["functions"][0])


# -- G5: docstrings ------------------------------------------------------------------------------

def test_g5_docstring_lines_count_in_neither_function_nor_file_nloc():
    src = '''
        """Module
        docstring."""


        def f(x):
            """Function
            docstring
            on three lines."""
            return x


        class A:
            """Class docstring."""

            def m(self):
                """Method docstring."""
                return 1
    '''
    m = fm(src)
    assert m.nloc == 5                 # def f, return x, class A, def m, return 1
    assert [f.nloc for f in m.functions] == [2, 2]


# -- bumps as runs ----------------------------------------------------------------------------

def test_bump_runs_levels_and_separators():
    f = func("""
        def f(xs):
            for x in xs:
                if x:
                    a = 1
                    b = 2
                c = 3
                if x > 1:
                    d = 4
            e = 5
            while e:
                if e:
                    e -= 1
    """)
    # runs: [if x, a, b], [if x > 1, d], [if e, e -= 1]; `c = 3` (level 1) separates
    assert bumps_runs(f) == 3


def test_bump_runs_try_with_do_not_count_and_elif_stays():
    f = func("""
        def f(x):
            try:
                with open(x) as h:
                    if h:
                        return 1
            except OSError:
                pass
            if x == 1:
                return 1
            elif x == 2:
                return 2
    """)
    assert bumps_runs(f) == 0
    g = func("""
        def g(x):
            if x:
                if x > 1:
                    return 1
                elif x > 2:
                    return 2
    """)
    assert bumps_runs(g) == 1            # one run: if, return, elif, return (all level >= 2)


def test_bump_runs_match_and_nested_def():
    f = func("""
        def f(x):
            match x:
                case 1:
                    if x:
                        return 1
            def inner(y):
                for z in y:
                    if z:
                        return z
            return inner
    """)
    assert bumps_runs(f) == 2


def test_bumps_metric_under_python1():
    f = by_name("""
        def f(xs):
            for x in xs:
                if x:
                    a = 1
                c = 3
                if x:
                    a = 2
    """)["f"]
    assert f.bumps == 2


# -- boolops in conditions ----------------------------------------------------------------------

def test_boolops_conditions_only():
    f = func("""
        def f(a, b, c, d):
            x = a and b and c and d
            assert a or b or c
            y = 1 if (a and b and c) else 2
            if a and (b or c):
                pass
            elif not (a or b or c or d):
                pass
            while a and b:
                pass
            match a:
                case 1 if a and b and c and d and c:
                    pass
            return [z for z in d if z and a and b]
    """)
    assert boolops_conditions(f) == 4              # the match guard
    g = func("""
        def g(a, b, c):
            x = a and b and c
            if a and b:
                return x
    """)
    assert boolops_conditions(g) == 1
    assert by_name("def h(a, b):\n    return a and b and a\n")["h"].boolops == 0


# -- class state: channel rules ---------------------------------------------------------------

def test_channels_basic_and_method_counts():
    c = cls("""
        class A:
            def __init__(self):
                self.a = 0
                self.unused = 1
            def p1(self):
                self.a = 1
                self.b = 2
                self.own = 3
                return self.own
            def p2(self):
                return self.a + self.b
            def p3(self):
                self.b = 5
    """)
    ch = classstate.channels(c)
    assert ch == {"a": ["p1"], "b": ["p1", "p3"]}
    ws = {m.name: n for m, n in classstate.method_state_writes(c, ch).items()}
    assert ws == {"__init__": 0, "p1": 2, "p2": 0, "p3": 1}
    assert classstate.class_state_writes(ch) == 3


def test_constructor_helpers_fixpoint():
    c = cls("""
        class A:
            def __init__(self):
                self._setup()
            def _setup(self):
                self.a = 1
                self._more()
            def _more(self):
                self.b = 2
            def _shared(self):
                self.c = 3
            def run(self):
                self._shared()
                return self.a + self.b + self.c
            def __init_helper_dunder__(self):
                self.d = 4
    """)
    assert classstate.constructors(c) == {"__init__", "_setup", "_more"}
    assert classstate.channels(c) == {"c": ["_shared"]}


def test_helper_called_outside_constructors_is_not_one():
    c = cls("""
        class A:
            def __init__(self):
                self._reset()
            def _reset(self):
                self.a = 0
            def run(self):
                self._reset()
                return self.a
    """)
    assert "_reset" not in classstate.constructors(c)
    assert classstate.channels(c) == {"a": ["_reset"]}


def test_setters_static_class_methods_and_method_names_excluded():
    c = cls("""
        class A:
            @property
            def v(self):
                return self._v
            @v.setter
            def v(self, x):
                self._v = x
            @staticmethod
            def s(obj):
                obj.a = 1
            @classmethod
            def k(cls):
                cls.a = 2
            def read(self):
                return self.a + self._v
            def rebind(self):
                self.read = None
    """)
    assert classstate.channels(c) == {}


def test_dict_keys_attribute_chains_setattr_and_targets():
    c = cls("""
        class A:
            def w(self, i, items, ctx):
                self.d["k"] = 1
                self.e[i] = 2
                self.f.g = 3
                setattr(self, "h", 4)
                self.t1, (self.t2, *self.t3) = items
                for self.it in items:
                    pass
                with ctx as self.cm:
                    pass
                self.n += 1
                self.ann: int = 5
                self.bare: int
                del self.gone
            def r(self):
                return (self.d["k"], self.e, self.f.g, self.h, self.t1, self.t2, self.t3,
                        self.it, self.cm, self.n, self.ann, self.bare, self.gone)
    """)
    assert set(classstate.channels(c)) == {"d[k]", "d", "e", "f.g", "h", "t1", "t2", "t3", "it",
                                           "cm", "n", "ann", "gone"}


def test_class_state_records_and_display_smell():
    writers = "\n".join(f"    def w{i}(self):\n        self.x{i} = {i}\n" for i in range(9))
    src = "class Big:\n" + writers + "    def r(self):\n        return " + " + ".join(
        f"self.x{i}" for i in range(9)) + "\n"
    r = file_report(fm(src), 0, P1)
    c = r["classes"][0]
    assert c["state_writes"] == 9 and c["channels"]["x0"] == ["w0"]
    assert r["class_smells"] == [{"name": "Big", "line": 1, "smell": "class_state", "state_writes": 9}]
    assert "class_state" not in r["file_smells"]
    w0 = next(f for f in r["functions"] if f["name"] == "Big.w0")
    assert w0["state_writes"] == 1 and w0["debt_parts"] == {"state_writes": 0.5}
    assert r["health"] == 9.0
    r8 = file_report(fm(src.replace("    def w8(self):\n        self.x8 = 8\n", "")
                        .replace(" + self.x8", "")), 0, P1)
    assert r8["class_smells"] == [] and r8["health"] == 10.0


def test_public_field():
    fs = {f.name: f.public for f in fm("""
        def f(): pass
        def _g(): pass
        class A:
            def __init__(self): pass
            def _h(self): pass
            def k(self): pass
        class _B:
            def k(self): pass
    """).functions}
    assert fs == {"f": True, "_g": False, "A.__init__": True, "A._h": False, "A.k": True, "_B.k": False}


def test_params_threshold_four_public_and_private():
    fs = by_name("def f(a, b, c, d, e): pass\ndef _g(a, b, c, d, e): pass\n")
    r = file_report(fm("def f(a, b, c, d, e): pass\ndef _g(a, b, c, d, e): pass\n"), 0, P1)
    assert fs["f"].params == 5 and [f["debt_parts"] for f in r["functions"]] == [{"params": 1.0}] * 2


# -- clones -------------------------------------------------------------------------------------

CLONE_SRC = '''
def a(xs, be):
    """Doc."""
    total = 0
    for x in xs:
        if x > be.limit:
            total += x * be.scale
    result = be.finish(total, mode="a")
    be.log("done", result)
    return result


def b(ys, self):
    acc = 1
    for y in ys:
        if y > self.be.lim:
            acc += y * self.be.k
    out = self.be.finish(acc, mode="b")
    self.be.log("over", out)
    return out


def c(zs):
    for z in zs:
        print(z)
    return zs
'''


def test_clone_groups_and_statement_counts():
    tree = ast.parse(CLONE_SRC)
    groups = pyclones.clone_groups(tree, 3, 30)
    assert ([[(o[0].lineno, o[-1].end_lineno) for o in g] for g in groups]
            == [[(4, 8), (14, 18)], [(5, 9), (15, 19)]])
    # assign, for, if, augassign (nested ones included), assign, expr: 6 statements per function
    assert pyclones.clone_statements(tree, 3, 30) == {"a": 6, "b": 6}
    fs = by_name(CLONE_SRC)
    assert (fs["a"].clones, fs["b"].clones, fs["c"].clones) == (6, 6, 0)


def test_clones_need_a_compound_statement_and_keyword_names_count():
    flat = "def f(p):\n" + "".join(f"    p.add_argument('--x{i}', type=int, default={i}, help='h')\n"
                                   for i in range(8))
    assert pyclones.clone_groups(ast.parse(flat), 3, 30) == []
    other = CLONE_SRC.replace('mode="b"', 'kind="b"')
    assert pyclones.clone_groups(ast.parse(other), 3, 30) == []


def test_clones_ignore_formatting():
    wide = CLONE_SRC.replace('result = be.finish(total, mode="a")',
                             'result = be.finish(\n        total,\n        mode="a",\n    )')
    assert pyclones.clone_statements(ast.parse(wide), 3, 30) == {"a": 6, "b": 6}


# -- G8: class-state ratchet ------------------------------------------------------------------

CFG = 'schema = 1\n[profiles]\npython = "python@1"\n[scope]\ninclude = ["pkg/*"]\n'
BASE = '''
class Run:
    def __init__(self, x):
        self.x = x

    def step(self):
        a = self.x * 2
        if a > 1:
            if a > 2:
                if a > 3:
                    if a > 4:
                        if a > 5:
                            a += 1
        b = a + 1
        return a + b
'''
# (a): helpers that communicate through two new self fields
EXTRACTED = '''
class Run:
    def __init__(self, x):
        self.x = x

    def step(self):
        self._phase1()
        return self._phase2()

    {allow}def _phase1(self):
        self.a = self.x * 2
        self.b = self.a + 1

    def _phase2(self):
        return self.a + self.b
'''
# (d): the same, state made local: a returned NamedTuple
LOCAL = '''
from typing import NamedTuple


class _Phase(NamedTuple):
    a: int
    b: int


class Run:
    def __init__(self, x):
        self.x = x

    def step(self):
        return self._phase2(self._phase1())

    def _phase1(self):
        a = self.x * 2
        return _Phase(a, a + 1)

    def _phase2(self, p):
        return p.a + p.b
'''
# (c): base with __init__ code; head moves it into a private helper called only from __init__
INIT_BASE = '''
class Run:
    def __init__(self, x):
        self.x = x
        self.y = x + 1
        self.z = x + 2

    def total(self):
        return self.x + self.y + self.z
'''
INIT_HEAD = '''
class Run:
    def __init__(self, x):
        self._setup(x)

    def _setup(self, x):
        self.x = x
        self.y = x + 1
        self.z = x + 2

    def total(self):
        return self.x + self.y + self.z
'''


def ratchet_repo(tmp_path, base, head):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": CFG, "pkg/__init__.py": "", "pkg/run.py": base})
    (tmp_path / "pkg/run.py").write_text(head)
    sha = gitio.rev(tmp_path, "HEAD")
    return sha, config.load(tmp_path, sha), gitio.changes(tmp_path, sha, None)


def check(tmp_path, base, head, mode="refactor"):
    sha, cfg, ch = ratchet_repo(tmp_path, base, head)
    return debt_check(tmp_path, sha, cfg, ch, mode=mode)


def test_g8a_new_channels_fail_although_debt_falls(tmp_path):
    c, db, dh = check(tmp_path, BASE, EXTRACTED.format(allow=""))
    assert dh < db and not c.ok
    assert "class state rose: Run 0 -> 2 (_phase1 writes a, b)" in c.detail


def test_g8b_allow_comment_does_not_lift_the_ratchet(tmp_path):
    head = EXTRACTED.format(allow="# codehealth: allow state_writes -- intermediate\n    ")
    c, db, dh = check(tmp_path, BASE, head)
    assert dh < db and not c.ok and "class state rose: Run 0 -> 2" in c.detail
    r = file_report(fm(head), 0, P1)
    assert next(f for f in r["functions"] if f["name"] == "Run._phase1")["debt_parts"] == {}


def test_g8c_init_code_moved_into_constructor_helper_passes(tmp_path):
    c, db, dh = check(tmp_path, INIT_BASE, INIT_HEAD)
    assert c.ok, c.detail
    assert file_report(fm(INIT_HEAD), 0, P1)["classes"][0]["state_writes"] == 0


def test_g8d_channel_made_local_passes_and_lowers_state(tmp_path):
    head = EXTRACTED.format(allow="")
    c, db, dh = check(tmp_path, head, LOCAL)
    assert c.ok, c.detail
    assert dh < db
    before = {x["name"]: x["state_writes"] for x in file_report(fm(head), 0, P1)["classes"]}
    after = {x["name"]: x["state_writes"] for x in file_report(fm(LOCAL), 0, P1)["classes"]}
    assert before["Run"] == 2 and after["Run"] == 0


def test_g8e_optimize_mode_gives_a_note(tmp_path):
    c, db, dh = check(tmp_path, BASE, EXTRACTED.format(allow=""), mode="optimize")
    assert c.ok
    assert "note: class state rose: Run 0 -> 2 (_phase1 writes a, b) (allowed in optimize mode)" in c.detail


@pytest.mark.parametrize("mode", ["restructure", "interface"])
def test_ratchet_is_an_error_in_restructure_and_interface_modes(tmp_path, mode):
    c, _, _ = check(tmp_path, BASE, EXTRACTED.format(allow=""), mode=mode)
    assert not c.ok and "class state rose" in c.detail


def test_unchanged_state_passes_and_a_new_class_rise_fails(tmp_path):
    base = EXTRACTED.format(allow="")
    moved = base.replace("class Run:", "class Other:\n    pass\n\n\nclass Run:")
    c, _, _ = check(tmp_path, base, moved)
    assert c.ok, c.detail
    small = base + extra(1)
    other = tmp_path / "g"
    other.mkdir()
    c2, _, _ = check(other, base, small)
    assert "class state rose" not in c2.detail   # seed allowance (its 0.5 state debt still counts)
    big = base + extra(9)
    third = tmp_path / "h"
    third.mkdir()
    c3, _, _ = check(third, base, big)
    assert not c3.ok and "class state rose over the touched files: 2 -> 11 (new: Extra 9)" in c3.detail


def extra(n):
    """A new class Extra with n channels (p writes q0..q{n-1}, r reads them)."""
    w = "\n        ".join(f"self.q{i} = {i}" for i in range(n))
    r = " + ".join(f"self.q{i}" for i in range(n))
    return f"\n\nclass Extra:\n    def p(self):\n        {w}\n\n    def r(self):\n        return {r}\n"


def test_seed_allowance_does_not_apply_when_a_base_class_disappears(tmp_path):
    base = EXTRACTED.format(allow="") + "\n\nclass Gone:\n    pass\n"
    head = EXTRACTED.format(allow="") + extra(1)
    c, _, _ = check(tmp_path, base, head)
    assert not c.ok and "class state rose over the touched files: 2 -> 3 (new: Extra 1)" in c.detail


CACHE_BASE = '''
class Store:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data[k]
'''
CACHE_HEAD = '''
class Store:
    def __init__(self):
        self.data = {}
        self.cache = None

    def get(self, k):
        if self.cache is None:
            self.cache = dict(self.data)
        return self.cache[k]

    def invalidate(self):
        self.cache = None
'''


def test_optimize_mode_does_not_charge_a_cache_field(tmp_path):
    c, db, dh = check(tmp_path, CACHE_BASE, CACHE_HEAD, mode="optimize")
    assert c.ok, c.detail
    other = tmp_path / "r"
    other.mkdir()
    c2, _, _ = check(other, CACHE_BASE, CACHE_HEAD, mode="refactor")
    assert not c2.ok


def test_nested_class_writes_are_not_the_methods_writes():
    src = '''
class A:
    def f(self):
        class I:
            def m(self):
                self.z = 1
        return I

    def g(self):
        return self.z
'''
    r = file_report(fm(src), 0, P1)
    assert next(c for c in r["classes"] if c["name"] == "A")["state_writes"] == 0


def test_diff_has_the_ratchet(tmp_path, capsys):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": CFG, "pkg/__init__.py": "", "pkg/run.py": BASE})
    git(tmp_path, "checkout", "-q", "-b", "work")
    commit(tmp_path, {"pkg/run.py": EXTRACTED.format(allow="")})
    out = tmp_path / "d.json"
    assert main(["diff", "--repo", str(tmp_path), "--base", "main", "--json", str(out)]) == 1
    assert "FAIL class state rose: Run 0 -> 2 (_phase1 writes a, b)" in capsys.readouterr().out
    head = json.loads(out.read_text())["head"]["pkg/run.py"]
    assert head["classes"][0]["channels"] == {"a": ["_phase1"], "b": ["_phase1"]}
