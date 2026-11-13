"""Gate features for C++ suites: skip_exit, fail_regex, findings and convergence tiers, flags."""
import json
import math

import pytest
from test_gate import MOD, PY
from test_git import commit, git

from cce_code_health import gate as gatemod
from cce_code_health.cli import main
from cce_code_health.config import check

SKIP_RX = r"\*\*\*Skipped|Not Run|SKIPPED"


def make_repo(path, cfg, files=None):
    git(path, "init", "-q", "-b", "main")
    commit(path, {".codehealth/config.toml": cfg, ".gitignore": "__pycache__/\n",
                  "pkg/__init__.py": "", "pkg/mod.py": MOD, **(files or {})})
    return path


def gate_json(repo, *extra):
    out = repo.parent / "g.json"
    rc = main(["gate", "--repo", str(repo), "--log", str(repo.parent / "gate.log"), "--json", str(out), *extra])
    return rc, {c["name"]: c for c in json.loads(out.read_text())["checks"]}


def doc_edit(repo):
    (repo / "pkg/mod.py").write_text(MOD.replace("Docstrings may change.", "Changed doc."))


def py_config(tiers, gate_extra=""):
    return f'schema = 1\n[scope]\ninclude = ["pkg/*"]\n[gate]\n{gate_extra}\n' + tiers


# -- skip_exit and fail_regex ---------------------------------------------------------------

UNIT_77 = f"""
[[gate.tier]]
name = "unit"
skip_exit = 77
cmd = '''{PY} -c "import pkg.mod as m, sys; sys.exit(0 if m.calc(3) == 10 else 77)"'''
"""


def test_skip_exit_fails_step_and_is_error_in_self_test(tmp_path, capsys):
    repo = make_repo(tmp_path, py_config(UNIT_77))
    (repo / "pkg/mod.py").write_text(MOD.replace("x * 3 + 1", "x * 3 + 2"))
    rc, checks = gate_json(repo)
    assert rc == 1 and checks["unit"]["detail"] == "not run (exit 77)" and checks["unit"]["exit_code"] == 77
    git(repo, "checkout", "-q", "--", "pkg/mod.py")
    out = tmp_path.parent / "st.json"
    rc = main(["gate", "--repo", str(repo), "--self-test", "--log", str(tmp_path.parent / "s.log"),
               "--json", str(out)])
    c = json.loads(out.read_text())["checks"][0]
    assert rc == 1 and c["outcome"] == "error" and c["detail"] == "not run (exit 77)"
    assert "ERROR" in capsys.readouterr().out


def test_skip_exit_list_and_baseline(tmp_path):
    repo = make_repo(tmp_path, py_config('[[gate.tier]]\nname = "unit"\nskip_exit = [75, 77]\ncmd = "exit 75"\n'))
    doc_edit(repo)
    rc, checks = gate_json(repo)
    assert rc == 1 and checks["unit"]["detail"] == "not run (exit 75)"


def test_fail_regex_on_command_tier(tmp_path):
    tier = f"""
[[gate.tier]]
name = "ctest"
fail_regex = '{SKIP_RX}'
cmd = "echo '100% tests passed'; echo '  3/4 Test  #3: slow ....***Skipped   0.00 sec'"
"""
    repo = make_repo(tmp_path, py_config(tier))
    doc_edit(repo)
    rc, checks = gate_json(repo)
    assert rc == 1
    assert checks["ctest"]["detail"] == "output matched fail_regex: 3/4 Test  #3: slow ....***Skipped   0.00 sec"


def test_fail_regex_ignores_the_command_line_itself(tmp_path):
    tier = f"""
[[gate.tier]]
name = "ctest"
fail_regex = '{SKIP_RX}'
cmd = "echo fine  # SKIPPED appears only in the command"
"""
    repo = make_repo(tmp_path, py_config(tier))
    doc_edit(repo)
    rc, checks = gate_json(repo)
    assert rc == 0 and checks["ctest"]["ok"]


def test_fail_regex_on_reference_base_run(tmp_path):
    tier = f"""
[[gate.tier]]
name = "golden"
kind = "reference"
outputs = ["r.txt"]
fail_regex = '{SKIP_RX}'
cmd = '''{PY} -c "import os, sys; print('SKIPPED' if os.path.exists('pkg/marker.py') else 'ran'); open(sys.argv[1] + '/r.txt', 'w').write('1')" {{out}}'''
"""
    repo = make_repo(tmp_path, py_config(tier), {"pkg/marker.py": ""})
    (repo / "pkg/marker.py").unlink()                 # head does not skip, the base run does
    for _ in range(2):                                # a matched base run is not cached
        rc, checks = gate_json(repo)
        assert rc == 1 and checks["golden"]["detail"] == "output matched fail_regex: SKIPPED"


# -- findings tier --------------------------------------------------------------------------

TIDY = '''
import os, sys, glob
with open(sys.argv[1], "a") as f:
    f.write("run\\n")
for path in sorted(glob.glob("src/*.cpp")):
    for no, line in enumerate(open(path), 1):
        if "WARN(" in line:
            check = line.split("WARN(")[1].split(")")[0]
            col = line.index("WARN(") + 1
            print(f"{os.path.abspath(path)}:{no}:{col}: warning: something [{check}]")
print("/usr/include/c++/v1/vector:10:3: warning: external [bugprone-x]")
sys.exit(1)
'''
CPP = "int f(int x) {\n    return x + 1;  // WARN(bugprone-x)\n}\n"


def findings_repo(tmp_path, extra=""):
    counter = tmp_path.parent / "tidy-runs.txt"
    cfg = f"""schema = 1
[profiles]
cpp = "cpp@0"
[scope]
include = ["src/*"]
[[gate.tier]]
name = "unit"
cmd = "exit 0"
[[gate.tier]]
name = "tidy"
kind = "findings"
cmd = "{PY} tools/tidy.py {counter}"
{extra}
"""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, "tools/tidy.py": TIDY, "src/a.cpp": CPP})
    return tmp_path, counter


def test_findings_rise_fails_equal_or_lower_passes(tmp_path):
    repo, counter = findings_repo(tmp_path)
    (repo / "src/a.cpp").write_text(CPP + "int g() { return 2; }  // WARN(bugprone-x)\n")
    rc, checks = gate_json(repo)
    assert rc == 1
    assert checks["tidy"]["detail"] == "findings rose: src/a.cpp [bugprone-x] 1 -> 2; base 2, head 3"
    (repo / "src/a.cpp").write_text(CPP + "int g() { return 2; }  // WARN(modernize-y)\n")
    rc, checks = gate_json(repo)
    assert rc == 1 and "src/a.cpp [modernize-y] 0 -> 1" in checks["tidy"]["detail"]
    (repo / "src/a.cpp").write_text("// moved\n" + CPP)            # same count, other line: pass
    rc, checks = gate_json(repo)
    assert rc == 0 and checks["tidy"]["detail"] == "base 2, head 2"
    (repo / "src/a.cpp").write_text(CPP.replace("  // WARN(bugprone-x)", ""))
    rc, checks = gate_json(repo)
    assert rc == 0 and checks["tidy"]["detail"] == "base 2, head 1"
    assert counter.read_text().count("run") == 5                  # base ran once, then cached


def test_findings_rename_maps_base_path(tmp_path):
    repo, _ = findings_repo(tmp_path)
    git(repo, "mv", "src/a.cpp", "src/b.cpp")
    rc, checks = gate_json(repo)
    assert rc == 0 and checks["tidy"]["detail"] == "base 2, head 2"


def test_findings_ignore_exit_false(tmp_path):
    repo, _ = findings_repo(tmp_path, "ignore_exit = false")
    (repo / "src/a.cpp").write_text("// moved\n" + CPP)
    rc, checks = gate_json(repo)
    assert rc == 1 and checks["tidy"]["detail"] == "exit 1 at base"


def test_parse_findings_normalises_paths(tmp_path):
    base, head = tmp_path / "base", tmp_path / "head"
    text = (f"{base}/src/a.cpp:3:5: warning: x [c1]\n"
            f"{base}/src/a.cpp:3:5: warning: x [c1]\n"            # duplicate: counted once
            f"src/../src/a.cpp:4:1: error: y [c2]\n"
            "/usr/include/v.h:1:1: warning: z [c3]\n"
            f"{base}/src/a.cpp:9:9: note: no check here\n")
    assert gatemod.parse_findings(text, base) == {("src/a.cpp", 3, 5, "c1"), ("src/a.cpp", 4, 1, "c2"),
                                                  ("external:/usr/include/v.h", 1, 1, "c3")}
    assert gatemod.parse_findings(f"{head}/src/a.cpp:3:5: warning: x [c1]\n", head) == {
        ("src/a.cpp", 3, 5, "c1")}


# -- flags ----------------------------------------------------------------------------------

CMAKE = """cmake_minimum_required(VERSION 3.20)
project(p CXX)
add_executable(app src/a.cpp)
target_compile_options(app PRIVATE -O2 -ffp-contract=off)
"""


def flags_repo(tmp_path, cpp=True, gate_extra=""):
    cfg = ('schema = 1\n' + ('[profiles]\ncpp = "cpp@0"\n' if cpp else "")
           + f'[scope]\ninclude = ["*"]\n[gate]\n{gate_extra}\n[[gate.tier]]\nname = "unit"\ncmd = "exit 0"\n')
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, "CMakeLists.txt": CMAKE, "src/a.cpp": CPP,
                      "pkg/mod.py": MOD})
    return tmp_path


@pytest.mark.parametrize("old,new,ok,hit", [
    ("project(p CXX)\n", "project(p CXX)\nadd_compile_options(-ffast-math)\n", False,
     "CMakeLists.txt:3: +add_compile_options(-ffast-math)"),
    (" -ffp-contract=off)", ")", False, "CMakeLists.txt:4: -target_compile_options(app PRIVATE -O2 -ffp-contract=off)"),
    ("-O2", "-O3", False, "CMakeLists.txt:4: +target_compile_options(app PRIVATE -O3 -ffp-contract=off)"),
    ("add_executable(app src/a.cpp)", "add_executable(app src/a.cpp src/b.cpp)", True, ""),
])
def test_flags_in_cmake(tmp_path, old, new, ok, hit):
    repo = flags_repo(tmp_path)
    (repo / "CMakeLists.txt").write_text(CMAKE.replace(old, new))
    _, checks = gate_json(repo)
    assert checks["flags"]["ok"] is ok
    assert hit in checks["flags"]["detail"]


def test_flags_in_source_and_user_patterns(tmp_path):
    repo = flags_repo(tmp_path, gate_extra="flag_patterns = ['EIGEN_DONT_VECTORIZE']")
    (repo / "src/a.cpp").write_text("#pragma omp parallel for num_threads(4)\n" + CPP)
    _, checks = gate_json(repo)
    assert not checks["flags"]["ok"] and "src/a.cpp:1: +#pragma omp" in checks["flags"]["detail"]
    (repo / "src/a.cpp").write_text("#define EIGEN_DONT_VECTORIZE\n" + CPP)
    _, checks = gate_json(repo)
    assert not checks["flags"]["ok"]
    (repo / "src/a.cpp").write_text("// set_flags(CMAKE_CXX_FLAGS) is fine in a source file\n" + CPP)
    _, checks = gate_json(repo)
    assert checks["flags"]["ok"]


def test_flags_inactive_for_python_only_config(tmp_path):
    repo = flags_repo(tmp_path, cpp=False)
    (repo / "CMakeLists.txt").write_text(CMAKE + "add_compile_options(-ffast-math)\n")
    _, checks = gate_json(repo)
    assert "flags" not in checks


def test_flags_switch(tmp_path):
    repo = flags_repo(tmp_path, cpp=False, gate_extra="flags = true")
    (repo / "CMakeLists.txt").write_text(CMAKE + "add_compile_options(-ffast-math)\n")
    _, checks = gate_json(repo)
    assert not checks["flags"]["ok"]
    off = tmp_path.parent / "off"
    off.mkdir()
    repo = flags_repo(off, cpp=True, gate_extra="flags = false")
    (repo / "CMakeLists.txt").write_text(CMAKE + "add_compile_options(-ffast-math)\n")
    _, checks = gate_json(repo)
    assert "flags" not in checks


# -- convergence tier -----------------------------------------------------------------------

def conv_repo(tmp_path, lines, extra=""):
    data = "\\n".join(lines)
    tier = f"""
[[gate.tier]]
name = "unit"
cmd = "exit 0"
[[gate.tier]]
name = "conv"
kind = "convergence"
outputs = ["conv.txt"]
order = 2.0
band = [1.8, 2.2]
{extra}
cmd = "printf '{data}\\n' > {{out}}/conv.txt"
"""
    repo = make_repo(tmp_path, py_config(tier))
    doc_edit(repo)
    return repo


def test_convergence_pass(tmp_path):
    repo = conv_repo(tmp_path, ["# h error", "0.1 1.0e-2", "0.05 2.5e-3  # level 2", "0.025 6.4e-4"])
    rc, checks = gate_json(repo)
    assert rc == 0 and checks["conv"]["detail"] == "orders 2.00 1.97 (band 1.8..2.2)"


def test_convergence_out_of_band_and_last(tmp_path):
    lines = ["0.1 1.0e-2", "0.05 5.0e-3", "0.025 1.25e-3"]           # orders 1.00 2.00
    rc, checks = gate_json(conv_repo(tmp_path, lines))
    assert rc == 1 and checks["conv"]["detail"] == "orders 1.00 2.00 outside band 1.8..2.2"
    other = tmp_path.parent / "last"
    other.mkdir()
    rc, checks = gate_json(conv_repo(other, lines, "last = 1"))
    assert rc == 0 and checks["conv"]["detail"] == "orders 1.00 2.00 (band 1.8..2.2, last 1 checked)"


def test_convergence_too_few_levels(tmp_path):
    rc, checks = gate_json(conv_repo(tmp_path, ["0.1 1e-2"]))
    assert rc == 1 and checks["conv"]["detail"] == "need at least 2 levels, got 1"


def test_observed_orders_rejects_bad_values():
    assert gatemod.observed_orders([(0.1, 0.0), (0.05, 1.0)])[1].startswith("level 1: non-positive")
    assert "non-finite" in gatemod.observed_orders([(0.1, math.nan), (0.05, 1.0)])[1]
    assert "equal h" in gatemod.observed_orders([(0.1, 1.0), (0.1, 0.5)])[1]
    orders, why = gatemod.observed_orders([(0.2, 4.0), (0.1, 1.0)])
    assert not why and orders == pytest.approx([2.0])


# -- config --check -------------------------------------------------------------------------

@pytest.mark.parametrize("tier,ok", [
    ('kind = "command"\ncmd = "x"\nskip_exit = 77\nfail_regex = "SKIP"', True),
    ('kind = "command"\ncmd = "x"\nskip_exit = [77, 75]', True),
    ('kind = "command"\ncmd = "x"\nskip_exit = "77"', False),
    ('kind = "command"\ncmd = "x"\nfail_regex = "(unclosed"', False),
    ('kind = "findings"\ncmd = "x"\nformat = "gcc"\nignore_exit = false', True),
    ('kind = "findings"\ncmd = "x"\nformat = "msvc"', False),
    ('kind = "command"\ncmd = "x"\nignore_exit = true', False),
    ('kind = "convergence"\ncmd = "x"\noutputs = ["c.txt"]\norder = 2.0\nband = [1.8, 2.2]\nlast = 2', True),
    ('kind = "convergence"\ncmd = "x"\noutputs = ["c.txt", "d.txt"]\norder = 2.0\nband = [1.8, 2.2]', False),
    ('kind = "convergence"\ncmd = "x"\noutputs = ["c.txt"]\norder = 3.0\nband = [1.8, 2.2]', False),
    ('kind = "convergence"\ncmd = "x"\noutputs = ["c.txt"]\norder = 2.0\nband = [2.2, 1.8]', False),
])
def test_config_check_build_tier_keys(tier, ok):
    _, bad = check(f'schema = 1\n[[gate.tier]]\nname = "t"\n{tier}\n')
    assert (not bad) is ok, bad


@pytest.mark.parametrize("gate_extra,ok", [
    ("flags = true\nflag_patterns = ['-mavx512', 'EIGEN_.*']", True),
    ("flags = 1", False),
    ("flag_patterns = ['(']", False),
])
def test_config_check_flags(gate_extra, ok):
    _, bad = check(f"schema = 1\n[gate]\n{gate_extra}\n")
    assert (not bad) is ok, bad


# -- self-test on C++ sources ------------------------------------------------------------------

def test_cpp_literal_sites_skip_comments_strings_preprocessor():
    src = ('int f() {\n  /* 3 */ double x = 0.5f * 2 + 1e-3; // 7\n'
           '  const char* c = "9"; return x > 3u;\n#define A 5\n  int o = 017;\n}\n')
    sites = gatemod._cpp_literal_sites(src, 2)
    assert [s[3] for s in sites] == [0.5, 2, 0.001]
    assert [s[3] for s in gatemod._cpp_literal_sites(src, 3)] == [3]
    assert gatemod._cpp_literal_sites(src, 4) == [] and gatemod._cpp_literal_sites(src, 5) == []
    text, what = gatemod.mutate_source(src, sites[0])
    assert "double x = 1.75f * 2" in text and what == "line 2 col 22: 0.5 -> 1.75"


CPP_REF = """
schema = 1
[profiles]
cpp = "cpp@0"
[scope]
include = ["src/*"]
[[gate.tier]]
name = "hash"
kind = "reference"
outputs = ["o.txt"]
timeout = 60
cmd = "sed -n 3p {repo}/src/k.hpp > {out}/o.txt"
"""


def test_self_test_mutates_a_cpp_literal(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": CPP_REF,
                      "src/k.hpp": "// kernel\ninline double k(double x) {\n  return 0.25 * x;  // 9\n}\n"})
    out = tmp_path.parent / "st.json"
    rc = main(["gate", "--repo", str(tmp_path), "--self-test", "--mutate", "src/k.hpp:3",
               "--log", str(tmp_path.parent / "st.log"), "--json", str(out)])
    data = json.loads(out.read_text())
    assert rc == 0 and data["checks"][0]["outcome"] == "detected"
    assert main(["gate", "--repo", str(tmp_path), "--self-test",
                 "--log", str(tmp_path.parent / "st.log")]) == 2       # C++ needs --mutate


# -- same_lines: dependency resolution must not differ between base and head --------------------

SAME = """
schema = 1
[scope]
include = ["pkg/*"]
[[gate.tier]]
name = "ref"
kind = "reference"
outputs = ["o.txt"]
timeout = 60
same_lines = '^-- Found dep'
cmd = "if [ -d {repo}/../sib ]; then echo '-- Found dep: sibling'; else echo '-- Found dep: fetched v1.3.0'; fi; echo '-- Found depdir: {repo}/x'; echo 42 > {out}/o.txt"
"""


def test_same_lines_catches_a_dependency_resolved_differently(tmp_path):
    # the dem case: a sibling checkout exists next to the repo but not next to the base worktree
    assert not check(SAME)[1]
    (tmp_path / "r").mkdir()
    repo = make_repo(tmp_path / "r", SAME)
    doc_edit(repo)
    rc, checks = gate_json(repo)
    assert rc == 0 and checks["ref"]["ok"]                     # tree paths normalised to {repo}
    (tmp_path / "sib").mkdir()
    rc, checks = gate_json(repo)                               # base lines come from the cache
    assert rc == 1 and "same_lines differ" in checks["ref"]["detail"]
    assert "fetched v1.3.0" in checks["ref"]["detail"] and "sibling" in checks["ref"]["detail"]


def test_same_lines_only_on_reference_tiers():
    bad = check(SAME.replace('kind = "reference"', 'kind = "command"'))[1]
    assert any("same_lines is for reference tiers" in b for b in bad)
