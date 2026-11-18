"""Benchmark timing: exact process wait (no 50 ms polling grid) and order-balanced pairs."""
import io
import subprocess

from cce_code_health import gate


def test_run_waits_without_poll_timeout(monkeypatch, tmp_path):
    # Popen.wait(timeout=...) polls with sleeps up to 50 ms, which put bench medians on a 50 ms grid
    calls = []
    real = subprocess.Popen.wait

    def spy(self, timeout=None):
        calls.append(timeout)
        return real(self, timeout)
    monkeypatch.setattr(subprocess.Popen, "wait", spy)
    with open(tmp_path / "log", "w") as log:
        rc, sec = gate._run("sleep 0.07", tmp_path, {}, log, timeout=30)
    assert rc == 0 and calls and all(c is None for c in calls)
    assert sec < 0.1      # was ~0.113 or more on the polling grid


def test_run_timeout_still_kills_and_returns_124(tmp_path):
    with open(tmp_path / "log", "w") as log:
        rc, sec = gate._run("sleep 30", tmp_path, {}, log, timeout=0.3)
    assert rc == 124 and sec < 10 and "TIMEOUT after 0.3 s" in (tmp_path / "log").read_text()


def test_pair_order_is_balanced(monkeypatch, tmp_path):
    # the second run of every pair is 1 % slower: balanced orders give a median ratio of 1
    order = []

    def fake_run(t, cwd, base, files, env, log):
        order.append(cwd)
        second = len(order) % 2 == 0
        return 0, 2.0 * (1.01 if second else 1.0), 1.0, ""
    monkeypatch.setattr(gate, "_bench_run", fake_run)
    monkeypatch.setattr(gate, "_warm_up", lambda t, sides, args: ("", ""))
    ctx = gate.Ctx(repo=tmp_path / "head", base="b", ref_base="b", files="", env={}, log=io.StringIO(),
                   mode="refactor", cfg=None, changes=[])
    ok, detail = gate._benchmark({"name": "bench", "cmd": "true", "repeat": 5}, ctx, lambda: tmp_path / "base")
    firsts = order[0::2]
    assert firsts.count(tmp_path / "base") == firsts.count(tmp_path / "head") == 3
    assert ok and "speedup 1.000" in detail and "n=6" in detail
