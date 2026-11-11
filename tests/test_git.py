import os
import subprocess
import textwrap

import pytest

from cce_code_health import gitio
from cce_code_health.cli import main

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, **ENV})


def commit(repo, files: dict, msg="c", remove=()):
    for p in remove:
        git(repo, "rm", "-q", p)
    for p, text in files.items():
        f = repo / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text))
        git(repo, "add", p)
    git(repo, "commit", "-qm", msg)


SIMPLE = """
def f(x):
    return x + 1
"""


def nested(depth, name="f"):
    lines = [f"def {name}(x):"]
    for d in range(depth):
        lines.append("    " * (d + 1) + f"if x > {d}:")
    lines.append("    " * (depth + 1) + "return x")
    lines.append("    return 0")
    return "\n".join(lines) + "\n"


def brain(name="big"):
    body = [f"def {name}(x):", "    y = 0"]
    for i in range(40):
        body += [f"    if x > {i}:", f"        if x % {i + 2} == 0:", f"            y += {i}"]
    body.append("    return y")
    return "\n".join(body) + "\n"


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {"pkg/a.py": SIMPLE, "pkg/b.py": nested(5, "g")}, "base")
    return tmp_path


def diff(repo, *extra):
    return main(["diff", "--repo", str(repo), *extra])


def test_worsening_fails_improving_passes(repo, capsys):
    git(repo, "checkout", "-qb", "worse")
    commit(repo, {"pkg/a.py": nested(6)})
    assert diff(repo, "--base", "main", "--head", "HEAD") == 1
    git(repo, "checkout", "-qb", "better", "main")
    commit(repo, {"pkg/b.py": nested(2, "g")})
    assert diff(repo, "--base", "main", "--head", "HEAD") == 0
    assert "debt" in capsys.readouterr().out


def test_rename_and_split_pass(repo):
    git(repo, "checkout", "-qb", "move")
    git(repo, "mv", "pkg/b.py", "pkg/c.py")
    git(repo, "commit", "-qm", "rename")
    assert diff(repo, "--base", "main", "--head", "HEAD") == 0
    commit(repo, {"pkg/a.py": SIMPLE + "\n" + brain()}, "add brain at base of split")
    git(repo, "checkout", "-qb", "split")
    commit(repo, {"pkg/a.py": SIMPLE, "pkg/d.py": brain()}, "move brain to new file")
    assert diff(repo, "--base", "move", "--head", "HEAD") == 0


def test_new_brain_function_fails(repo, capsys):
    git(repo, "checkout", "-qb", "brain")
    commit(repo, {"pkg/new.py": brain()})
    assert diff(repo, "--base", "main", "--head", "HEAD") == 1
    assert "new brain function" in capsys.readouterr().out


def test_head_cannot_loosen_its_own_config(repo):
    commit(repo, {".codehealth/config.toml": 'schema = 1\n[scope]\ninclude = ["pkg/*"]\n'})
    git(repo, "checkout", "-qb", "loosen")
    commit(repo, {".codehealth/config.toml": 'schema = 1\n[scope]\nexclude = ["pkg/a.py"]\n',
                  "pkg/a.py": nested(6)})
    assert diff(repo, "--base", "main", "--head", "HEAD") == 1


def test_worktree_mode_sees_uncommitted_and_untracked(repo):
    (repo / "pkg/a.py").write_text(nested(6))
    assert diff(repo) == 1
    git(repo, "checkout", "-q", "--", "pkg/a.py")
    assert diff(repo) == 0
    (repo / "pkg/u.py").write_text(brain())
    assert diff(repo) == 1


def test_bad_config_is_tool_error(repo):
    commit(repo, {".codehealth/config.toml": "schema = 99\n"})
    assert diff(repo, "--base", "HEAD", "--head", "HEAD") == 2


def test_commit_counts_follow_renames(repo):
    commit(repo, {"pkg/a.py": SIMPLE + "\n# edit\n"})
    git(repo, "mv", "pkg/a.py", "pkg/z.py")
    git(repo, "commit", "-qm", "rename")
    commit(repo, {"pkg/z.py": SIMPLE + "\n# edit 2\n"})
    counts = gitio.commit_counts(repo)
    assert counts["pkg/z.py"] == 4 and "pkg/a.py" not in counts
