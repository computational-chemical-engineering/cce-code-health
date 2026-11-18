"""report.new_brains: brains matched by name as a multiset over the touched files."""
from cce_code_health.report import new_brains


def rep(*fns):
    return {"functions": [{"name": n, "line": i + 1, "brain": True} for i, n in enumerate(fns)]}


def names(out):
    return [(p, f["name"]) for p, f in out]


def test_move_between_files_is_not_new():
    rb, rh = {"a.py": rep("big")}, {"a.py": rep(), "b.py": rep("big")}
    assert new_brains(rb, rh, [("M", "a.py", "a.py"), ("A", None, "b.py")]) == []


def test_second_brain_with_same_name_is_new_and_reported_where_added():
    rb = {"a.py": rep("run::lambda#1")}
    rh = {"a.py": rep("run::lambda#1"), "0.py": rep("run::lambda#1")}
    assert names(new_brains(rb, rh, [("M", "a.py", "a.py"), ("A", None, "0.py")])) == [("0.py", "run::lambda#1")]


def test_rename_counterpart_keeps_its_brain():
    rb = {"old.py": rep("big")}
    rh = {"new.py": rep("big"), "0.py": rep("big")}
    out = new_brains(rb, rh, [("R", "old.py", "new.py"), ("A", None, "0.py")])
    assert names(out) == [("0.py", "big")]


def test_overload_in_same_file_is_new():
    assert names(new_brains({"a.cpp": rep("f")}, {"a.cpp": rep("f", "f")}, [("M", "a.cpp", "a.cpp")])) == [("a.cpp", "f")]


def test_non_brains_are_ignored():
    rh = {"a.py": {"functions": [{"name": "g", "line": 1, "brain": False}]}}
    assert new_brains({}, rh, [("A", None, "a.py")]) == []


def test_added_file_at_renamed_files_old_path_is_blamed():
    rb = {"a.py": rep("X")}
    rh = {"core.py": rep("X"), "a.py": rep("X")}
    out = new_brains(rb, rh, [("R", "a.py", "core.py"), ("A", None, "a.py")])
    assert names(out) == [("a.py", "X")]
