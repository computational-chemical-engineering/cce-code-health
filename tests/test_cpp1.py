"""cpp@1 rules (docs/CPP1_LAYERS.md). Falsifiers F1-F6 come from docs/cpp1/f1.cpp."""
from dataclasses import replace

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
    assert CPP1.thresholds == dict(CPP0.thresholds, captures=12) and "captures" not in CPP0.thresholds
    assert CPP1.weights == dict(CPP0.weights, captures=0.5) and "captures" not in CPP0.weights
    assert CPP0.metrics == ("ccn", "nloc", "nesting", "params")
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


# Rule (3): kernel lambdas are functions of their own.
F5 = """\
void s(View a, View b, int n) {
  const double dt = 0.1;
  Kokkos::parallel_for("k", n, [a, &b, c = 2 * n](const int i) { a(i) = b(i) + c; });
  Kokkos::parallel_for("k2", n, KOKKOS_LAMBDA(const int i) { if (i) a(i) = dt * b(i) + n; });
}
"""
NO_SPLIT = replace(CPP1, cpp_rules=CPP1.cpp_rules - {"kernels"})


def ccn_sum(src, profile):
    return sum(f.ccn - 1 for f in analyze("f.cpp", src, profile).functions)


def test_f5_kernel_lambdas_split():
    f = fns(F5, CPP1)
    assert sorted(f) == ["s", "s::k", "s::k2"]
    assert (f["s"].ccn, f["s::k"].ccn, f["s::k2"].ccn) == (1, 1, 2)
    assert (f["s::k2"].nesting, f["s::k2"].params, f["s::k2"].line, f["s::k2"].end) == (1, 1, 4, 4)
    assert fns(F5, CPP0)["s"].ccn == 2
    assert ccn_sum(F5, CPP1) == ccn_sum(F5, NO_SPLIT) == 1


MULTI = """\
namespace ns {
template <class P>
void run(P p, int n) {
  if (n > 2) n--;
  Kokkos::parallel_for(
      "outer", Kokkos::TeamPolicy<>(n, 4),
      KOKKOS_LAMBDA(const member_t& m) {
        for (int j = 0; j < 4; ++j) {
          if (j && n) p(j);
        }
        Kokkos::parallel_for(Kokkos::TeamThreadRange(m, n), [&](int i) {
          while (i > 0) { if (i % 2) --i; --i; }
        });
      });
  Kokkos::parallel_reduce(n, KOKKOS_CLASS_LAMBDA(int i, double& s) mutable {
    s += i > 1 ? 1.0 : 0.0;
  }, total);
  std::sort(v.begin(), v.end(), [](int x, int y) { return x < y || x == 0; });
  auto f = [&](int i) { if (i) return 1; return 0; };
  Kokkos::parallel_for(n, KOKKOS_LAMBDA(int i) { p(i); });
  Kokkos::parallel_for("outer", n, KOKKOS_LAMBDA(int i) { p(i); });
}
}
"""


def test_nested_and_unlabelled_kernels():
    f = fns(MULTI, CPP1)
    assert sorted(f) == ["ns::run", "ns::run::lambda#1", "ns::run::lambda#2", "ns::run::outer",
                         "ns::run::outer#2", "ns::run::outer::lambda#1"]
    outer, inner = f["ns::run::outer"], f["ns::run::outer::lambda#1"]
    assert (outer.ccn, outer.nesting, outer.line, outer.end) == (4, 2, 7, 14)
    assert (inner.ccn, inner.nesting, inner.params, inner.line) == (3, 2, 1, 11)
    red = f["ns::run::lambda#1"]                       # parallel_reduce, `mutable` skipped
    assert (red.ccn, red.params, red.line, red.end) == (2, 2, 15, 17)
    # the parent keeps its own if, the std::sort comparator (||) and the named lambda (if)
    assert f["ns::run"].ccn == 1 + 1 + 1 + 1
    assert ccn_sum(MULTI, CPP1) == ccn_sum(MULTI, NO_SPLIT)


def test_parent_nloc_drops_kernel_lines():
    f1, f0 = fns(MULTI, CPP1), fns(MULTI, NO_SPLIT)
    assert (f0["ns::run"].nloc, f1["ns::run"].nloc) == (20, 7)   # keeps lines 3-6, 18, 19, 22
    assert (f0["ns::run"].nesting, f1["ns::run"].nesting) == (2, 1)


def test_kernel_suppression_from_line_above_launch():
    src = ("void s(int n) {\n"
           "  // codehealth: allow ccn -- kernel dispatch\n"
           "  Kokkos::parallel_for(\"k\",\n"
           "      n, KOKKOS_LAMBDA(int i) { if (i) g(i); });\n"
           "}\n")
    f = fns(src, CPP1)
    assert [a.line for a in f["s::k"].allows] == [2]
    assert f["s"].allows == []


def test_kernel_outside_a_function_and_cpp0_unsplit():
    src = "auto g = KOKKOS_LAMBDA(int i) { if (i) h(i); };\nvoid s() { parallel_for(1, [=](int i) { if (i) h(i); }); }\n"
    # lizard reads the file-scope lambda as a function named KOKKOS_LAMBDA; it is not split
    assert sorted(fns(src, CPP1)) == ["KOKKOS_LAMBDA", "s", "s::lambda#1"]
    assert sorted(fns(src, CPP0)) == ["KOKKOS_LAMBDA", "s"]


def test_file_nloc_unchanged_by_split():
    assert analyze("f.cpp", MULTI, CPP1).nloc == analyze("f.cpp", MULTI, NO_SPLIT).nloc


def test_kernel_zone_from_its_introducer():
    from cce_code_health.zones import annotate
    rules = [{"name": "device", "markers": ["KOKKOS_LAMBDA"]}, {"name": "host"}]
    fm = analyze("f.cpp", F5, CPP1)
    funcs = [{"name": f.name, "line": f.line, "end_line": f.end} for f in fm.functions]
    reports = {"f.cpp": {"functions": funcs}}
    annotate(reports, rules, {"f.cpp": F5})
    assert {f["name"]: f["zone"] for f in funcs} == {"s": "host", "s::k": "host", "s::k2": "device"}


def test_kernel_zone_survives_braces_before_the_macro_and_nesting():
    from cce_code_health.zones import annotate
    src = ("void s(int n) {\n"
           "  parallel_for(\"md\", MD({0, 0}, {n, n}), KOKKOS_LAMBDA(int i, int j) {\n"
           "    parallel_for(R(i), [&](int k) { if (k) g(k); });\n"
           "  });\n"
           "  parallel_for(n, [=](int i) { g(i); });\n"
           "}\n")
    fm = analyze("f.cpp", src, CPP1)
    assert {f.name: f.introducer for f in fm.functions} == {
        "s": None, "s::md": "KOKKOS_LAMBDA", "s::md::lambda#1": "KOKKOS_LAMBDA", "s::lambda#1": None}
    funcs = [{"name": f.name, "line": f.line, "end_line": f.end,
              **({"introducer": f.introducer} if f.introducer else {})} for f in fm.functions]
    annotate({"f.cpp": {"functions": funcs}}, [{"name": "K", "markers": ["KOKKOS_LAMBDA"]},
                                                {"name": "H"}], {"f.cpp": src})
    assert [f["zone"] for f in funcs] == ["H", "K", "K", "H"]


@pytest.mark.parametrize("src", [F5, MULTI])
def test_function_nloc_sum_conserved(src):
    assert (sum(f.nloc for f in analyze("f.cpp", src, CPP1).functions)
            == sum(f.nloc for f in analyze("f.cpp", src, NO_SPLIT).functions))


def test_half_written_file_falls_back_to_unsplit():
    src = MULTI.replace("while (i > 0) {", "while (i > 0) )")     # a stray ) in a kernel
    one, two = analyze("f.cpp", src, CPP1).functions, analyze("f.cpp", src, NO_SPLIT).functions
    assert sum(f.ccn - 1 for f in one) == sum(f.ccn - 1 for f in two)
    assert all("None" not in f.name for f in one)


def test_mismatched_brackets_are_not_paired():
    from cce_code_health.cpp_kernels import code_tokens, find_kernels
    toks = code_tokens("f(KOKKOS_LAMBDA(int i) { g(i]; });")
    assert find_kernels(toks, {"KOKKOS_LAMBDA"}, set()) == []


# cpp@1 rule (4): captures (docs/CPP1_LAYERS.md; F6 in docs/cpp1/f1.cpp)


def test_f6_captures():
    f = fns(F5, CPP1)
    assert f["s::k"].captures == 3              # [a, &b, c = 2 * n]
    assert f["s::k2"].captures == 4             # KOKKOS_LAMBDA ([=]) using a, dt, b, n
    assert f["s"].captures == 0


def test_captures_of_nested_and_class_lambdas():
    f = fns(MULTI, CPP1)
    assert f["ns::run::outer"].captures == 2               # p, n (m and j are its own)
    assert f["ns::run::outer::lambda#1"].captures == 0     # [&] using only its parameter
    assert f["ns::run::lambda#1"].captures == 1            # KOKKOS_CLASS_LAMBDA: this
    assert f["ns::run::lambda#2"].captures == 1            # p


CAPS = """\
struct S { int m; };
void t(View a, View b, S s, int n, int k) {
  int loc = 3;
  Kokkos::parallel_for(n, KOKKOS_LAMBDA(int i) { s.m = a(i) + ns::b + n::x + i; });
  Kokkos::parallel_for(n, [&, a](int i) { int k = 1; a(i) = b(i) + k + loc; });
  Kokkos::parallel_for(n, [=, *this](int i) { a(i) = b(i); });
  Kokkos::parallel_for(n, [this](int i) { m = i; });
}
"""


def test_declared_heuristic():
    from cce_code_health.cpp_kernels import code_tokens, declared
    t = code_tokens("if (j && n) a->g(x); auto&& r = 1; for (const auto& e : v) {} T* q = 0; "
                    "if (i > m) f(a * b, c); std::vector<int> w; ns::h(y);")
    assert declared(t, 0, len(t)) == {"r", "e", "q", "w"}


def test_capture_rules():
    f = fns(CAPS, CPP1)
    # names after . -> :: and before :: are not captures; s and a are
    assert f["t::lambda#1"].captures == 2
    # explicit a once, plus b and loc by default; the body's own k is not a capture
    assert f["t::lambda#2"].captures == 3
    assert f["t::lambda#3"].captures == 3      # *this, plus a and b by default
    assert f["t::lambda#4"].captures == 1      # this; an implicit member is not seen


def _wide(n):
    names = [f"v{j}" for j in range(n)]
    sig = ", ".join(f"int {v}" for v in names)
    return f"void w({sig}) {{\n  Kokkos::parallel_for(1, KOKKOS_LAMBDA(int i) {{ g({', '.join(names)}); }});\n}}\n"


def test_captures_debt_only_in_cpp1():
    from cce_code_health.report import file_report
    rep = file_report(analyze("f.cpp", _wide(14), CPP1), 0, CPP1)
    lam = next(f for f in rep["functions"] if f["name"] == "w::lambda#1")
    assert (lam["captures"], lam["debt_parts"]) == (14, {"captures": 1.0})
    assert next(f for f in rep["functions"] if f["name"] == "w")["captures"] == 0
    assert all("captures" not in f for f in file_report(analyze("f.cpp", _wide(14), CPP0), 0, CPP0)["functions"])
    rep8 = file_report(analyze("f.cpp", _wide(12), CPP1), 0, CPP1)
    assert all("captures" not in f["debt_parts"] for f in rep8["functions"])


def test_captures_suppression():
    from cce_code_health.report import file_report
    src = _wide(14).replace("  Kokkos", "  // codehealth: allow captures -- views of one cell\n  Kokkos")
    rep = file_report(analyze("f.cpp", src, CPP1), 0, CPP1)
    lam = next(f for f in rep["functions"] if f["name"] == "w::lambda#1")
    assert (lam["captures"], lam["debt_parts"]) == (14, {})


def _caps(body, sig="View a, View b, View y, View x, double dt, double tol, int flag, View mask, double c"):
    src = f"void t({sig}) {{\n  double* p = 0;\n  Kokkos::parallel_for(1, KOKKOS_LAMBDA(int i) {{ {body} }});\n}}\n"
    return fns(src, CPP1)["t::lambda#1"].captures


@pytest.mark.parametrize("body,n", [
    ("y(i) = x(i) * dt;", 3),                       # dt after * is a use, not a declaration
    ("if (i > 0 && mask[i]) a(i) = 1;", 2),
    ("if (i > 0 && flag == 1) a(i) = 1;", 2),
    ("bool q = a(i) > tol;", 2),
    ("*p = a(i);", 2),
    ("a(i) = i > 0 ? b(i) * c : 0;", 3),
])
def test_operands_are_not_declarations(body, n):
    assert _caps(body) == n


def test_local_views_and_nested_kernel_params():
    src = """\
void t(int n) {
  Kokkos::View<double*> u("u", n);
  Kokkos::View<double*> v("v", n);
  Kokkos::parallel_for(n, KOKKOS_LAMBDA(int i) { u(i) = v(i); });
  Kokkos::parallel_for("team", policy, KOKKOS_LAMBDA(const member_type& team) {
    const int r = team.league_rank();
    Kokkos::parallel_for(Kokkos::TeamThreadRange(team, n), [&](const int j) {
      Kokkos::parallel_for(Kokkos::ThreadVectorRange(team, n), [&](const int k) { u(r) += team.team_rank() + k; });
      v(j) = r;
    });
  });
}
"""
    f = fns(src, CPP1)
    assert f["t::lambda#1"].captures == 2                    # local views u, v
    assert f["t::team"].captures == 3                        # n, u, v
    assert f["t::team::lambda#1"].captures == 5              # team, n, r, v, and u from inside
    assert f["t::team::lambda#1::lambda#1"].captures == 3    # team (two kernels up), u, r
