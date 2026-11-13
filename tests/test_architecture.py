import json

import pytest
from test_gate import CONFIG as GATE_CONFIG
from test_gate import MOD
from test_git import commit, git

from cce_code_health.architecture import _sccs, analyse_texts
from cce_code_health.cli import main

ARCH = """
[architecture]
roots = ["src"]
cycle_depth = 2
[[architecture.layers]]
name = "app-layers"
order = ["pkg.app", "pkg.services", "pkg.domain"]
[[architecture.forbid]]
name = "domain-pure"
from = ["pkg.domain"]
to = ["pkg.app", "requests"]
reason = "domain code has no I/O"
"""
ARCH_TABLE = {"roots": ["src"], "cycle_depth": 2,
              "layers": [{"name": "app-layers", "order": ["pkg.app", "pkg.services", "pkg.domain"]}],
              "forbid": [{"name": "domain-pure", "from": ["pkg.domain"], "to": ["pkg.app", "requests"]}]}


def keys(res):
    return {(r, a, b) for r, a, b in res.violations}


def test_layer_violation_detected():
    res = analyse_texts({"src/pkg/__init__.py": "", "src/pkg/app/__init__.py": "",
                         "src/pkg/app/ui.py": "from pkg.services import run\n",
                         "src/pkg/services/__init__.py": "def run(): pass\n",
                         "src/pkg/services/svc.py": "def f():\n    import pkg.app.ui\n"}, ARCH_TABLE)
    assert keys(res) == {("app-layers", "pkg.services.svc", "pkg.app.ui")}
    imp = res.violations[("app-layers", "pkg.services.svc", "pkg.app.ui")]
    assert (imp.path, imp.line) == ("src/pkg/services/svc.py", 2)


def test_forbid_external_and_relative_imports():
    res = analyse_texts({"src/pkg/__init__.py": "", "src/pkg/domain/__init__.py": "",
                         "src/pkg/domain/model.py": "import requests.adapters\nfrom . import util\n"
                                                    "from ..app import views\nfrom .util import helper\n",
                         "src/pkg/domain/util.py": "", "src/pkg/app/__init__.py": "",
                         "src/pkg/app/views.py": ""}, ARCH_TABLE)
    targets = {i.imported for i in res.imports if i.importer == "pkg.domain.model"}
    assert targets == {"requests.adapters", "pkg.domain.util", "pkg.app.views"}
    assert keys(res) == {("domain-pure", "pkg.domain.model", "requests.adapters"),
                         ("domain-pure", "pkg.domain.model", "pkg.app.views"),
                         ("app-layers", "pkg.domain.model", "pkg.app.views")}


def test_type_checking_ignored_by_default():
    src = ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg.app import ui\n"
           "else:\n    import requests\n")
    texts = {"src/pkg/domain/m.py": src, "src/pkg/app/ui.py": ""}
    assert keys(analyse_texts(texts, ARCH_TABLE)) == {("domain-pure", "pkg.domain.m", "requests")}
    both = keys(analyse_texts(texts, {**ARCH_TABLE, "type_checking": True}))
    assert ("domain-pure", "pkg.domain.m", "pkg.app.ui") in both


def test_cycle_found_with_example_line():
    res = analyse_texts({"src/pkg/a/x.py": "import os\nfrom pkg.b import y\n",
                         "src/pkg/b/y.py": "\n\nimport pkg.c.z\n", "src/pkg/c/z.py": "from pkg.a import x\n",
                         "src/pkg/d/w.py": "import pkg.a.x\n"}, {"roots": ["src"]})
    assert [c["nodes"] for c in res.cycles] == [["pkg.a", "pkg.b", "pkg.c"]]
    assert set(res.cycle_edges) == {("pkg.a", "pkg.b"), ("pkg.b", "pkg.c"), ("pkg.c", "pkg.a")}
    edge = next(e for e in res.cycles[0]["edges"] if e["from"] == "pkg.b")
    assert (edge["path"], edge["line"], edge["imported"]) == ("src/pkg/b/y.py", 3, "pkg.c.z")


def test_tarjan_is_iterative():
    n = 5000
    succ = {i: {i + 1} for i in range(n)}
    succ[n] = {0}
    assert [len(c) for c in _sccs(list(range(n + 1)), succ)] == [n + 1]


def test_config_check_rejects_unknown_keys(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text("schema = 1\n" + ARCH + "bogus = 1\n[[architecture.layers]]\nname = 'x'\norder = 'a'\n")
    assert main(["config", "--check", "--config", str(cfg)]) == 1
    out = capsys.readouterr().out
    assert "unknown key 'bogus'" in out and "order must be a non-empty list" in out
    cfg.write_text("schema = 1\n" + ARCH)
    assert main(["config", "--check", "--config", str(cfg)]) == 0


# -- diff ratchet and gate ----------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".codehealth/config.toml": 'schema = 1\n[scope]\ninclude = ["src/*"]\n' + ARCH,
                      "src/pkg/__init__.py": "", "src/pkg/app/__init__.py": "",
                      "src/pkg/app/ui.py": "X = 1\n", "src/pkg/domain/__init__.py": "",
                      "src/pkg/domain/model.py": "import requests\n\n\ndef f():\n    return 1\n"})
    return tmp_path


def diff(repo, *extra):
    return main(["diff", "--repo", str(repo), *extra])


def test_arch_command_reports(repo, capsys):
    out_json = repo.parent / "a.json"
    assert main(["arch", "--repo", str(repo), "--json", str(out_json)]) == 0
    out = capsys.readouterr().out
    assert "forbid domain-pure: 1 violation(s) -- domain code has no I/O" in out
    assert "pkg.domain.model -> requests (src/pkg/domain/model.py:1)" in out
    data = json.loads(out_json.read_text())
    assert data["violations"][0]["rule"] == "domain-pure"


def test_diff_ratchet(repo, capsys):
    (repo / "src/pkg/domain/model.py").write_text("import requests\n\n\ndef f():\n    return 2\n")
    assert diff(repo) == 0                                   # existing violation passes
    (repo / "src/pkg/domain/model.py").write_text("import requests\nimport pkg.app.ui\n")
    assert diff(repo) == 1
    out = capsys.readouterr().out
    assert "new architecture violation: app-layers: pkg.domain.model -> pkg.app.ui (src/pkg/domain/model.py:2)" in out
    (repo / "src/pkg/domain/model.py").write_text("def f():\n    return 1\n")
    assert diff(repo) == 0
    assert "removed architecture violation: domain-pure: pkg.domain.model -> requests" in capsys.readouterr().out


def test_diff_new_cycle_edge_fails(repo, capsys):
    commit(repo, {"src/pkg/app/ui.py": "from pkg.domain import model\n"})
    (repo / "src/pkg/domain/model.py").write_text("import requests\n\n\ndef f():\n    from pkg.app import ui\n")
    assert diff(repo) == 1
    out = capsys.readouterr().out
    assert "new import cycle edge: pkg.app -> pkg.domain" in out
    assert "new import cycle edge: pkg.domain -> pkg.app" in out


def test_gate_architecture_check(tmp_path, capsys):
    cfg = GATE_CONFIG.replace('include = ["pkg/*"]', 'include = ["pkg/*"]\n[architecture]\nroots = ["."]\n'
                              '[[architecture.forbid]]\nname = "no-tests"\nfrom = ["pkg"]\nto = ["tests"]\n')
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {".gitignore": "__pycache__/\n", ".codehealth/config.toml": cfg, "pkg/__init__.py": "",
                      "pkg/mod.py": MOD, "frozen/ident.py": "X = 1\n", "tests/test_x.py": "def test(): pass\n"})
    log = str(tmp_path.parent / "g.log")
    (tmp_path / "pkg/mod.py").write_text(MOD.replace("Docstrings may change.", "Changed doc."))
    assert main(["gate", "--repo", str(tmp_path), "--log", log]) == 0
    assert "ok   architecture" in capsys.readouterr().out
    (tmp_path / "pkg/mod.py").write_text(MOD.replace("def calc(x):\n", "def calc(x):\n    import tests\n"))
    assert main(["gate", "--repo", str(tmp_path), "--log", log]) == 1
    out = capsys.readouterr().out
    assert "FAIL architecture" in out and "new architecture violation: no-tests: pkg.mod -> tests" in out
