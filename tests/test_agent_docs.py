from test_git import commit, git

from cce_code_health.agent_docs import compare, duplicates, report
from cce_code_health.cli import main

PARA = " ".join(f"word{i}" for i in range(45))
D = {"include": ["**/*.md"], "agent_words": 50}


def test_budget_ratchet_and_duplicates():
    small, big = "x " * 30, "x " * 60
    assert compare({"CLAUDE.md": small}, {"CLAUDE.md": big}, {}, D)[0] == [
        "CLAUDE.md: grew 30 -> 60 words, over the agent-doc budget of 50 words (60)"]
    assert compare({"CLAUDE.md": big + "x " * 10}, {"CLAUDE.md": big}, {}, D)[0] == []      # over, but shrank
    assert compare({}, {"a/CLAUDE.md": big}, {}, D)[0][0].startswith("a/CLAUDE.md: new agent doc")
    assert compare({"old.md": big}, {"new.md": big}, {"new.md": "old.md"}, D)[0] == []      # rename, same size
    head = {"CLAUDE.md": PARA + "\n\nrest", ".claude/agents/x.md": "intro\n\n" + PARA.upper()}
    assert list(duplicates(head).values()) == [[".claude/agents/x.md", "CLAUDE.md"]]
    dup = compare({"CLAUDE.md": PARA}, head, {}, D)[1]
    assert len(dup) == 1 and "45-word paragraph" in dup[0]
    assert compare(head, head, {}, D)[1] == []                                              # existing: not new
    rep = report(head, D)
    assert rep["total_words"] == 92 and rep["docs"][0]["path"] in head


def test_docs_agent_command_and_diff(tmp_path, capsys):
    cfg = 'schema = 1\n[scope]\ninclude = ["src/*"]\n[docs]\ninclude = ["**/*.md"]\nagent_words = 50\nagent_budget = "fail"\n'
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": cfg, "CLAUDE.md": "short\n", "src/a.py": "A = 1\n"})
    assert main(["docs", "--agent", "--repo", str(tmp_path)]) == 0
    assert "DOCS-AGENT" in capsys.readouterr().out
    (tmp_path / "CLAUDE.md").write_text("x " * 80)
    assert main(["diff", "--repo", str(tmp_path)]) == 1
    assert "CLAUDE.md: grew 1 -> 80 words, over the agent-doc budget of 50 words" in capsys.readouterr().out
