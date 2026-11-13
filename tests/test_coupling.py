import json

from cce_code_health.cli import main
from cce_code_health.coupling import module_of

from test_git import commit, git


def init(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def run(repo, capsys, *extra):
    out = repo / "o.json"
    assert main(["coupling", "--repo", str(repo), "--json", str(out), *extra]) == 0
    capsys.readouterr()
    return json.loads(out.read_text())


def history(repo, n, extra=0):
    for i in range(n):
        more = {f"c/f{j}.py": f"{i}\n" for j in range(extra)}
        commit(repo, {"a/x.py": f"v={i}\n", "b/y.py": f"v={i}\n", **more}, f"c{i}")


def test_degree_and_cross_module(tmp_path, capsys):
    repo = init(tmp_path)
    history(repo, 5)
    commit(repo, {"a/x.py": "v=99\n"}, "solo")
    d = run(repo, capsys)
    [p] = d["pairs"]
    assert (p["a"], p["b"], p["shared"], p["revs_a"], p["revs_b"]) == ("a/x.py", "b/y.py", 5, 6, 5)
    assert p["degree"] == round(5 / 5.5, 3) and p["cross_module"] is True
    assert d["module_pairs"] == [{"a": "a", "b": "b", "shared": 5}]
    assert d["meta"]["commits"] == 6


def test_same_module_not_marked(tmp_path, capsys):
    repo = init(tmp_path)
    for i in range(5):
        commit(repo, {"a/x.py": f"v={i}\n", "a/y.py": f"v={i}\n"})
    d = run(repo, capsys)
    assert d["pairs"][0]["cross_module"] is False and d["module_pairs"] == []


def test_thresholds(tmp_path, capsys):
    repo = init(tmp_path)
    history(repo, 4)
    assert run(repo, capsys)["pairs"] == []                       # shared 4 < 5
    assert len(run(repo, capsys, "--min-shared", "4")["pairs"]) == 1
    for i in range(8):                                            # degree 4 / 8 = 0.5
        commit(repo, {"a/x.py": f"z={i}\n"}, f"s{i}")
    assert len(run(repo, capsys, "--min-shared", "4")["pairs"]) == 1
    assert run(repo, capsys, "--min-shared", "4", "--min-degree", "0.6")["pairs"] == []


def test_rename_followed(tmp_path, capsys):
    repo = init(tmp_path)
    history(repo, 3)
    git(repo, "mv", "b/y.py", "b/z.py")
    git(repo, "commit", "-qm", "rename")
    for i in range(3):
        commit(repo, {"a/x.py": f"w={i}\n", "b/z.py": f"w={i}\n"})
    d = run(repo, capsys, "--min-shared", "5")
    [p] = d["pairs"]
    assert p["b"] == "b/z.py" and p["shared"] == 6


def test_big_commit_skipped(tmp_path, capsys):
    repo = init(tmp_path)
    history(repo, 5, extra=40)
    d = run(repo, capsys)
    assert d["pairs"] == [] and d["meta"]["skipped_commits"] == 5
    d = run(repo, capsys, "--max-files-per-commit", "100", "--min-shared", "5")
    assert len(d["pairs"]) > 0 and d["meta"]["skipped_commits"] == 0


def test_depth_and_module_of():
    assert module_of("src/pkg/sub/m.py", 2) == "src/pkg"
    assert module_of("src/pkg/sub/m.py", None) == "src/pkg/sub"
    assert module_of("m.py", None) == "."


def test_depth_merges_modules(tmp_path, capsys):
    repo = init(tmp_path)
    for i in range(5):
        commit(repo, {"s/a/x.py": f"v={i}\n", "s/b/y.py": f"v={i}\n"})
    assert run(repo, capsys)["pairs"][0]["cross_module"] is True
    assert run(repo, capsys, "--depth", "1")["pairs"][0]["cross_module"] is False


def test_test_pairs_dropped_by_default():
    from cce_code_health.coupling import compute, is_test
    assert is_test("tests/test_x.py") and is_test("src/x_test.cpp") and not is_test("src/x.py")
    commits = [["src/a.py", "src/b.py", "tests/test_a.py"]] * 6
    pairs = {(p["a"], p["b"]) for p in compute(commits, None, 5, 0.5)["pairs"]}
    assert pairs == {("src/a.py", "src/b.py")}
    pairs = {(p["a"], p["b"]) for p in compute(commits, None, 5, 0.5, include_test_pairs=True)["pairs"]}
    assert ("src/a.py", "tests/test_a.py") in pairs
