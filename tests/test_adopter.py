"""Adopter feedback: {ref_base}, exit codes, detect_exit, debt and --require-gain, log, --mutate."""
import json

import pytest
from test_gate import CONFIG, MOD, PY, doc_edit, edit, gate, repo, set_config  # noqa: F401
from test_git import commit, git

from cce_code_health import gitio
from cce_code_health.cli import main
from cce_code_health.config import check


def sha(repo, ref="HEAD"):
    return gitio.rev(repo, ref)


def run(repo, *args):
    return main([*args[:1], "--repo", str(repo), *args[1:]])


def test_ref_base_placeholder_defaults_to_base_and_takes_flag(repo, capsys):
    sha0 = sha(repo)
    tier = ("\n[[gate.tier]]\nname = \"refs\"\ncmd = '''" + PY +
            " -c \"import sys; sys.exit(0 if sys.argv[1] == sys.argv[2] else 4)\" {base} {ref_base}'''\n")
    set_config(repo, CONFIG + tier)
    doc_edit(repo)
    assert gate(repo) == 0                         # no --ref-base: equals {base}
    strict = ("\n[[gate.tier]]\nname = \"refs\"\ncmd = '''" + PY +
              f" -c \"import sys; sys.exit(0 if sys.argv[2] == '{sha0}' != sys.argv[1] else 4)\" {{base}} {{ref_base}}'''\n")
    set_config(repo, CONFIG + strict)
    doc_edit(repo)
    assert gate(repo, "--ref-base", sha0) == 0
    assert gate(repo) == 1                         # default ref_base is base, not sha0
    assert "FAIL refs" in capsys.readouterr().out


def cmd_repo(tmp_path, detect=None, extra=""):
    cfg = CONFIG.split("[gate]")[0] + f"""
[gate]
[[gate.tier]]
name = "unit"
{('detect_exit = ' + detect) if detect else ''}
cmd = '{PY} -c "import pkg.mod as m, sys; sys.exit(0 if m.calc(3) == 10 else 5)"'
""" + extra
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, ".gitignore": "__pycache__/\n",
                      "pkg/__init__.py": "", "pkg/mod.py": MOD})
    return tmp_path


def self_test(repo, tmp_path):
    out = tmp_path.parent / "st.json"
    rc = main(["gate", "--repo", str(repo), "--self-test", "--log", str(tmp_path.parent / "s.log"),
               "--json", str(out)])
    return rc, json.loads(out.read_text())


def test_self_test_json_has_exit_codes(tmp_path, capsys):
    rc, data = self_test(cmd_repo(tmp_path), tmp_path)
    c = data["checks"][0]
    assert rc == 0 and (c["outcome"], c["exit_code"], c["baseline_exit_code"]) == ("detected", 5, 0)
    assert c["detail"] == "exit 5"
    assert "detected" in capsys.readouterr().out


def test_detect_exit_error_and_detected(tmp_path, capsys):
    repo = cmd_repo(tmp_path, detect="[1]")
    rc, data = self_test(repo, tmp_path)
    c = data["checks"][0]
    assert rc == 1 and c["outcome"] == "error" and c["exit_code"] == 5
    assert c["detail"] == "exit 5 not in detect_exit [1]"
    assert "ERROR" in capsys.readouterr().out
    set_config(repo, (repo / ".codehealth/config.toml").read_text().replace("[1]", "[1, 5]"))
    rc, data = self_test(repo, tmp_path)
    assert rc == 0 and data["checks"][0]["outcome"] == "detected"


@pytest.mark.parametrize("val,ok", [("[1, 5]", True), ("3", False), ("[0]", False), ("[\"a\"]", False),
                                    ("[true]", False)])
def test_config_check_detect_exit(val, ok):
    text = f'schema = 1\n[[gate.tier]]\nname = "t"\ncmd = "true"\ndetect_exit = {val}\n'
    assert (not check(text)[1]) is ok


def test_gate_json_exit_code_of_command_tier(repo):
    doc_edit(repo)
    out = repo.parent / "g.json"
    assert gate(repo, "--json", str(out)) == 0
    checks = {c["name"]: c for c in json.loads(out.read_text())["checks"]}
    assert checks["unit"]["exit_code"] == 0 and checks["golden"]["exit_code"] == 0
    assert checks["debt"]["exit_code"] is None


def test_mutate_literal_index(repo, capsys):
    out = repo.parent / "m.json"
    for spec, what in (("pkg/mod.py:7", "line 7 col 16 (literal 1 of 2): 3 -> 4"),
                       ("pkg/mod.py:7:2", "line 7 col 20 (literal 2 of 2): 1 -> 2")):
        assert main(["gate", "--repo", str(repo), "--self-test", "--mutate", spec,
                     "--log", str(repo.parent / "s.log"), "--json", str(out)]) == 0
        assert json.loads(out.read_text())["meta"]["mutation"] == f"pkg/mod.py {what}"
    assert main(["gate", "--repo", str(repo), "--self-test", "--mutate", "pkg/mod.py:7:3",
                 "--log", str(repo.parent / "s.log")]) == 2
    assert "literals: 1 = 3 at col 16, 2 = 1 at col 20" in capsys.readouterr().err
    assert main(["gate", "--repo", str(repo), "--self-test", "--mutate", "pkg/mod.py:2",
                 "--log", str(repo.parent / "s.log")]) == 2


def test_scan_json_has_end_line_and_qualname(repo):
    out = repo.parent / "scan.json"
    assert main(["scan", "--repo", str(repo), "--json", str(out)]) == 0
    fn = json.loads(out.read_text())["files"]["pkg/mod.py"]["functions"][0]
    assert fn["qualname"] == "pkg/mod.py::calc" and fn["line"] == 5 and fn["end_line"] == 7


# -- debt, require-gain, log -----------------------------------------------------------------------

def params(n):
    return "def f(" + ", ".join(f"a{i}" for i in range(n)) + "):\n    return 0\n"


@pytest.fixture
def drepo(tmp_path):
    cfg = '''schema = 1
[scope]
include = ["pkg/*"]
[gate]
[[gate.tier]]
name = "ok"
cmd = "true"
'''
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, "pkg/p.py": params(9)})
    return tmp_path


def gjson(repo, *extra, name="g.json"):
    out = repo.parent / name
    rc = gate(repo, "--json", str(out), *extra)
    return rc, json.loads(out.read_text())


def log(repo, gj, *extra, campaign="c1"):
    return main(["log", "--repo", str(repo), "--gate-json", str(repo.parent / gj), "--campaign", campaign,
                 "--target", "pkg/p.py::f", "--recipe", "r1", *extra])


def test_debt_meta_head_tree_and_require_gain(drepo, capsys):
    edit(drepo, "pkg/p.py", params(8))
    rc, d = gjson(drepo, "--require-gain")
    assert rc == 0 and (d["meta"]["debt_before"], d["meta"]["debt_after"]) == (4.0, 3.0)
    assert d["meta"]["require_gain"] is True and d["meta"]["trial"] is False
    assert "debt=4.0->3.0" in capsys.readouterr().out
    assert [c["name"] for c in d["checks"]].count("gain") == 1
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "step")
    import subprocess
    tree = subprocess.run(["git", "-C", str(drepo), "rev-parse", "HEAD^{tree}"], capture_output=True,
                          text=True).stdout.strip()
    assert tree == d["meta"]["head_tree"]
    git(drepo, "reset", "-q", "--hard", "HEAD~1")
    edit(drepo, "pkg/p.py", params(9) + "# just a comment\n")
    rc, d = gjson(drepo, "--require-gain")
    assert rc == 1
    assert [c["detail"] for c in d["checks"] if c["name"] == "gain" and not c["ok"]] == [
        "no debt reduction: 4.0 -> 4.0"]
    assert gate(drepo) == 0                        # without the flag a comment-only step passes


def test_log_accepted_flow(drepo, capsys):
    edit(drepo, "pkg/p.py", params(8))
    rc, d = gjson(drepo)
    assert rc == 0
    assert log(drepo, "g.json", "--result", "accepted") == 2      # not committed yet
    assert "commit the gated change" in capsys.readouterr().err
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "step")
    assert log(drepo, "g.json", "--model", "m", "--hypothesis", "h") == 0
    assert "LOG c1 step 1 accepted pkg/p.py::f debt 4.0 -> 3.0" in capsys.readouterr().out
    out = drepo / ".git/codehealth/runs/c1.jsonl"
    rec = json.loads(out.read_text())
    assert list(rec)[:9] == ["campaign", "step", "class", "target", "recipe", "result", "failed_tier",
                             "reason", "debt_before"]
    assert rec["result"] == "accepted" and rec["head_sha"] == sha(drepo) and rec["failed_tier"] is None
    assert rec["checks"]["gain"] if "gain" in rec["checks"] else rec["checks"]["debt"]
    assert rec["details"]["debt"] == "4.0 -> 3.0" and rec["model"] == "m"
    assert log(drepo, "g.json") == 2                                # duplicate
    assert "duplicate" in capsys.readouterr().err
    assert len(out.read_text().splitlines()) == 1


def test_log_gate_fail_inferred_and_no_gain(drepo, capsys):
    edit(drepo, "pkg/p.py", params(10))
    rc, _ = gjson(drepo)
    assert rc == 1
    assert log(drepo, "g.json") == 0
    rec = json.loads((drepo / ".git/codehealth/runs/c1.jsonl").read_text())
    assert rec["result"] == "gate-fail" and rec["failed_tier"] == "debt" and rec["head_sha"] is None
    assert log(drepo, "g.json", "--result", "no-gain", campaign="c2") == 2
    edit(drepo, "pkg/p.py", params(9) + "# c\n")
    gjson(drepo, name="g2.json")
    assert log(drepo, "g2.json", "--result", "accepted") == 2       # no gain
    assert log(drepo, "g2.json") == 0
    assert "no-gain" in capsys.readouterr().out
    assert log(drepo, "g2.json", "--result", "review-reject", campaign="c3") == 2   # needs reason
    assert log(drepo, "g2.json", "--result", "review-reject", "--reason", "x", campaign="c3") == 0


def test_log_refuses_self_test_and_trial_json(repo, tmp_path):
    rc, _ = self_test(repo, repo)
    assert log(repo, "st.json") == 2
    doc_edit(repo)
    trial = repo.parent / "trial.toml"
    trial.write_text(CONFIG)
    assert gate(repo, "--config", str(trial), "--json", str(repo.parent / "t.json")) == 3
    assert log(repo, "t.json") == 2


def test_log_optimize_accepted_without_debt_drop(drepo):
    edit(drepo, "pkg/p.py", params(9) + "# c\n")
    rc, d = gjson(drepo)
    d["meta"]["mode"] = "optimize"
    (drepo.parent / "o.json").write_text(json.dumps(d))
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "step")
    assert log(drepo, "o.json", "--result", "accepted") == 0
    rec = json.loads((drepo / ".git/codehealth/runs/c1.jsonl").read_text())
    assert rec["class"] == "optimize" and rec["result"] == "accepted"


def test_log_accepted_neutral(drepo, capsys):
    edit(drepo, "pkg/p.py", params(9) + "# names and docstrings\n")
    rc, d = gjson(drepo)                                   # no --require-gain: passes without gain
    assert rc == 0
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "review note")
    assert log(drepo, "g.json", "--result", "accepted") == 2          # no gain
    assert "accepted-neutral" in capsys.readouterr().err
    assert log(drepo, "g.json", "--result", "accepted-neutral") == 2  # needs reason
    assert log(drepo, "g.json", "--result", "accepted-neutral", "--reason", "review 2 names") == 0
    rec = json.loads((drepo / ".git/codehealth/runs/c1.jsonl").read_text())
    assert rec["result"] == "accepted-neutral" and rec["head_sha"] == sha(drepo)
    assert rec["reason"] == "review 2 names"


def test_log_accepted_neutral_refused_with_gain_check_or_optimize(drepo, capsys):
    edit(drepo, "pkg/p.py", params(9) + "# c\n")
    rc, d = gjson(drepo, "--require-gain")
    assert rc == 1                                         # gain check failed
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "step")
    for c in d["checks"]:
        c["ok"] = True                                     # even an all-ok JSON with a gain check
    (drepo.parent / "g2.json").write_text(json.dumps(d))
    assert log(drepo, "g2.json", "--result", "accepted-neutral", "--reason", "x") == 2
    assert "without --require-gain" in capsys.readouterr().err
    rc, d = gjson(drepo, name="g3.json")
    d["meta"]["mode"] = "optimize"
    (drepo.parent / "g3.json").write_text(json.dumps(d))
    assert log(drepo, "g3.json", "--result", "accepted-neutral", "--reason", "x") == 2


def test_log_head_sha_after_later_commit(drepo, capsys):
    edit(drepo, "pkg/p.py", params(8))
    rc, d = gjson(drepo)
    assert rc == 0
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "step")
    step = sha(drepo)
    (drepo / "NOTES.md").write_text("docs after the gate\n")
    git(drepo, "add", "NOTES.md")
    git(drepo, "commit", "-qm", "docs")
    assert log(drepo, "g.json") == 2                       # inferred accepted: HEAD differs
    err = capsys.readouterr().err
    assert "--head SHA" in err
    assert log(drepo, "g.json", "--head", sha(drepo)) == 2           # wrong tree
    assert log(drepo, "g.json", "--head", step) == 0
    rec = json.loads((drepo / ".git/codehealth/runs/c1.jsonl").read_text())
    assert rec["result"] == "accepted" and rec["head_sha"] == step


def test_log_head_must_be_ancestor(drepo, capsys):
    edit(drepo, "pkg/p.py", params(8))
    rc, d = gjson(drepo)
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "step")
    step = sha(drepo)
    git(drepo, "reset", "-q", "--hard", "HEAD~1")          # step dropped: no longer on the branch
    assert log(drepo, "g.json", "--head", step) == 2
    assert "not an ancestor" in capsys.readouterr().err


def test_log_head_refused_for_gate_fail(drepo, capsys):
    edit(drepo, "pkg/p.py", params(10))
    rc, _ = gjson(drepo)
    assert rc == 1
    assert log(drepo, "g.json", "--head", "HEAD") == 2
    assert "--head is only for" in capsys.readouterr().err
