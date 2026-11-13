import textwrap

from cce_code_health.profiles import get
from cce_code_health.python_backend import analyze
from cce_code_health.report import file_report

P = get("python@0")


def fm(src):
    return analyze("x.py", textwrap.dedent(src))


def by_name(src):
    return {f.name: f for f in fm(src).functions}


def test_elif_does_not_nest_and_with_does_not_count():
    f = by_name("""
        def f(x):
            with open(x) as h:
                if x == 1:
                    return 1
                elif x == 2:
                    return 2
                elif x == 3:
                    if h:
                        return 3
            return 0
    """)["f"]
    assert f.nesting == 2


def test_params_self_and_varargs():
    fs = by_name("""
        class A:
            def m(self, a, b, *args, **kw):
                pass
        def g(a, b, /, c, *, d):
            pass
    """)
    assert fs["A.m"].params == 2 + 3 + 3
    assert fs["g"].params == 4


def test_nloc_excludes_docstring_comments_blank_and_nested_belongs_to_unit():
    fs = by_name('''
        def f():
            """Doc
            string."""
            # comment

            x = 1
            def inner():
                return 2
            return x
    ''')
    assert list(fs) == ["f"]
    assert fs["f"].nloc == 5          # def, x=1, def inner, return 2, return x


def test_boolops_and_bumps():
    f = by_name("""
        def f(a, b, c, d):
            if a and b or not (c and d):
                pass
            for i in a:
                if i:
                    pass
            while b:
                if c:
                    pass
            if d:
                for j in d:
                    pass
    """)["f"]
    assert f.boolops == 3
    assert f.bumps == 3


def test_cognitive_matches_decorated_function():
    f = by_name("""
        import functools
        @functools.cache
        def f(x):
            if x:
                if x > 1:
                    return 1
            return 0
    """)["f"]
    assert f.cognitive == 3


def test_debt_uncapped_and_suppression():
    many = "def f(" + ", ".join(f"a{i}" for i in range(9)) + "):\n    pass\n"
    r = file_report(analyze("x.py", many), 0, P)
    assert r["debt"] == 4.0                       # params 9 - 5
    sup = "# codehealth: allow params -- legacy API\n" + many
    r = file_report(analyze("x.py", sup), 0, P)
    assert r["debt"] == 0.0 and r["suppressions"][0]["valid"]
    bad = "# codehealth: allow params\n" + many
    r = file_report(analyze("x.py", bad), 0, P)
    assert r["debt"] == 4.0 and not r["suppressions"][0]["valid"]


def test_scan_with_trial_config_reads_nothing_from_repo(tmp_path, capsys):
    import json
    import subprocess
    from cce_code_health.cli import main
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("def f(x):\n    return x\n")
    (repo / "skip.py").write_text("def g(x):\n    return x\n")
    cfg = tmp_path / "trial.toml"
    cfg.write_text('schema = 1\n[scope]\ninclude = ["a.py"]\n')
    out = tmp_path / "s.json"
    assert main(["scan", "--repo", str(repo), "--config", str(cfg), "--json", str(out)]) == 0
    files = json.loads(out.read_text())["files"]
    assert sorted(files) == ["a.py"]
    assert not (repo / ".codehealth").exists()
