"""Zones: debt reported per layer (docs/CPP1_LAYERS.md step 1, paths only). Reporting only."""
import json
import sys

import pytest
from test_git import brain, commit, git, nested

from cce_code_health import config, zones
from cce_code_health.cli import main

K = f"[[{config.ZONES_KEY}]]"          # the key is named once, in config.py
BASE = 'schema = 1\n[scope]\ninclude = ["pkg/*"]\n'
ZONES = BASE + f'''
{K}
name = "core"
paths = ["pkg/core/*"]
{K}
name = "rest"
'''


def make(tmp_path, cfg):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, "pkg/core/a.py": nested(6, "f"),
                      "pkg/ui/b.py": nested(5, "g")}, "base")
    return tmp_path


def scan(repo, tmp_path):
    out = tmp_path.parent / f"{tmp_path.name}-scan.json"
    assert main(["scan", "--repo", str(repo), "--json", str(out)]) == 0
    return json.loads(out.read_text())


# -- config -----------------------------------------------------------------------------------

def test_key_is_named_once():
    assert config.ZONES_KEY == "zones" and config.ZONES_KEY in config.TOP_KEYS


def test_check_accepts_rules_and_reports_them():
    info, bad = config.check(ZONES)
    assert not bad
    assert "zones: 2 rule(s), zone(s) core, rest" in info
    assert not any("catch-all" in i for i in info)


def test_check_warns_without_catch_all():
    info, bad = config.check(BASE + f'{K}\nname = "core"\npaths = ["pkg/core/*"]\n')
    assert not bad
    assert any(i.startswith("WARNING") and "catch-all" in i for i in info)


@pytest.mark.parametrize("entry,why", [
    ('name = ""', "name"), ('name = "-"', "name"), ("paths = ['x']", "name"),
    ('name = "a"\npaths = []', "paths"), ('name = "a"\npaths = "x"', "paths"),
    ('name = "a"\nmarkers = ["K-1"]', "markers must be"), ('name = "a"\nmarkers = []', "markers must be"),
    ('name = "a"\ncolour = 1', "unknown key 'colour'"),
])
def test_check_rejects_bad_rules(entry, why):
    _, bad = config.check(BASE + f"{K}\n{entry}\n")
    assert any(why in b for b in bad), bad
    with pytest.raises(ValueError, match=why):
        config.parse(BASE + f"{K}\n{entry}\n", "t")


def test_check_rejects_a_table():
    _, bad = config.check(BASE + '[zones]\nname = "a"\n')
    assert bad == [f"{K} must be an array of tables"]


def test_first_matching_rule_wins_and_names_repeat():
    rules = [{"name": "dev", "paths": ["k/*.hpp"]}, {"name": "user", "paths": ["k/u*"]},
             {"name": "dev", "paths": ["d/*"]}]
    assert [zones.file_zone(rules, p) for p in ("k/u.hpp", "k/u.cpp", "d/x.cpp", "e.cpp")] == \
        ["dev", "user", "dev", zones.UNMATCHED]


# -- scan ---------------------------------------------------------------------------------------

def test_scan_without_zones_has_no_zone_keys(tmp_path):
    data = scan(make(tmp_path, BASE), tmp_path)
    assert "zones" not in data
    assert not any("zone" in r or any("zone" in f for f in r["functions"]) for r in data["files"].values())


def test_scan_reports_per_zone(tmp_path, capsys):
    data = scan(make(tmp_path, ZONES), tmp_path)
    files = data["files"]
    assert files["pkg/core/a.py"]["zone"] == "core" and files["pkg/ui/b.py"]["zone"] == "rest"
    assert all(f["zone"] == r["zone"] for r in files.values() for f in r["functions"])
    z = data["zones"]
    assert list(z) == ["core", "rest"]
    for name, path in (("core", "pkg/core/a.py"), ("rest", "pkg/ui/b.py")):
        r = files[path]
        assert z[name]["files"] == 1 and z[name]["nloc"] == r["nloc"]
        assert z[name]["debt"] == pytest.approx(r["debt"])
        assert z[name]["functions"] == len(r["functions"])
    assert z["core"]["debt"] > z["rest"]["debt"] > 0
    assert sum(x["debt"] for x in z.values()) == pytest.approx(data["totals"]["debt"])
    out = capsys.readouterr().out
    assert "zone " in out and "\ncore " in out and "\nrest " in out


def test_unmatched_files_are_zone_dash(tmp_path):
    cfg = BASE + f'{K}\nname = "core"\npaths = ["pkg/core/*"]\n'
    z = scan(make(tmp_path, cfg), tmp_path)["zones"]
    assert list(z) == ["core", "-"] and z["-"]["files"] == 1


def test_empty_configured_zone_is_shown(tmp_path):
    cfg = ZONES.replace('name = "rest"', f'name = "gpu"\npaths = ["pkg/gpu/*"]\n{K}\nname = "rest"')
    z = scan(make(tmp_path, cfg), tmp_path)["zones"]
    assert list(z) == ["core", "gpu", "rest"] and z["gpu"]["files"] == 0 and z["gpu"]["debt"] == 0


# -- diff and gate ------------------------------------------------------------------------------

def test_diff_reports_zone_debt_and_new_brains(tmp_path, capsys):
    repo = make(tmp_path, ZONES)
    git(repo, "checkout", "-qb", "work")
    commit(repo, {"pkg/core/a.py": nested(2, "f"), "pkg/ui/b.py": nested(5, "g") + "\n\n" + brain()})
    out_json = tmp_path.parent / "zd.json"
    assert main(["diff", "--repo", str(repo), "--base", "main", "--head", "HEAD", "--json", str(out_json)]) == 1
    zc = json.loads(out_json.read_text())["zones"]
    assert zc["core"]["debt_after"] < zc["core"]["debt_before"] and zc["core"]["new_brains"] == 0
    assert zc["rest"]["new_brains"] == 1
    out = capsys.readouterr().out
    assert "zone core: debt" in out and "zone rest: debt" in out and "new brains 1" in out


def test_diff_without_zones_has_no_zone_rows(tmp_path, capsys):
    repo = make(tmp_path, BASE)
    git(repo, "checkout", "-qb", "work")
    commit(repo, {"pkg/core/a.py": nested(2, "f")})
    out_json = tmp_path.parent / "zd.json"
    assert main(["diff", "--repo", str(repo), "--base", "main", "--head", "HEAD", "--json", str(out_json)]) == 0
    assert "zones" not in json.loads(out_json.read_text())
    assert "  zone " not in capsys.readouterr().out


@pytest.mark.parametrize("with_zones", [True, False])
def test_gate_debt_detail_names_zones_only_when_configured(tmp_path, with_zones):
    cfg = (ZONES if with_zones else BASE) + f"[[gate.tier]]\nname = \"unit\"\ncmd = '{sys.executable} -c \"pass\"'\n"
    repo = make(tmp_path, cfg)
    (repo / "pkg/core/a.py").write_text(nested(3, "f"))
    out = tmp_path.parent / "g.json"
    assert main(["gate", "--repo", str(repo), "--log", str(tmp_path.parent / "gate.log"), "--json", str(out)]) == 0
    detail = {c["name"]: c for c in json.loads(out.read_text())["checks"]}["debt"]["detail"]
    assert ("zones core " in detail and " -> 0.0" in detail) if with_zones else "zones" not in detail


@pytest.mark.parametrize("paths", ['["*"]', '["**"]', '["**/*"]'])
def test_glob_catch_all_needs_no_warning(paths):
    info, bad = config.check(BASE + f'{K}\nname = "a"\npaths = ["x/*"]\n{K}\nname = "b"\npaths = {paths}\n')
    assert not bad and not any("WARNING" in i for i in info)


def test_rules_after_a_catch_all_warn_and_empty_array_fails():
    info, _ = config.check(BASE + f'{K}\nname = "all"\n{K}\nname = "late"\npaths = ["x/*"]\n')
    assert any("entry 1 matches every file" in i for i in info)
    _, bad = config.check(f"{config.ZONES_KEY} = []\n" + BASE)
    assert bad and "is empty" in bad[0]


def test_diff_rename_across_zones_moves_debt(tmp_path):
    repo = make(tmp_path, ZONES)
    git(repo, "checkout", "-qb", "work")
    git(repo, "mv", "pkg/core/a.py", "pkg/ui/a.py")
    git(repo, "commit", "-qm", "move")
    out_json = tmp_path.parent / "zr.json"
    main(["diff", "--repo", str(repo), "--base", "main", "--head", "HEAD", "--json", str(out_json)])
    zc = json.loads(out_json.read_text())["zones"]
    assert zc["core"]["debt_before"] > 0 and zc["core"]["debt_after"] == 0
    assert zc["rest"]["debt_after"] == pytest.approx(zc["rest"]["debt_before"] + zc["core"]["debt_before"])


@pytest.mark.parametrize("extra", [[], ["--behaviour-only"]])
def test_gate_fails_a_step_that_adds_malformed_zones(tmp_path, extra):
    cfg = BASE + f"[[gate.tier]]\nname = \"unit\"\ncmd = '{sys.executable} -c \"pass\"'\n"
    repo = make(tmp_path, cfg)
    (repo / ".codehealth/config.toml").write_text(cfg + f'{K}\nname = ""\n')
    (repo / "pkg/core/a.py").write_text(nested(3, "f"))
    out = tmp_path.parent / "g.json"
    rc = main(["gate", "--repo", str(repo), "--log", str(tmp_path.parent / "gate.log"), "--json", str(out), *extra])
    checks = {c["name"]: c for c in json.loads(out.read_text())["checks"]}
    assert rc == 1 and not checks["config"]["ok"] and "name must be" in checks["config"]["detail"]


# -- markers (step 2) ---------------------------------------------------------------------------

KERNEL_HPP = """\
#pragma once
// KOKKOS_INLINE_FUNCTION in a comment marks nothing
inline int host_launch(int n) {
  int s = 0;
  for (int i = 0; i < n; ++i) { if (i % 2) { s += i; } }
  return s;
}

template <class T>
KOKKOS_INLINE_FUNCTION
T device_fn(T x) {
  if (x > 0) { return x; }
  return -x;
}

struct Functor {
  KOKKOS_FUNCTION void operator()(const int i) const { (void)i; }
  void host_member() const { /* KOKKOS_FUNCTION */ }
};
"""
MARKED = BASE.replace('"pkg/*"', '"pkg/*", "src/*"') + f'''[profiles]
cpp = "cpp@0"
{K}
name = "user"
paths = ["pkg/*"]
{K}
name = "device"
markers = ["KOKKOS_INLINE_FUNCTION", "KOKKOS_FUNCTION"]
{K}
name = "host"
'''


def test_markers_zone_functions_not_files(tmp_path):
    pytest.importorskip("lizard")
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": MARKED, "src/k.hpp": KERNEL_HPP,
                      "pkg/m.py": "def KOKKOS_FUNCTION():\n    return 1\n"}, "base")
    data = scan(tmp_path, tmp_path)
    k = data["files"]["src/k.hpp"]
    assert k["zone"] == "host"
    zone = {f["name"].rsplit("::", 1)[-1]: f["zone"] for f in k["functions"]}
    assert zone == {"host_launch": "host", "device_fn": "device", "operator()": "device",
                    "host_member": "host"}, zone
    assert data["files"]["pkg/m.py"]["functions"][0]["zone"] == "user"   # markers are C++ only
    z = data["zones"]
    assert z["device"]["functions"] == 2 and z["device"]["files"] == 0
    assert z["host"]["files"] == 1 and z["host"]["functions"] == 2


def test_marker_rules_are_not_catch_alls():
    info, bad = config.check(BASE + f'{K}\nname = "dev"\nmarkers = ["K"]\n')
    assert not bad and any("no catch-all" in i for i in info)
    assert "(1 with markers, C++ only)" in info[-2]
    rules = [{"name": "dev", "markers": ["K"]}, {"name": "rest", "paths": ["x/*"]}]
    assert zones.file_zone(rules, "x/a.hpp") == "rest"
    assert zones.function_zone(rules, "x/a.hpp", {"K", "int"}) == "dev"
    assert zones.function_zone(rules, "y/a.hpp", set()) == zones.UNMATCHED
