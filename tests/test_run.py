"""Tier processes run in their own process group and die as a group (timeouts, stragglers)."""
import os
import time

from cce_code_health import gate


def _alive(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.05)
    return True


def _grandchild_pid(path) -> int:
    for _ in range(100):
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.02)
    raise AssertionError("grandchild did not start")


def test_timeout_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "pid"
    # The shell forks a busy grandchild and waits on it: like pytest with xdist workers.
    cmd = f"sh -c 'sleep 300 & echo $! > {pidfile}; wait'"
    rc, secs = gate._run(cmd, tmp_path, {}, open(tmp_path / "log", "w"), timeout=1)
    assert rc == 124 and secs < 10
    assert not _alive(_grandchild_pid(pidfile))


def test_sigterm_ignoring_grandchild_gets_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_KILL_GRACE", 0.5)
    pidfile = tmp_path / "pid"
    cmd = f"sh -c 'trap \"\" TERM; sleep 300 & echo $! > {pidfile}; wait'"
    rc, _ = gate._run(cmd, tmp_path, {}, open(tmp_path / "log", "w"), timeout=1)
    assert rc == 124
    assert not _alive(_grandchild_pid(pidfile))


def test_finished_tier_leaves_no_stragglers(tmp_path):
    pidfile = tmp_path / "pid"
    cmd = f"sleep 300 & echo $! > {pidfile}"
    rc, secs = gate._run(cmd, tmp_path, {}, open(tmp_path / "log", "w"), timeout=None)
    assert rc == 0 and secs < 10
    assert not _alive(_grandchild_pid(pidfile))
