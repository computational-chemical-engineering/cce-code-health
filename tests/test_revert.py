"""Self-test --ref-base, gate meta head, and gate --revert."""
import json
import subprocess

from test_adopter import CONFIG, PY, cmd_repo, drepo, gjson, log, params, run, sha  # noqa: F401
from test_gate import MOD, doc_edit, edit, gate, repo, set_config  # noqa: F401
from test_git import commit, git

from cce_code_health import gitio
from cce_code_health.cli import main


def runs(repo):
    return [json.loads(x) for x in (repo / ".git/codehealth/runs/c1.jsonl").read_text().splitlines()]


def step(repo, n, msg="step"):
    edit(repo, "pkg/p.py", params(n))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return sha(repo)


def test_self_test_honours_ref_base(tmp_path):
    (tmp_path / "r").mkdir()
    repo = cmd_repo(tmp_path / "r")
    out = tmp_path / "refs.txt"
    set_config(repo, (repo / ".codehealth/config.toml").read_text() + (
        "\n[[gate.tier]]\nname = \"refs\"\nbehaviour = false\ncmd = '''" + PY +
        f" -c \"import sys; open('{out}', 'a').write(sys.argv[1] + chr(10))\" {{ref_base}}'''\n"))
    sha0 = sha(repo)
    git(repo, "commit", "-q", "--allow-empty", "-m", "e")
    head = sha(repo)

    def st(*extra):
        j = tmp_path / "st.json"
        main(["gate", "--repo", str(repo), "--self-test", "--log", str(tmp_path / "s.log"),
              "--json", str(j), *extra])
        return json.loads(j.read_text())["meta"]["ref_base"]

    assert st() is None
    assert set(out.read_text().split()) == {head}
    out.unlink()
    assert st("--ref-base", sha0) == sha0
    assert set(out.read_text().split()) == {sha0}


def test_meta_head_sha_or_worktree(drepo, capsys):
    s0 = sha(drepo)
    s1 = step(drepo, 8)
    rc, d = gjson(drepo, "--base", s0)
    assert rc == 0 and d["meta"]["head"] == s1
    assert f"head={s1[:10]}" in capsys.readouterr().out
    edit(drepo, "pkg/p.py", params(7))
    rc, d = gjson(drepo, "--base", s0)
    assert d["meta"]["head"] == "worktree" and "head=worktree" in capsys.readouterr().out
    assert d["meta"]["revert"] == []
    assert log(drepo, "g.json", "--result", "review-reject", "--reason", "x") == 0
    assert runs(drepo)[0]["head_sha"] is None and runs(drepo)[0]["reverts"] == []


def test_log_failed_committed_step_keeps_head_sha(drepo):
    s0 = sha(drepo)
    s1 = step(drepo, 10)
    rc, _ = gjson(drepo, "--base", s0)
    assert rc == 1
    assert log(drepo, "g.json") == 0
    assert runs(drepo)[0]["head_sha"] == s1


def revert_flow(drepo, capsys):
    s0 = sha(drepo)
    s1 = step(drepo, 8)
    git(drepo, "revert", "--no-edit", s1)
    return s0, s1


def test_revert_passes_and_logs(drepo, capsys):
    s0, s1 = revert_flow(drepo, capsys)
    rc, d = gjson(drepo, "--base", s1, "--revert", s1)
    assert rc == 0 and d["meta"]["revert"] == [s1]
    chk = {c["name"]: c for c in d["checks"]}
    assert chk["debt"]["detail"] == "3.0 -> 4.0 (rise allowed: exact revert)" and chk["revert"]["ok"]
    assert "revert=1" in capsys.readouterr().out
    assert gate(drepo, "--base", s1) == 1                       # plain gate refuses the rise
    assert log(drepo, "g.json") == 2                            # reason required
    assert log(drepo, "g.json", "--reason", "bad", "--result", "accepted") == 2
    assert log(drepo, "g.json", "--reason", "bad") == 0
    rec = runs(drepo)[0]
    assert (rec["class"], rec["result"], rec["reverts"], rec["head_sha"]) == ("revert", "review-reject", [s1], sha(drepo))


def test_revert_log_needs_commit(drepo):
    s0, s1 = revert_flow(drepo, None)
    git(drepo, "reset", "-q", "--soft", "HEAD~1")
    rc, _ = gjson(drepo, "--base", s1, "--revert", s1)
    assert rc == 0
    assert log(drepo, "g.json", "--reason", "bad") == 2


def test_revert_with_extra_edit_fails(drepo, capsys):
    s0, s1 = revert_flow(drepo, capsys)
    edit(drepo, "pkg/p.py", params(9) + "# extra\n")
    git(drepo, "add", "-A")
    git(drepo, "commit", "-qm", "extra")
    rc, d = gjson(drepo, "--base", s1, "--revert", s1)
    c = next(c for c in d["checks"] if c["name"] == "revert")
    assert rc == 1 and not c["ok"] and "pkg/p.py" in c["detail"]
    assert log(drepo, "g.json", "--reason", "bad") == 0
    assert runs(drepo)[0]["result"] == "gate-fail" and runs(drepo)[0]["failed_tier"] == "revert"


def test_revert_non_ancestor_fails(drepo):
    s0 = sha(drepo)
    git(drepo, "checkout", "-q", "-b", "side")
    other = step(drepo, 8)
    git(drepo, "checkout", "-q", "main")
    git(drepo, "reset", "-q", "--hard", s0)
    s1 = step(drepo, 7)
    rc, d = gjson(drepo, "--base", s1, "--revert", other)
    c = next(c for c in d["checks"] if c["name"] == "revert")
    assert rc == 1 and "not an ancestor" in c["detail"]


def test_revert_with_require_gain_is_usage_error(drepo):
    s1 = step(drepo, 8)
    assert gate(drepo, "--base", s1, "--revert", s1, "--require-gain") == 2


def test_revert_two_steps_together(drepo):
    s0 = sha(drepo)
    s1 = step(drepo, 8)
    s2 = step(drepo, 7)
    git(drepo, "revert", "--no-edit", "--no-commit", s2, s1)
    git(drepo, "commit", "-qm", "revert both")
    rc, d = gjson(drepo, "--base", s2, "--revert", s1, "--revert", s2)
    assert rc == 0 and set(d["meta"]["revert"]) == {s1, s2}
    assert "(rise allowed" in next(c["detail"] for c in d["checks"] if c["name"] == "debt")
