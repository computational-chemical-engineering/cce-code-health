from test_git import commit, git

from cce_code_health import config
from cce_code_health.cli import main
from cce_code_health.docs_stale import cochange_review, stale_docs

CFG = '''schema = 1
[scope]
include = ["src/*"]
[docs]
include = ["docs/**/*.md"]
records = ["docs/adr/**"]
'''


def make(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": CFG, "src/a.py": "A = 1\n", "src/b.py": "B = 1\n",
                      "docs/guide.md": "See `src/a.py`.\n", "docs/adr/0001.md": "Uses `src/a.py`.\n"})
    for i in range(3):                                     # guide co-changes with b
        commit(tmp_path, {"src/b.py": f"B = {i + 2}\n", "docs/guide.md": f"See `src/a.py`. v{i}\n"})
    commit(tmp_path, {"src/a.py": "A = 2\n"})
    commit(tmp_path, {"src/a.py": "A = 3\n", "src/b.py": "B = 9\n"})
    return tmp_path


def test_stale_docs_referenced_and_cochange(tmp_path):
    repo = make(tmp_path)
    rows = stale_docs(repo, config.load(repo, "HEAD"))
    assert [r["doc"] for r in rows] == ["docs/guide.md"]                # the ADR is a record
    r = rows[0]
    assert r["referenced"] == {"src/a.py": 2} and r["doc_commits_ago"] == 2
    assert r["cochange"] == {"src/b.py": {"commits_since": 1, "shared": 4, "degree": 0.89}}


def test_cochange_review_and_cli(tmp_path, capsys):
    repo = make(tmp_path)
    cfg = config.load(repo, "HEAD")
    assert cochange_review(repo, cfg, {"src/b.py"}, ["docs/guide.md", "docs/adr/0001.md"]) == [
        ("docs/guide.md", "src/b.py", 4, 0.89)]
    assert cochange_review(repo, cfg, {"src/b.py", "docs/guide.md"}, ["docs/guide.md"]) == []
    assert main(["docs", "--stale", "--repo", str(repo)]) == 0
    assert "DOCS-STALE" in capsys.readouterr().out
    (repo / "src/b.py").write_text("B = 10\n")
    main(["diff", "--repo", str(repo)])
    assert "review docs/guide.md (changes with src/b.py in 4 commits, degree 0.89" in capsys.readouterr().out
