import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_git import SIMPLE, commit, git, nested

from cce_code_health import config
from cce_code_health.cli import main

CONFIG = "schema = 1\n"
SCRIPT = Path(__file__).resolve().parents[1] / "plugins/cce-code-health/hooks/post_edit.sh"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEHEALTH_HOOK", raising=False)
    git(tmp_path, "init", "-q")
    commit(tmp_path, {".codehealth/config.toml": CONFIG, "a.py": SIMPLE, "notes.md": "x\n"})
    return tmp_path


def payload(repo, rel, tool="Edit"):
    return json.dumps({"tool_name": tool, "tool_input": {"file_path": str(repo / rel)},
                       "cwd": str(repo)})


def run_hook(monkeypatch, capsys, stdin, *args):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main(["hook", *args])
    return code, capsys.readouterr()


def write(repo, rel, text):
    (repo / rel).write_text(text)


def test_silent_without_config(tmp_path, monkeypatch, capsys):
    git(tmp_path, "init", "-q")
    commit(tmp_path, {"a.py": SIMPLE})
    write(tmp_path, "a.py", nested(5))
    code, out = run_hook(monkeypatch, capsys, payload(tmp_path, "a.py"))
    assert (code, out.err, out.out) == (0, "", "")


def test_silent_when_unchanged_or_lower(repo, monkeypatch, capsys):
    commit(repo, {"a.py": nested(5)}, "deep")
    assert run_hook(monkeypatch, capsys, payload(repo, "a.py"))[0] == 0
    write(repo, "a.py", nested(2))
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert (code, out.err) == (0, "")


def test_nesting_rise_reports(repo, monkeypatch, capsys):
    write(repo, "a.py", nested(5, "f"))
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert code == 2 and out.out == ""
    lines = out.err.strip().splitlines()
    assert len(lines) <= 15
    assert lines[0].startswith("codehealth: a.py: debt 0.0 -> ")
    assert any(ln.startswith("  f (line 1): ") and "nesting 5 > 3" in ln for ln in lines)
    assert "playbook.md" in lines[-1]


def test_new_function_with_debt(repo, monkeypatch, capsys):
    write(repo, "a.py", SIMPLE + "\n" + nested(4, "g"))
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py", "Write"))
    assert code == 2 and "  g (line 5)" in out.err and "f (line" not in out.err


def test_new_file_absent_at_base(repo, monkeypatch, capsys):
    write(repo, "new.py", nested(4, "g"))
    code, out = run_hook(monkeypatch, capsys, payload(repo, "new.py"))
    assert code == 2 and "debt 0.0 ->" in out.err


def test_disabled_by_env(repo, monkeypatch, capsys):
    write(repo, "a.py", nested(5))
    monkeypatch.setenv("CODEHEALTH_HOOK", "0")
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert (code, out.err) == (0, "")


def test_disabled_by_config(repo, monkeypatch, capsys):
    write(repo, ".codehealth/config.toml", CONFIG + "[hook]\nenabled = false\n")
    write(repo, "a.py", nested(5))
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert (code, out.err) == (0, "")


def test_out_of_scope_and_non_python(repo, monkeypatch, capsys):
    write(repo, ".codehealth/config.toml", 'schema = 1\n[scope]\ninclude = ["src/*"]\n')
    write(repo, "a.py", nested(5))
    assert run_hook(monkeypatch, capsys, payload(repo, "a.py"))[0] == 0
    write(repo, "notes.md", nested(5))
    assert run_hook(monkeypatch, capsys, payload(repo, "notes.md"))[0] == 0


def test_bad_input_is_silent(repo, monkeypatch, capsys):
    for text in ("not json", "{}", json.dumps({"tool_input": {}}), "[]"):
        code, out = run_hook(monkeypatch, capsys, text)
        assert (code, out.err) == (0, "")


def test_unparsable_python_is_silent(repo, monkeypatch, capsys):
    write(repo, "a.py", "def f(:\n  pass\n")
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert (code, out.err) == (0, "")


def test_exception_is_swallowed(repo, monkeypatch, capsys):
    write(repo, "a.py", nested(5))
    monkeypatch.setattr(config, "load", lambda *a: 1 / 0)
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert (code, out.err) == (0, "")


def test_invalid_suppression_and_base_option(repo, monkeypatch, capsys):
    write(repo, "a.py", SIMPLE + "# codehealth: allow complex\n")
    code, out = run_hook(monkeypatch, capsys, payload(repo, "a.py"))
    assert code == 2 and "invalid suppression" in out.err
    commit(repo, {"a.py": nested(5)}, "deep")
    assert run_hook(monkeypatch, capsys, payload(repo, "a.py"), "--base", "HEAD")[0] == 0
    assert run_hook(monkeypatch, capsys, payload(repo, "a.py"), "--base", "HEAD~1")[0] == 2


def test_config_check_hook_table(tmp_path, capsys):
    ok = tmp_path / "ok.toml"
    ok.write_text("schema = 1\n[hook]\nenabled = false\n")
    assert main(["config", "--check", "--config", str(ok)]) == 0
    for body in ("[hook]\nenabled = 1\n", "[hook]\nloud = true\n"):
        bad = tmp_path / "bad.toml"
        bad.write_text("schema = 1\n" + body)
        assert main(["config", "--check", "--config", str(bad)]) == 1
    capsys.readouterr()


def test_speed_2000_lines(repo, monkeypatch, capsys):
    body = "".join(f"def fn{i}(x):\n    y = x + {i}\n    return y\n\n" for i in range(500))
    commit(repo, {"big.py": body}, "big")
    assert len(body.splitlines()) >= 2000
    write(repo, "big.py", body + "\ndef extra(x):\n    return x\n")
    t = time.perf_counter()
    code, out = run_hook(monkeypatch, capsys, payload(repo, "big.py"))
    assert code == 0 and time.perf_counter() - t < 3


def run_script(repo, rel, env_extra):
    env = {**os.environ, **env_extra}
    env.pop("CODEHEALTH_HOOK", None)
    return subprocess.run(["sh", str(SCRIPT)], input=payload(repo, rel), text=True,
                          capture_output=True, env=env)


@pytest.mark.skipif(shutil.which("codehealth") is None and not (Path(sys.executable).parent / "codehealth").exists(),
                    reason="codehealth binary not installed")
def test_post_edit_script(repo):
    binary = Path(sys.executable).parent / "codehealth"
    env = {"CODEHEALTH_BIN": str(binary)}
    r = run_script(repo, "a.py", env)
    assert (r.returncode, r.stderr) == (0, "")
    write(repo, "a.py", nested(5))
    r = run_script(repo, "a.py", env)
    assert r.returncode == 2 and "codehealth: a.py: debt" in r.stderr
    r = run_script(repo, "notes.md", env)
    assert r.returncode == 0
    r = subprocess.run(["sh", str(SCRIPT)], input="garbage", text=True, capture_output=True)
    assert (r.returncode, r.stderr) == (0, "")


def fake_bin(tmp_path, stderr, rc):
    b = tmp_path / "fakebin"
    b.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' {stderr!r} >&2\nexit {rc}\n")
    b.chmod(0o755)
    return {"CODEHEALTH_BIN": str(b)}


@pytest.mark.parametrize("stderr", [
    "usage: codehealth [-h] {scan,diff}\ncodehealth: error: argument command: invalid choice: 'hook'",
    "codehealth: error: argument command: invalid choice: 'hook'",
    "Traceback (most recent call last):"])
def test_post_edit_script_ignores_foreign_exit_2(repo, tmp_path_factory, stderr):
    env = fake_bin(tmp_path_factory.mktemp("bin"), stderr, 2)
    r = run_script(repo, "a.py", env)
    assert (r.returncode, r.stderr) == (0, "")


def test_post_edit_script_relays_report_only_with_config_and_py(repo, tmp_path_factory):
    env = fake_bin(tmp_path_factory.mktemp("bin"), "codehealth: a.py: debt 0.0 -> 3.0", 2)
    r = run_script(repo, "a.py", env)
    assert r.returncode == 2 and "codehealth: a.py: debt" in r.stderr
    assert run_script(repo, "notes.md", env).returncode == 0
    assert run_script(repo, "a.py", fake_bin(tmp_path_factory.mktemp("bin"), "codehealth: x", 1)).returncode == 0
    (repo / ".codehealth/config.toml").unlink()
    assert run_script(repo, "a.py", env).returncode == 0
