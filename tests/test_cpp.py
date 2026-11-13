import json
import textwrap

import pytest

pytest.importorskip("lizard")

from cce_code_health import config, gitio, gate as gatemod  # noqa: E402
from cce_code_health.cli import main  # noqa: E402
from cce_code_health.cpp_backend import analyze, nesting_depth, tokens  # noqa: E402
from cce_code_health.profiles import get  # noqa: E402
from cce_code_health.report import file_report, function_debt, is_brain  # noqa: E402

from test_git import commit, git  # noqa: E402

P = get("cpp@0")

GRID = """\
// header comment
namespace geo {
namespace detail {

class Grid {
 public:
  // codehealth: allow nesting -- deliberate
  int walk(int a, int b, int c) const {
    int s = 0;
    if (a > 0) {
      for (int i = 0; i < b; ++i) {
        while (c > 0 && a > 0) {
          if (i % 2 == 0) {
            s += i;
          } else if (i % 3 == 0) {
            s -= i;
          }
          --c;
        }
      }
    }
    return s;
  }
};

}  // namespace detail
}  // namespace geo

// codehealth: allow params -- mirrors the external API
template <typename T>
T pick(T a, T b, T c, T d, T e, T f, int k) {
  auto twice = [](T x) { if (x) { return x + x; } return x; };
  switch (k) {
    case 0: return a;
    case 1: return b;
    default: break;
  }
  return twice(f);
}

int geo::Grid2::deep(int x, const char* s = "if (x) {") {
  if (x) for (;;) while (x) do { if (x) return 1; } while (x);
  return 0;
}
"""


def by_name(src, path="g.cpp"):
    return {f.name: f for f in analyze(path, src).functions}


def test_known_metrics_and_qualified_names():
    fs = by_name(GRID)
    assert list(fs) == ["geo::detail::Grid::walk", "pick", "geo::Grid2::deep"]
    w, p, d = fs.values()
    # walk: 1 + if + for + while + && + if + else-if = 7; if > for > while > if = 4
    assert (w.ccn, w.nesting, w.params, w.nloc, w.line, w.end) == (7, 4, 3, 16, 8, 23)
    # pick: 1 + 2 case + lambda's if = 4 (lambda belongs to pick); switch, lambda if: 1
    assert (p.ccn, p.nesting, p.params, p.nloc) == (4, 1, 7, 9)
    # deep: 1 + if + for + while + if + do's while = 6 (lizard does not count do);
    # braceless if > for > while > do > if = 5; the string literal is not code
    assert (d.ccn, d.nesting, d.params, d.nloc) == (6, 5, 2, 4)


def test_suppressions_and_profile_debt():
    fm = analyze("g.cpp", GRID)
    w, p, d = fm.functions
    assert [a.smells for a in w.allows] == [("nesting",)]
    assert [a.smells for a in p.allows] == [("params",)]   # above the template line
    assert function_debt(w, P) == (0, {})                    # nesting 4 suppressed
    assert function_debt(p, P) == (0, {})                    # params 7 suppressed
    assert function_debt(d, P) == (6.0, {"nesting": 6.0})    # 3 * (5 - 3)
    r = file_report(fm, 0, P)
    assert r["debt"] == 6.0 and r["file_smells"] == []
    assert set(r["functions"][0]) >= {"ccn", "nesting", "params", "nloc"}
    assert "cognitive" not in r["functions"][0]


def test_invalid_and_file_level_suppressions():
    src = ("// codehealth: allow-file large_file -- generated\n"
           "// codehealth: allow cognitive -- python name\n"
           "void f() {}\n")
    fm = analyze("x.hpp", src)
    assert [(a.file_level, a.valid) for a in fm.allows] == [(True, True), (False, False)]


def test_brain_and_ccn_debt():
    body = "".join(f"  if (x > {i}) {{\n    y += {i};\n  }}\n" for i in range(30))
    fm = analyze("b.cc", f"int big(int x) {{\n  int y = 0;\n{body}  return y;\n}}\n")
    (f,) = fm.functions
    assert (f.ccn, f.nloc, f.nesting) == (31, 94, 1)
    assert is_brain(f, P)
    assert function_debt(f, P) == (23.4, {"ccn": 21.0, "nloc": 2.4})


def test_templates_lambdas_odd_input_do_not_crash():
    src = textwrap.dedent("""\
        #define LOOP(x) for (;;) { if (x) break; }
        template <class... Ts> auto f(Ts&&... ts) -> decltype(auto) {
          auto g = [&](auto y) mutable noexcept { return [y]() { return y; }; };
          constexpr auto r = R"raw(if (x) { while )raw";
          if constexpr (sizeof...(Ts) > 1'000) { return g(1)(); }
          return 0;
        }
        struct S { S() = default; operator bool() const { return true; } };
        int broken( {
        """)
    fm = analyze("t.tpp", src)
    assert fm.error is None
    names = [f.name for f in fm.functions]
    assert "f" in names and "S::operator bool" in names


def test_nesting_rules():
    def nd(code):
        return nesting_depth([t for _, t in tokens(code)[0]])
    assert nd("if (a) { } else if (b) { } else { if (c) {} }") == 2
    assert nd("try { if (a) {} } catch (E& e) { for (;;) {} }") == 2
    assert nd("do { x; } while (a); if (b) {}") == 1
    assert nd("[]{ if (a) { return; } }();") == 1
    assert nd("// if (a) { if (b) { if (c) {\n x;") == 0


def test_comment_blanking_keeps_lines():
    src = "a; /* x\n y */ b; // c\n\"// s\";\n"
    out = tokens(src)
    assert out[1] == "a;     \n      b;     \n\"// s\";\n"
    assert [ln for ln, _ in out[0]] == [1, 1, 2, 2, 3, 3]


CFG = 'schema = 1\n[scope]\ninclude = ["src/*"]\n'


@pytest.fixture
def mixed(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {"src/a.py": "def f(x):\n    return x\n", "src/g.cpp": GRID,
                      ".codehealth/config.toml": CFG}, "base")
    return tmp_path


def scan(repo, tmp_path):
    out = tmp_path / "scan.json"
    assert main(["scan", "--repo", str(repo), "--json", str(out)]) == 0
    return json.loads(out.read_text())


def test_python_only_config_ignores_cpp(mixed, tmp_path):
    data = scan(mixed, tmp_path / "o")
    assert list(data["files"]) == ["src/a.py"]
    assert "profiles" not in data["meta"]


def test_cpp_measured_only_when_configured(mixed, tmp_path):
    commit(mixed, {".codehealth/config.toml": CFG + '[profiles]\ncpp = "cpp@0"\n'})
    data = scan(mixed, tmp_path / "o")
    assert sorted(data["files"]) == ["src/a.py", "src/g.cpp"]
    assert data["meta"]["profiles"] == {"cpp": "cpp@0"}
    assert data["files"]["src/g.cpp"]["debt"] == 6.0
    assert data["files"]["src/a.py"]["functions"][0]["cognitive"] == 0


def test_diff_and_gate_static_rules_with_cpp(mixed, capsys):
    cfg_text = CFG + '[profiles]\ncpp = "cpp@0"\n[gate]\nliterals = true\npublic_api = true\n'
    commit(mixed, {".codehealth/config.toml": cfg_text})
    git(mixed, "checkout", "-qb", "work")
    commit(mixed, {"src/g.cpp": GRID.replace("if (x) return 1;", "if (x) { if (x) return 1; }"),
                   "src/new.hpp": "inline int h() { return 1; }\n"})
    assert main(["diff", "--repo", str(mixed), "--base", "main", "--head", "HEAD"]) == 1
    assert "debt 6.00 -> 9.00" in capsys.readouterr().out
    git(mixed, "checkout", "-q", "main")
    git(mixed, "checkout", "-q", "work", "--", "src")
    cfg = config.parse(cfg_text, "t")
    base = gitio.rev(mixed, "main")
    checks = gatemod.static_checks(mixed, base, cfg, [("M", "src/g.cpp", "src/g.cpp"),
                                                      ("A", None, "src/new.hpp")])
    assert {c.name: c.ok for c in checks} == {"frozen": True, "forbid": True, "scope": True,
                                              "literals": True, "public_api": True,
                                              "flags": True, "numeric-literals": True}


def test_config_profiles_table():
    cfg = config.parse('schema = 1\nprofile = "python@0"\n[profiles]\ncpp = "cpp@0"\n', "t")
    assert cfg.profile.name == "python@0" and cfg.other_profiles["cpp"].name == "cpp@0"
    assert cfg.in_scope("a/b.hpp") and cfg.in_scope("a/b.py") and not cfg.in_scope("a/b.c")
    assert not config.parse("schema = 1\n", "t").in_scope("a/b.cpp")
    info, bad = config.check('schema = 1\n[profiles]\ncpp = "cpp@0"\n')
    assert bad == [] and "profile cpp@0 (cpp)" in info
    for text in ('schema = 1\n[profiles]\ncpp = "python@0"\n',
                 'schema = 1\n[profiles]\nrust = "cpp@0"\n',
                 'schema = 1\nprofile = "python@0"\n[profiles]\npython = "cpp@0"\n'):
        assert config.check(text)[1], text
        with pytest.raises(ValueError):
            config.parse(text, "t")


def test_python_report_keys_unchanged():
    from cce_code_health.python_backend import analyze as analyze_py
    r = file_report(analyze_py("a.py", "def f(a, b):\n    return a\n"), 0, get("python@0"))
    assert list(r["functions"][0]) == ["name", "qualname", "line", "end_line", "nloc", "cognitive",
                                       "nesting", "bumps", "params", "boolops", "debt",
                                       "debt_parts", "brain"]
