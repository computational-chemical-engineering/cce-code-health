"""cpp@1 rules (docs/CPP1_LAYERS.md). Falsifiers F1-F6 come from docs/cpp1/f1.cpp."""
import pytest

pytest.importorskip("lizard")

from cce_code_health.cpp_backend import analyze, nesting_depth, tokens  # noqa: E402
from cce_code_health.cpp_view import select_branches, unmark_constexpr  # noqa: E402
from cce_code_health.profiles import get  # noqa: E402

CPP0, CPP1 = get("cpp@0"), get("cpp@1")

F1 = """\
template <class T> int f(T x) {
  if constexpr (A<T>) { return 1; } else if constexpr (B<T>) { return 2; } else { return x ? 3 : 4; }
}
"""
F2 = """\
int g(int x) {
#if defined(A)
  if (x) { x++; }
#elif 0
  while (x) x--; while (x) x--;
#else
  if (x > 1 && x < 9) { x--; }
#endif
  return x;
}
"""
F3 = """\
void h(int c) {
#ifdef M
  if (false) {
#else
  if (c > 1) {
#endif
    c++;
  }
}
int after1(int a) { if (a) return 1; return 0; }
int after2(int a) { return a; }
"""


def fns(src, profile):
    return {f.name: f for f in analyze("f.cpp", src, profile).functions}


def test_f1_if_constexpr_chain():
    f0, f1 = fns(F1, CPP0)["f"], fns(F1, CPP1)["f"]
    assert (f0.ccn, f0.nesting) == (4, 2)
    assert (f1.ccn, f1.nesting) == (2, 0)


def test_f2_one_branch_with_most_decisions():
    g0, g1 = fns(F2, CPP0)["g"], fns(F2, CPP1)["g"]
    assert (g0.ccn, g0.nloc) == (8, 6)
    assert (g1.ccn, g1.nloc) == (3, 4)


def test_f3_unbalanced_braces_keep_later_functions():
    f0, f1 = fns(F3, CPP0), fns(F3, CPP1)
    assert "after1" not in f0 and "after2" not in f0
    assert f1["h"].ccn == 2
    assert (f1["after1"].ccn, f1["after2"].ccn) == (2, 1)


def test_view_keeps_lines_and_offsets():
    v = select_branches(F2)
    assert len(v) == len(F2) and v.count("\n") == F2.count("\n")
    assert "#" not in v and "while" not in v and "if (x) { x++; }" not in v
    assert "if (x > 1 && x < 9)" in v


@pytest.mark.parametrize("src, kept", [
    ("#if 0\nA\n#else\nB\n#endif\n", "B"),
    ("#if false\nA\n#endif\n", ""),
    ("#if 0 // off\nA\n#else\nB\n#endif\n", "B"),
    ("#if 0 || defined(X)\nif (a) x;\n#else\nB\n#endif\n", "if (a) x;"),  # not a literal 0
    ("#if (0)\nif (a) x;\n#else\nB\n#endif\n", "B"),
    ("#if 1\nA\n#else\nif (a) if (b) x;\n#endif\n", "A"),
    ("#ifdef X\nif (a) x;\n#elif 1\nif (a && b || c) x;\n#else\nwhile (a) while (b) x;\n#endif\n",
     "if (a && b || c) x;"),
    ("#ifndef X\nif (a) x;\n#endif\n", "if (a) x;"),                      # vs implicit empty else
    ("#ifdef X\nA\n#else\nB\n#endif\n", "A"),                             # tie: first branch
    ("#ifdef X\nint f() {\n#else\nint f() { if (a) {\n#endif\n", "int f() {"),  # brace guard
    ("#ifdef X\n#if 0\nif (a) if (b) x;\n#else\nif (c) x;\n#endif\n#else\nfor (;;) {}\n#endif\n",
     "if (c) x;"),                                                         # nested groups
    ("#ifdef X\nif (a) x;\n", "if (a) x;"),                               # unterminated group
    ("#endif\n#else\nA\n", "#endif\n#else\nA"),                           # stray directives stay
    ('#ifdef X\nconst char* s = "{";\n#else\nint y;\n#endif\n', 'const char* s = "{";'),
])
def test_branch_selection(src, kept):
    assert " ".join(select_branches(src).split()) == " ".join(kept.split())


def test_and_or_counted_as_pairs():
    # `& &` (two tokens apart) is not a decision; `&&` is.
    src = "#ifdef X\nif (a) x;\n#else\nf(a & &b); f(c & d);\n#endif\n"
    assert select_branches(src).split() == ["if", "(a)", "x;"]
    src = "#ifdef X\nif (a) x;\n#else\nf(a && b || c);\n#endif\n"
    assert select_branches(src).split() == ["f(a", "&&", "b", "||", "c);"]


def test_unmark_constexpr_blanks_only_the_if():
    src = 'if constexpr (A) {} // if constexpr\nauto s = "if constexpr"; constexpr int k = 1;\n'
    out = unmark_constexpr(src)
    assert len(out) == len(src)
    assert out.startswith("   constexpr (A) {}") and out.count("if constexpr") == 2


def test_nesting_of_if_constexpr():
    def nd(code):
        return nesting_depth([t for _, t in tokens(code)[0]], flat_constexpr=True)
    assert nd("if constexpr (A) { if (b) {} } else if constexpr (B) { } else { if (c) {} }") == 1
    assert nd("if constexpr (A) { } else if (b) { if (c) {} }") == 2   # runtime else-if nests
    assert nd("if constexpr (A) return 1; else for (;;) {}") == 1
    assert nd("for (;;) { if constexpr (A) { while (x) {} } else { do { y; } while (z); } }") == 2
    assert nd("if (a) {} else { if (b) {} }") == 2                     # plain else unchanged


def test_cpp0_unchanged_by_flag_default():
    src = "if constexpr (A) { } else if constexpr (B) { } else { }"
    assert nesting_depth([t for _, t in tokens(src)[0]]) == 2


def test_allows_and_lines_from_raw_source():
    src = ("// codehealth: allow ccn -- dispatch\n"
           "int f(int a) {\n#ifdef X\n  if (a) return 1;\n#endif\n  return 0;\n}\n")
    f = fns(src, CPP1)["f"]
    assert (f.line, f.end, f.ccn) == (2, 7, 2)
    assert [(a.line, tuple(a.smells)) for a in f.allows] == [(1, ("ccn",))]


def test_runtime_chain_with_else_if_constexpr():
    def nd(code):
        return nesting_depth([t for _, t in tokens(code)[0]], flat_constexpr=True)
    assert nd("if (a) { } else if constexpr (B) { } else { if (c) { } }") == 2
    assert nd("if (a) { } else if constexpr (B) { } else if constexpr (C) { } else { if (c) {} }") == 2


@pytest.mark.parametrize("src, kept", [
    ("#if 0 /* off */ || defined(X)\nif (a) x;\n#else\nB\n#endif\n", "if (a) x;"),
    ("#if 1 /* on */ && defined(X)\nA\n#else\nif (a) x;\n#endif\n", "if (a) x;"),
    ("#if 0 /* off */\nA\n#else\nB\n#endif\n", "B"),
    ("#ifdef X\nA\n#elifdef Y\nif (a) x;\n#endif\n", "if (a) x;"),
    ("#ifdef X\nif constexpr (A) x;\n#else\nif (a) x;\n#endif\n", "if (a) x;"),  # constexpr no decision
])
def test_branch_selection_review_cases(src, kept):
    assert " ".join(select_branches(src).split()) == " ".join(kept.split())


def test_cpp1_does_not_share_cpp0_tables():
    assert CPP1.thresholds == CPP0.thresholds and CPP1.thresholds is not CPP0.thresholds
    assert CPP1.weights == CPP0.weights and CPP1.weights is not CPP0.weights
    assert CPP0.cpp_rules == frozenset()


def test_cpp1_through_config_and_scan(tmp_path):
    import json
    from test_git import commit, git
    from cce_code_health.cli import main
    git(tmp_path, "init", "-q", "-b", "main")
    cfg = 'schema = 1\nprofile = "python@0"\n[profiles]\ncpp = "cpp@1"\n[scope]\ninclude = ["*.cpp"]\n'
    commit(tmp_path, {"g.cpp": F2, ".codehealth/config.toml": cfg}, "base")
    out = tmp_path / "scan.json"
    assert main(["scan", "--repo", str(tmp_path), "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["meta"]["profiles"] == {"cpp": "cpp@1"}
    (g,) = data["files"]["g.cpp"]["functions"]
    assert (g["ccn"], g["nloc"]) == (3, 4)


F4 = """\
void k() { for (int i=0;i<2;++i) for (int j=0;j<2;++j) for (int l=0;l<2;++l) if (i) c++; }
void q(int a) { if (a) if (a>1) if (a>2) if (a>3) a++; }
"""


def test_f4_loop_credit():
    from cce_code_health.report import function_debt
    f0, f1 = fns(F4, CPP0), fns(F4, CPP1)
    assert (f0["k"].nesting, f1["k"].nesting) == (4, 3)
    assert (f0["q"].nesting, f1["q"].nesting) == (4, 4)
    assert (function_debt(f0["k"], CPP0)[0], function_debt(f1["k"], CPP1)[0]) == (3.0, 0.0)
    assert function_debt(f1["q"], CPP1)[0] == 3.0


@pytest.mark.parametrize("code, depth", [
    ("for (;;) { if (a) { for (;;) { if (b) {} } } }", 3),      # two loops on the path
    ("for (;;) { if (a) { if (b) { if (c) {} } } }", 4),        # one loop: no credit
    ("for (;;) {} if (a) { for (;;) { if (b) {} } }", 3),       # loops on different paths
    ("do { while (a) { if (b) {} } } while (c);", 2),           # do and while are loops
    ("while (a) { for (;;) {} } if (a) { if (b) { if (c) {} } }", 3),
])
def test_loop_credit_paths(code, depth):
    assert nesting_depth([t for _, t in tokens(code)[0]], loop_credit=True) == depth
