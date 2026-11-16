from cce_code_health.duplication import code_lines, duplicated_lines

NAMES = "\n".join(f"    name_{i}," for i in range(12))


def test_parenthesised_import_lists_are_not_duplication():
    a = f"from pkg.spec import (\n{NAMES}\n)\n\ndef f():\n    return 1\n"
    b = f"from pkg.spec import (  # shared names\n{NAMES}\n)\n\ndef g():\n    return 2\n"
    assert duplicated_lines({"a.py": a, "b.py": b}, 8) == {}
    assert code_lines(a) == ["def f():", "return 1"]


def test_backslash_and_relative_imports_are_left_out():
    t = "from . import a, \\\n    b, \\\n    c\nimport os.path\nfrom ..x import (y)\nz = 1\n"
    assert code_lines(t) == ["z = 1"]


def test_code_after_an_import_list_still_counts():
    body = "\n".join(f"    v{i} = compute({i})" for i in range(10))
    a = f"from m import (\n    p,\n    q)\ndef f():\n{body}\n"
    b = f"def g():\n{body}\n"
    dup = duplicated_lines({"a.py": a, "b.py": b}, 8)
    assert dup == {"a.py": 10, "b.py": 10}


def test_prose_starting_with_from_is_not_an_import():
    t = '"""\nfrom the reference (see eq. 3\nthe rest\n"""\nx = 1\n'
    assert "the rest" in code_lines(t) and "x = 1" in code_lines(t)


def test_all_declarations_are_left_out():
    names = "\n".join(f'    "name_{i}",' for i in range(12))
    a = f"from .seg import (\n{NAMES}\n)\n\n__all__ = [\n{names}\n]\n"
    b = f"__all__ = [\n{names}\n]\n__all__ += ['x']\n__all__: list[str] = ('y',\n    'z')\n\ndef f():\n    return 1\n"
    assert duplicated_lines({"a.py": a, "b.py": b}, 8) == {}
    assert code_lines(b) == ["def f():", "return 1"]
