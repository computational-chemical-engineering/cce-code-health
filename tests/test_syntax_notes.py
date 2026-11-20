"""SyntaxWarnings (invalid escapes) are noted once per file and line, naming the file."""
from test_git import commit, git
from cce_code_health.cli import main

BAD = 'def f():\n    return "a\\\\p"\n\n\ndef g(x):\n    s = "\\p"\n    return s, x\n'
CFG = 'schema = 1\nprofile = "python@1"\n[scope]\ninclude = ["src/*"]\n'


def _repo(tmp_path):
    git(tmp_path, "init", "-q")
    commit(tmp_path, {".codehealth/config.toml": CFG, "src/bad.py": BAD, "src/ok.py": "x = 1\n"})
    return tmp_path


def test_scan_notes_bad_escape_once_with_path(tmp_path, capsys):
    repo = _repo(tmp_path)
    assert main(["scan", "--repo", str(repo)]) == 0
    err = capsys.readouterr().err
    assert "<unknown>" not in err and "SyntaxWarning:" not in err
    notes = [ln for ln in err.splitlines() if "codehealth: note:" in ln]
    assert len(notes) == 1 and "src/bad.py:6:" in notes[0] and "\\p" in notes[0]


def test_diff_notes_unchanged_bad_file_once(tmp_path, capsys):
    repo = _repo(tmp_path)
    (repo / "src/bad.py").write_text(BAD + "\n\ndef h():\n    return 1\n")
    main(["diff", "--repo", str(repo)])
    notes = [ln for ln in capsys.readouterr().err.splitlines() if "codehealth: note:" in ln]
    assert len(notes) == 1 and "src/bad.py:6:" in notes[0]
