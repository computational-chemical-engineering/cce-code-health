import json

import pytest
from test_git import commit, git

from cce_code_health import config, docs, gitio
from cce_code_health.cli import main

DOCS_CONFIG = """
schema = 1
[scope]
include = ["src/*"]
[docs]
include = ["README.md", "docs/**/*.md", "CLAUDE.md"]
exclude = ["docs/archive/**"]
records = ["docs/DECISIONS.md"]
python_roots = ["src"]
docstrings = true
"""

CORE = '''"""Core."""
import json
from pkg.util import helper as exported
from pkg.star_src import *


class Engine:
    """Engine."""

    rate = 1

    def __init__(self):
        self.state = 0

    def run(self):
        """Run."""
        return 1


def solve(x):
    """Solve."""
    return x
'''

UTIL = '''def helper():
    """Help."""
    return 1


def _private_helper():
    return 2
'''


@pytest.fixture
def drepo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {
        ".gitignore": "build/\n.venv/\n",
        ".codehealth/config.toml": DOCS_CONFIG,
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "src/pkg/util.py": UTIL,
        "src/pkg/star_src.py": "def starred():\n    \"\"\"S.\"\"\"\n",
        "src/pkg/dyn.py": "def __getattr__(name):\n    return 1\n",
        "tests/test_core.py": "def test_solve():\n    pass\n\nLIMIT = 3\n",
        "README.md": "Start with `src/pkg/core.py` and `pkg.core.solve`.\n",
        "docs/guide.md": "See [the readme](../README.md) and `core.py:20`.\n",
        "docs/DECISIONS.md": "2026: kept `src/pkg/util.py`.\n",
    })
    return tmp_path


def tree_at(repo, ref="HEAD", d=None):
    cfg = config.load(repo, ref)
    return docs.Tree(repo, gitio.rev(repo, ref) if ref else None, d or cfg.docs), cfg


def status(repo, text, doc="docs/x.md", ref="HEAD", link=False):
    tree, _ = tree_at(repo, ref)
    r = docs.classify(text, link, tree)
    if r is None:
        return None
    st = docs.resolve(r, doc, tree)
    return r.kind, st[0], st[1]


def test_candidates_skip_fences_and_find_links():
    text = "a `x.py` b\n```\n`y.py`\n```\n[t](docs/z.md) `p` <!-- `c.py` -->\n"
    got = [(c.line, c.kind, c.text) for c in docs.candidates("a.md", text)]
    assert got == [(1, "code", "x.py"), (5, "code", "p"), (5, "link", "docs/z.md")]
    nb = json.dumps({"cells": [{"cell_type": "code", "source": "`a.py`"},
                               {"cell_type": "markdown", "source": ["see `b.py`\n"]}]})
    assert [(c.where, c.text) for c in docs.candidates("n.ipynb", nb)] == [("cell 2", "b.py")]


def test_path_shapes_and_roots(drepo):
    assert status(drepo, "src/pkg/core.py") == ("path", "ok", "")
    assert status(drepo, "pkg/core.py") == ("path", "ok", "")               # suffix
    assert status(drepo, "core.py") == ("path", "ok", "")                    # bare name
    assert status(drepo, "util.py", doc="docs/x.md") == ("path", "ok", "")
    assert status(drepo, "src/pkg/gone.py") == ("path", "broken", "no such file")
    assert status(drepo, "src/pkg/") == ("path", "ok", "")
    assert status(drepo, "doc:src/pkg/core.py") == ("path", "ok", "")       # word: prefix
    assert status(drepo, "src/pkg/core.py#L3") == ("path", "ok", "")
    assert status(drepo, "1/(1+RR)") is None                                 # R1 charset
    assert status(drepo, "origin/main") == ("path", "skip", "not a repo directory")
    assert status(drepo, "wp/K03")[1] == "skip"                              # R2: no extension
    assert status(drepo, "build/out.json")[1] == "skip"
    assert status(drepo, "src/build/out.json") == ("path", "skip", "gitignored")
    assert status(drepo, "idaes/core/x.py") == ("path", "skip", "not a repo directory")
    assert status(drepo, "../README.md", doc="docs/x.md", link=True) == ("path", "ok", "")
    assert status(drepo, "https://example.org/a.md", link=True) is None


def test_anchor_and_node(drepo):
    assert status(drepo, "src/pkg/core.py:20") == ("anchor", "ok", "")
    assert status(drepo, "src/pkg/core.py:20-22,3") == ("anchor", "ok", "")
    kind, st, why = status(drepo, "src/pkg/core.py:999")
    assert (kind, st) == ("anchor", "broken") and why.startswith("line 999 beyond end")
    assert status(drepo, "tests/test_core.py::test_solve") == ("node", "ok", "")
    assert status(drepo, "tests/test_core.py::test_sol…") == ("node", "ok", "")
    assert status(drepo, "tests/test_core.py::LIMIT") == ("node", "ok", "")
    assert status(drepo, "tests/test_core.py::test_x")[1] == "broken"


def test_python_names(drepo):
    ok = ["pkg.core.solve", "pkg.core.solve()", "pkg.core.Engine.run", "pkg.core.Engine.state",
          "pkg.core.Engine.rate", "pkg.core.exported", "pkg.core.json.loads", "pkg.core.starred",
          "pkg.dyn.anything", "pkg.core.__file__", "pkg.util", "pkg.core:solve"]
    for name in ok:
        assert status(drepo, name) == ("python", "ok", ""), name
    assert status(drepo, "pkg.core.missing") == ("python", "broken", "name not found")
    assert status(drepo, "pkg.core.Engine.nope") == ("python", "broken", "name not found")
    assert status(drepo, "pkg.nomod.x") == ("python", "broken", "name not found")
    assert status(drepo, "os.path.join") is None                             # not a repo package
    tree, _ = tree_at(drepo)
    assert docs.classify("pkg.util._private_helper", False, tree).fragile


def diff(repo, *extra):
    return main(["diff", "--repo", str(repo), "--base", "HEAD~1", "--head", "HEAD",
                 "--json", str(repo.parent / "d.json"), *extra])


def diff_json(repo):
    return json.loads((repo.parent / "d.json").read_text())


def test_rot_fails_in_untouched_doc(drepo, capsys):
    git(drepo, "mv", "src/pkg/core.py", "src/pkg/engine.py")
    git(drepo, "commit", "-qm", "rename")
    assert diff(drepo) == 1
    out = capsys.readouterr().out
    assert "README.md:1 `src/pkg/core.py` no longer resolves" in out
    assert "README.md:1 `pkg.core.solve` no longer resolves" in out
    assert "DECISIONS" not in out                                            # records: no ROT


def test_rot_warns_in_gate_mode(drepo):
    git(drepo, "mv", "src/pkg/core.py", "src/pkg/engine.py")
    git(drepo, "commit", "-qm", "rename")
    cfg = config.load(drepo, "HEAD~1")
    res = docs.compare(drepo, gitio.rev(drepo, "HEAD~1"), gitio.rev(drepo, "HEAD"), cfg, rot="warn")
    assert not res.failures and any("no longer resolves" in w for w in res.warnings)


def test_new_broken_reference_fails_but_notes_a_deleted_file(drepo, capsys):
    commit(drepo, {"src/pkg/util.py": "def helper():\n    \"\"\"Help.\"\"\"\n"}, "keep")
    commit(drepo, {"docs/DECISIONS.md": "2026: kept `src/pkg/util.py`.\nremoved `src/pkg/dyn.py`.\n"},
           "log", remove=["src/pkg/dyn.py"])
    assert diff(drepo) == 0                                                  # resolved at base: a note
    commit(drepo, {"docs/guide.md": "See `src/pkg/invented.py` and `pkg.core.imagined`.\n"}, "typo")
    assert diff(drepo) == 0                                                  # default new = "warn"
    out = capsys.readouterr().out
    assert "WARNING docs: docs/guide.md:1 new reference `src/pkg/invented.py` does not resolve" in out
    assert "new reference `pkg.core.imagined` does not resolve" in out
    commit(drepo, {".codehealth/config.toml": DOCS_CONFIG.replace("docstrings = true", 'docstrings = true\nnew = "fail"')},
           "strict")
    commit(drepo, {"docs/guide.md": "See `src/pkg/invented2.py`.\n"}, "typo 2")
    assert diff(drepo) == 1


def test_new_reference_to_a_file_deleted_long_ago_is_a_note(drepo):
    commit(drepo, {}, "delete", remove=["src/pkg/dyn.py"])
    commit(drepo, {"docs/DECISIONS.md": "2026: kept `src/pkg/util.py`.\nonce had `src/pkg/dyn.py`.\n"}, "log")
    assert diff(drepo) == 0


def test_fragile_private_name_and_bare_name_only_warn_or_note(drepo, capsys):
    commit(drepo, {"README.md": "Start with `src/pkg/core.py` and `pkg.core.solve`.\n"
                                "Uses `pkg.util._private_helper`.\n"}, "doc")
    commit(drepo, {"src/pkg/util.py": "def helper():\n    \"\"\"Help.\"\"\"\n    return 1\n"}, "drop helper")
    assert diff(drepo) == 0
    assert "WARNING docs: README.md:2 `pkg.util._private_helper` no longer resolves" in capsys.readouterr().out
    commit(drepo, {"docs/guide.md": "See `nowhere.py`.\n"}, "bare")
    assert diff(drepo) == 0


def test_anchor_drift_warns_and_review_list(drepo, capsys):
    commit(drepo, {"docs/guide.md": "Solve is at `src/pkg/core.py:20`.\n"}, "anchor")
    commit(drepo, {"src/pkg/core.py": "# header\n# more\n" + CORE}, "shift")
    assert diff(drepo) == 0
    out = capsys.readouterr().out
    assert "anchor(s) into src/pkg/core.py drifted" in out and ":20 -> :22" in out
    assert "review docs/guide.md (references src/pkg/core.py, changed here)" in out
    assert any(d == "docs/guide.md" for d, _ in diff_json(drepo)["docs"]["review"])


def test_doc_lines_carried_through_edits(drepo):
    git(drepo, "mv", "src/pkg/util.py", "src/pkg/tools.py")
    commit(drepo, {"README.md": "New intro line.\nStart with `src/pkg/core.py` and `pkg.core.solve`.\n",
                   "docs/DECISIONS.md": "2026: kept `src/pkg/tools.py`.\n"}, "move util and fix docs")
    assert diff(drepo) == 0          # README line moved by an insertion: still carried, still ok


def test_docstrings_ratchet_in_diff(drepo, capsys):
    commit(drepo, {"src/pkg/util.py": UTIL + "\n\ndef added(a):\n    return a\n"}, "add")
    assert diff(drepo) == 1
    assert "src/pkg/util.py:10 added: public name without docstring (D1)" in capsys.readouterr().out


def test_config_check_and_docs_command(drepo, capsys):
    info, bad = config.check(DOCS_CONFIG)
    assert not bad and any(i.startswith("docs: 3 include") for i in info)
    _, bad = config.check(DOCS_CONFIG.replace('python_roots = ["src"]', 'anchors = "loud"'))
    assert any("anchors" in b for b in bad)
    commit(drepo, {"docs/guide.md": "See `src/pkg/gone.py`.\n"}, "stale")
    assert main(["docs", "--repo", str(drepo), "--stats"]) == 0
    out = capsys.readouterr().out
    assert "docs/guide.md:1 path `src/pkg/gone.py`: no such file" in out
    assert "1 broken in living docs" in out


def test_gate_has_docs_and_docstrings_checks(drepo):
    (drepo / "src/pkg/util.py").write_text(UTIL + "\n\ndef added(a):\n    return a\n")
    rc = main(["gate", "--repo", str(drepo), "--log", str(drepo.parent / "g.log"),
               "--json", str(drepo.parent / "g.json")])
    checks = {c["name"]: c for c in json.loads((drepo.parent / "g.json").read_text())["checks"]}
    assert rc == 1 and checks["docs"]["ok"] and not checks["docstrings"]["ok"]


# -- regressions from the implementation review (2026-11-13) -------------------------------------

def head_outcome(repo):
    cfg = config.load(repo, "HEAD~1")
    return docs.compare(repo, gitio.rev(repo, "HEAD~1"), gitio.rev(repo, "HEAD"), cfg)


def test_rot_on_an_edited_line_and_a_renamed_package(drepo):
    commit(drepo, {"README.md": "Start with `src/pkg/core.py` and `pkg.core.solve`, it is short.\n"},
           "edit line", remove=["src/pkg/core.py"])
    res = head_outcome(drepo)
    assert any("`src/pkg/core.py` no longer resolves" in f for f in res.failures)
    assert any("`pkg.core.solve` no longer resolves" in f for f in res.failures)


def test_package_rename_rots_dotted_names(drepo):
    git(drepo, "mv", "src/pkg", "src/pkg2")
    git(drepo, "commit", "-qm", "rename package")
    assert any("`pkg.core.solve` no longer resolves" in f for f in head_outcome(drepo).failures)


def test_links_are_doc_relative_and_dirs_resolve_without_slash(drepo):
    commit(drepo, {"docs/b.md": "B\n", "docs/a.md": "[b](b.md) and `src/pkg` and [root](/README.md)\n"}, "links")
    git(drepo, "mv", "docs/b.md", "docs/old_b.md")
    git(drepo, "commit", "-qm", "move b")
    fails = head_outcome(drepo).failures
    assert any("`b.md` no longer resolves" in f for f in fails)          # no by-name fallback for links
    assert not any("README" in f for f in fails)
    commit(drepo, {}, "drop package", remove=[p for p in ["src/pkg/__init__.py", "src/pkg/core.py",
                                                         "src/pkg/util.py", "src/pkg/star_src.py",
                                                         "src/pkg/dyn.py"]])
    assert any("`src/pkg` no longer resolves" in f for f in head_outcome(drepo).failures)


def test_notebook_cell_renumbering_still_detects_rot(drepo):
    nb = lambda cells: json.dumps({"cells": [{"cell_type": "markdown", "source": c} for c in cells]})
    commit(drepo, {".codehealth/config.toml": DOCS_CONFIG.replace('"CLAUDE.md"]', '"CLAUDE.md", "docs/*.ipynb"]'),
                   "docs/n.ipynb": nb(["see `src/pkg/util.py`"])}, "nb")
    commit(drepo, {"docs/n.ipynb": nb(["intro", "see `src/pkg/util.py`"])}, "insert cell",
           remove=["src/pkg/util.py"])
    assert any("(cell 2) `src/pkg/util.py` no longer resolves" in f for f in head_outcome(drepo).failures)


def test_inter_hunk_context_config_cannot_hide_rot(drepo):
    commit(drepo, {"README.md": "a\nb\nStart with `src/pkg/util.py`.\nd\ne\n"}, "five lines")
    git(drepo, "config", "diff.interHunkContext", "5")
    commit(drepo, {"README.md": "A\nb\nStart with `src/pkg/util.py`.\nd\nE\n"}, "edit around",
           remove=["src/pkg/util.py"])
    assert any("`src/pkg/util.py` no longer resolves" in f for f in head_outcome(drepo).failures)


def test_python_namespace_alias_and_class_branches(drepo):
    commit(drepo, {"src/pkg/ns/mod.py": "def g():\n    pass\n",
                   "src/pkg/m.py": "import sys\nif sys.version_info >= (3, 12):\n    Vec = list\n"
                                   "for _name in ('a',):\n    pass\n\n"
                                   "class C(object):\n    if True:\n        flag = 1\n\n"
                                   "class D(C):\n    pass\n"}, "ns")
    for name in ["pkg.ns", "pkg.ns.mod.g", "pkg.m.Vec", "pkg.m.C.flag", "pkg.m.D.flag", "pkg.m._name"]:
        assert status(drepo, name) == ("python", "ok", ""), name
    assert status(drepo, "pkg.m.C.nope")[1] == "broken"                   # (object) adds nothing
    assert status(drepo, "pkg.m.D.nope")[1] == "broken"                   # in-module base followed
    assert status(drepo, "pkg.ns.nope")[1] == "broken"


def test_gitignore_batch_survives_paths_outside_the_repo(drepo):
    commit(drepo, {".gitignore": "build/\n.venv/\n*.html\n",
                   "docs/sub/a.md": "`../nope/x.py` and `src/out.html`\n"}, "ignore")
    res = docs.analyse(drepo, gitio.rev(drepo, "HEAD"), config.load(drepo, "HEAD"))
    got = {o.ref.text: (o.status, o.reason) for o in res.docs["docs/sub/a.md"]}
    assert got["src/out.html"] == ("skip", "gitignored")
    res.tree.close()


def test_submodule_counts_as_a_directory(drepo, tmp_path_factory):
    sub = tmp_path_factory.mktemp("sub")
    git(sub, "init", "-q", "-b", "main")
    commit(sub, {"x.txt": "x\n"}, "s")
    git(drepo, "-c", "protocol.file.allow=always", "submodule", "-q", "add", str(sub), "extern/lib")
    commit(drepo, {"docs/guide.md": "Vendored in `extern/lib`.\n"}, "submodule")
    assert docs.compare(drepo, gitio.rev(drepo, "HEAD"), None, config.load(drepo, "HEAD")).failures == []


def test_gitignore_batch_survives_a_path_beyond_a_symlink(drepo):
    (drepo / "real").mkdir()
    (drepo / "real/a.txt").write_text("a\n")
    (drepo / "docs/lnk").symlink_to("../real")
    git(drepo, "add", "real/a.txt", "docs/lnk")
    commit(drepo, {"docs/guide.md": "See `docs/lnk/gone.js` and `src/pkg/gone.py`.\n"}, "symlink")
    res = docs.analyse(drepo, None, config.load(drepo, "HEAD"))
    got = {o.ref.text: o.status for o in res.docs["docs/guide.md"]}
    assert got == {"docs/lnk/gone.js": "broken", "src/pkg/gone.py": "broken"}
    res.tree.close()
