"""Benchmark timing: exact process wait (no 50 ms polling grid) and order-balanced pairs."""
import io
import subprocess
from pathlib import Path

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

    def fake_run(t, cwd, base, files, env, log, cache=None):
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


def _record_caches(monkeypatch, tmp_path, tier):
    seen = []

    def fake_run(t, cwd, base, files, env, log, cache=None):
        seen.append((cwd.name, cache))
        if cache is not None:
            assert Path(cache).is_dir()
        return 0, 1.0, 1.0, ""
    monkeypatch.setattr(gate, "_bench_run", fake_run)
    monkeypatch.setattr(gate, "_ignored", lambda cwd: set())
    ctx = gate.Ctx(repo=tmp_path / "head", base="b", ref_base="b", files="", env={}, log=io.StringIO(),
                   mode="refactor", cfg=None, changes=[])
    ok, detail = gate._benchmark(tier, ctx, lambda: tmp_path / "base")
    return ok, detail, seen


def test_cold_cache_is_fresh_per_run(monkeypatch, tmp_path):
    ok, detail, seen = _record_caches(monkeypatch, tmp_path, {"name": "b", "cmd": "x"})
    assert ok and all(c is None for _, c in seen) and "warm cache" not in detail


def test_warm_cache_is_kept_per_side_and_removed(monkeypatch, tmp_path):
    ok, detail, seen = _record_caches(monkeypatch, tmp_path, {"name": "b", "cmd": "x", "cache": "warm"})
    by_side = {s: {c for side, c in seen if side == s} for s in ("base", "head")}
    assert ok and len(seen) == 2 + 12 and all(len(v) == 1 for v in by_side.values())
    assert by_side["base"] != by_side["head"] and "warm cache" in detail
    assert not any(Path(c).exists() for v in by_side.values() for c in v)


def test_warm_cache_reaches_the_command(monkeypatch, tmp_path):
    # the warm-up writes a marker into NUMBA_CACHE_DIR; with cache = "warm" every timed run sees it
    monkeypatch.setattr(gate, "_ignored", lambda cwd: set())
    monkeypatch.setattr(gate, "_status", lambda cwd: "")
    (tmp_path / "base").mkdir()
    (tmp_path / "head").mkdir()
    with open(tmp_path / "log", "w") as log:
        cmd = 'test -f "$NUMBA_CACHE_DIR/m" && echo warm >> "$TMPDIR/../hits" ; mkdir -p "$NUMBA_CACHE_DIR"; touch "$NUMBA_CACHE_DIR/m"'
        ctx = gate.Ctx(repo=tmp_path / "head", base="b", ref_base="b", files="", env={}, log=log,
                       mode="refactor", cfg=None, changes=[])
        for cache, want in (("cold", 0), ("warm", 12)):
            cmd_logged = cmd.replace('"$TMPDIR/../hits"', str(tmp_path / f"hits-{cache}"))
            ok, _ = gate._benchmark({"name": "b", "cmd": cmd_logged, "cache": cache, "repeat": 6}, ctx,
                                    lambda: tmp_path / "base")
            hits = tmp_path / f"hits-{cache}"
            assert (len(hits.read_text().split()) if hits.exists() else 0) == want

