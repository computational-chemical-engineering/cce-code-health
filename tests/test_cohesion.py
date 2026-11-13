import ast
import textwrap

from cce_code_health import profiles
from cce_code_health.cohesion import classes
from cce_code_health.python_backend import analyze
from cce_code_health.report import file_report


def lcom(src):
    return {c["name"]: c for c in classes(ast.parse(textwrap.dedent(src)))}


def test_two_disjoint_attribute_groups():
    c = lcom("""
        class A:
            def a1(self): return self.x
            def a2(self): self.x = 1
            def b1(self): return self.y
            def b2(self): self.y = 2
    """)["A"]
    assert (c["methods"], c["lcom4"]) == (4, 2)


def test_self_call_connects():
    c = lcom("""
        class A:
            def a(self): return self.x
            def b(self): return self.y
            def c(self): return self.a() + self.b()
    """)["A"]
    assert c["lcom4"] == 1


def test_init_excluded_and_few_methods_none():
    c = lcom("""
        class A:
            def __init__(self): self.x = self.y = 1
            def a(self): return self.x
            def b(self): return self.y
    """)["A"]
    assert (c["methods"], c["lcom4"]) == (2, None)
    d = lcom("""
        class B:
            def __init__(self): self.x = 1
            def a(self): return self.x
            def b(self): return self.y
            def c(self): return 1
    """)["B"]
    assert (d["methods"], d["lcom4"]) == (3, 3)


def test_nested_class_classmethod_and_property():
    cl = lcom("""
        class Outer:
            class Inner:
                def a(self): return self.x
                def b(self): return self.x
                def c(self): return self.x
            @classmethod
            def f(cls): return cls.k
            @classmethod
            def g(cls): return cls.k
            @property
            def p(self): return 1
            def h(self): return self.p
    """)
    assert cl["Outer.Inner"]["lcom4"] == 1
    assert cl["Outer"]["lcom4"] == 2          # {f, g} and {p, h}


def test_old_fields_unchanged():
    src = textwrap.dedent("""
        class A:
            def a(self, x):
                if x:
                    return self.v
                return 0
            def b(self): return self.w
            def c(self): return 1
        def top(x): return x
    """)
    p = profiles.get("python@0")
    fm = analyze("m.py", src)
    r = file_report(fm, 0, p)
    fm.classes = []
    r0 = file_report(fm, 0, p)
    assert r["max_lcom4"] == 3 and r0["max_lcom4"] is None
    for k in r0:
        if k not in ("classes", "max_lcom4"):
            assert r[k] == r0[k]
