from cce_code_health import docstrings as ds

MOD = '''
def f(a, b):
    """Do f.

    Parameters
    ----------
    a : int
        First.
    b
        Second.

    Returns
    -------
    int
    """


def g(x, *args, key=1, **kw):
    """Google style.

    Args:
        x (int): the x.
        *args: more.
        key: a key.
        **kw: rest.
    """


def h(p, q):
    """Sphinx. :param p: the p. :param int r: wrong name."""


def nodoc():
    pass


def _private():
    pass


class C:
    """A class."""

    def method(self, y):
        """Method.

        Parameters
        ----------
        y, z : float
        """

    @property
    def prop(self):
        pass

    @prop.setter
    def prop(self, v):
        pass

    def _hidden(self):
        pass

    def __repr__(self):
        return ""
'''


def by_name(text, path="pkg/m.py"):
    return {it.qualname: it for it in ds.items(path, text)}


def test_items_presence_and_drift():
    it = by_name(MOD)
    assert set(it) == {"f", "g", "h", "nodoc", "C", "C.method", "C.prop"}
    assert it["f"].documented and it["f"].drift is None          # Returns is not a parameter
    assert it["g"].drift is None
    assert it["h"].drift == "missing q; extra r"
    assert it["C.method"].drift == "extra z"
    assert not it["nodoc"].documented and not it["C.prop"].documented


def test_private_modules_and_all():
    assert ds.items("pkg/_impl/m.py", MOD) == [] and ds.items("pkg/_m.py", MOD) == []
    assert ds.items("pkg/__init__.py", "def f():\n    pass\n")[0].qualname == "f"
    assert [i.qualname for i in ds.items("pkg/m.py", "__all__ = ['b']\ndef a(): pass\ndef b(): pass\n")] == ["b"]


def test_class_d2_only_with_explicit_init():
    dc = '''
from dataclasses import dataclass

@dataclass
class D:
    """D.

    Parameters
    ----------
    a
        A.
    gone
        No longer a field.
    """
    a: int
'''
    assert by_name(dc)["D"].drift is None                    # dataclass: not checked
    cls = '''
class E:
    """E.

    Parameters
    ----------
    a
        A.
    """

    def __init__(self, a, b, **kw):
        pass
'''
    assert by_name(cls)["E"].drift == "missing b"            # **kw: optional, extras allowed


def test_overrides_nested_type_checking_duplicates_tests():
    src = '''
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    def hidden():
        pass

try:
    def dup():
        """Documented here."""
except ImportError:
    def dup():
        pass


class Base:
    """B."""

    def run(self):
        """Run."""


class Child(Base):
    """C."""

    def run(self):
        pass

    @override
    def other(self):
        pass

    class Config:
        pass
'''
    it = by_name(src)
    assert set(it) == {"dup", "Base", "Base.run", "Child"}
    assert it["dup"].documented
    assert ds.items("tests/test_a.py", "def f():\n    pass\n") == []
    assert ds.items("pkg/test_a.py", "def f():\n    pass\n") == []
    assert ds.items("conftest.py", "def f():\n    pass\n") == []


def test_ratchet_new_vs_grandfathered():
    base = {"pkg/m.py": "def old():\n    pass\n"}
    head = {"pkg/m.py": "def old():\n    pass\n\ndef new():\n    pass\n"}
    fails = ds.ratchet(base, head, {})
    assert len(fails) == 1 and "new: public name without docstring (D1)" in fails[0]
    moved = ds.ratchet(base, {"pkg/n.py": "def old():\n    pass\n"}, {})       # moved: grandfathered
    assert moved == []
    drift_base = {"pkg/m.py": 'def f(a):\n    """F.\n\n    Args:\n        a: x.\n    """\n'}
    drift_head = {"pkg/m.py": 'def f(a, b):\n    """F.\n\n    Args:\n        a: x.\n    """\n'}
    fails = ds.ratchet(drift_base, drift_head, {})
    assert len(fails) == 1 and "missing b (D2)" in fails[0]
    assert ds.ratchet(drift_head, drift_head, {}) == []                      # old drift passes


def test_grandfathering_across_files_only_for_names_that_left():
    base = {"pkg/a.py": "def main():\n    pass\n", "pkg/b.py": "X = 1\n"}
    head = {"pkg/a.py": "def main():\n    pass\n", "pkg/b.py": "X = 1\n\ndef main():\n    pass\n"}
    fails = ds.ratchet(base, head, {})
    assert len(fails) == 1 and fails[0].startswith("pkg/b.py:3 main")    # a.py still has its main
