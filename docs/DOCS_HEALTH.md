# Documentation health: design (draft 2, 2026-11-13, after think review)

Goal: documentation for humans and agents (README, docs/, tutorials, CLAUDE.md, agents, skills,
state and decision files, docstrings) does not silently drift from the code. Same philosophy as
the debt ratchet: measure deterministically, never fail on old problems, fail on new ones.

## What adopters already have (recon 2026-11-13)

| | OpenFlowsheet | Peclet |
|---|---|---|
| site build | `mkdocs build --strict` (site.yml) | umbrella `mkdocs build --strict`; Doxygen per repo, warnings not errors |
| run examples | tests/test_doc_examples.py runs examples/*.ipynb in CI, compares numbers to the page | tools/release/check_docs_snippets.py `--run` (README, docs/index.md, quickstart notebook) and NAMES (python blocks of other pages vs the binding surface); peclet-examples check_pages.py weekly |
| generated pages | scripts/doc_pages.py --check, test_doc_pages.py, test_readme_current.py | API pages generated at build; decisions/build_index.py not in CI |
| docstrings | none (ruff without D rules) | tools/release/audit_docstrings.py, manual pre-flight only |
| agent docs | CLAUDE.md 94 lines, .claude/agents 395 lines, ~67 docs/*_STATE.md, 74 ADRs: **nothing checked** | CLAUDE.md 183 to 507 lines per repo, umbrella skills, DECISIONS.md 804 lines: **nothing checked** |

Peclet's lesson (check_docs_snippets.py docstring): only execution catches a signature change;
record pages (NAMING, CHANGELOG, archive) legitimately name retired spellings.

Conclusion: no new example runner (both adopters have one; wire it in as a gate tier). Build
what nobody has: references in prose and agent docs, anchors, docstring presence and parameter
drift.

## Config: `[docs]` (opt-in; read from the base commit in diff and gate)

```toml
[docs]
include = ["README.md", "CLAUDE.md", "**/CLAUDE.md", "docs/**/*.md", ".claude/**/*.md"]
exclude = ["CHANGELOG.md", "docs/archive/**"]
records = ["docs/*DECISIONS*.md", "docs/adr/**", "docs/reviews/**"]   # history: NEW check only
python_roots = ["src"]        # default: [architecture] roots, else ["src", "."]
anchors = "warn"              # anchor drift: "warn" (default) or "fail"
new = "warn"                  # new unresolved references: "warn" (default) or "fail"
docstrings = true             # D1/D2 on in-scope Python files (tests excluded)
```

## 1. References (docs.py; `codehealth docs [--stats]`)

Candidates: inline code spans and Markdown link targets outside fenced blocks (notebooks:
markdown cells). Shapes (everything else is ignored):

- **path**: after stripping a `word:` prefix (`doc:`), a `#fragment` and a line list
  (`:N`, `:N-M`, `:N,K-L`), only `[A-Za-z0-9_.-/]` (R1); with a known extension or a trailing
  `/` it is a reference, without one only when it resolves as written (R2: branches, JSON
  pointers, ratios). Roots in order (R4): repo root, the doc's directory,
  `<python_root>/<package>/`, any tracked path with that suffix. Skipped (R5): paths leaving
  the repo, a first component that is no tracked directory name (other repos, `origin/main`),
  gitignored paths (one batched `git check-ignore`). Worktree = tracked + untracked (R6).
- **anchor**: path with a line list; lines <= the file's length. A missing file is a path
  break; only the line part is fragile.
- **node**: `file.py::name[::name]` (R3): the file defines each name (def, class or
  assignment; `name…`, `name...`, `name*` match as a prefix; call arguments stripped).
- **python**: `pkg.mod.name[()]` or `pkg.mod:name`, first component a top-level package under
  `python_roots`; static resolution through modules, namespaces (imports followed, 5 hops),
  `if`/`try` branches, classes (body, `self.x =`); implicit module attributes (`__file__`,
  `__version__`, ...), out-of-repo imports, star imports, module `__getattr__`, classes with
  bases and unparsable modules resolve anything below them. A `_private` component is fragile.

Not checked: link fragments (GitHub and mkdocs slugs differ), C++ names, CLI flags, config
keys, compiled binding names (Peclet's NAMES check covers those).

## 2. Ratchet (diff, gate)

Per **occurrence**, not per text: each head occurrence is mapped to base through the doc's own
`git diff -U0` (rename-aware).

- **ROT**: on a carried line, resolved at base, not at head. Fails in `codehealth diff` (CI,
  feature work), warns when fragile. In the gate it always warns: a step may not edit docs, so
  rot is docs debt, fixed in a docs commit; the branch's CI diff then fails until it is.
  Records get no ROT.
- **NEW-broken**: on an added or changed line and unresolved, the target did not resolve at
  base, has no history (`git log` for paths, also `**/` suffix; `git log -S` for Python and
  test names) and no base doc names it broken; bare file names are never reported. It
  **warns** by default (`new = "fail"` to fail). Replay (below) showed every such case in 60
  OpenFlowsheet commits was a forward reference in a brief, review or record (a file a worker
  will create), not an invented one. Else a note in the JSON.
- **Anchor drift**: a carried anchor into a file this change touched is mapped through that
  file's hunks (diff pinned: `--diff-algorithm=myers --no-indent-heuristic --no-ext-diff`;
  insertion `-k,0` after old line k; for `N-M` an insertion inside counts as changed; a line in
  a hunk falls back to a unique stripped-text match). Aggregated per target file; warns
  (`anchors = "fail"` to fail). Not for records, not for ambiguous bare anchors.
- **Docs to review** (informational): carried references whose target file this change
  touched (diff output, max 20, and JSON `docs.review`): the input for a docs-sync review.

## 3. Docstrings (docstrings.py; D1, D2)

Public: in-scope modules without a `_private` path component, not tests (`tests/`, `test_*`,
`*_test.py`, conftest); top-level def/class without underscore (only `__all__` names when it is
a literal list), including `if`/`try` branches but not `if TYPE_CHECKING:`; non-underscore
methods of public classes. Not public: nested classes, `@overload`, setters/deleters,
`@override`, methods an in-module base class defines. Duplicate definitions count as
documented if one is.

- **D1**: new public name without docstring fails; grandfathered when the same qualname was
  undocumented at base in any touched file (moves keep their status).
- **D2**: a docstring with a parameter section (numpydoc `Parameters`/`Other Parameters` with
  underline; Google `Args:`/`Arguments:`/`Keyword Args:`; Sphinx `:param [type] x:`) must list
  the signature (first parameter of non-static methods dropped; `*args`/`**kwargs` optional;
  extra names allowed with `**kwargs`). Classes: only against an explicit `__init__`
  (dataclasses, attrs, NamedTuple, inherited constructors are skipped). Ratchet as D1.
- Hook: D1/D2 on each edited Python file vs HEAD (exit 2 with the list). Gate: check
  `docstrings`. C++ (Doxygen `@param`) later.

## 4. Executable docs

The repo's own runner as a gate tier (kind command, behaviour = true, timeout, cadence
campaign, or step when fast):
- OpenFlowsheet: `pytest -q tests/test_doc_examples.py tests/test_doc_pages.py tests/test_readme_current.py`;
- Peclet umbrella: `python tools/release/check_docs_snippets.py --installed --run`.
No runner in cce-code-health (cut after review).

## Measured (OpenFlowsheet 88da7f68, trial config)

763 docs, 26,129 references, 22,250 resolve; 457 broken in living docs and 651 in records,
all grandfathered. `codehealth docs` 2.9 s per ref. Replay of the ratchet over the last 60
first-parent commits (about 5 s per commit): 0 rot failures; 40 new unresolved references in 23
commits, all forward references (briefs 18, reviews 8, records and progress 14), hence
`new = "warn"` by default; anchor drift warnings in 9 commits.

## Implementation review (think, 2026-11-13)

Fixed before the first commit, each with a regression test in tests/test_docs.py: rot on an
edited line (a reference the doc already had) or in a renumbered notebook cell; a renamed
top-level package (packages are the union of base and head); links resolve relative to the doc
only (`/x` = repo root), no by-name fallback; directories named without a trailing slash;
gitignore batch with `-z`, repo-relative candidates only, and a retry without a path git
rejects (outside the repo, beyond a symlink); submodules count as directories (ls-tree mode
160000, worktree via `ls-files --stage`); diff pinned with `--inter-hunk-context=0
--no-textconv`; git line semantics (`\n` only); namespace packages, PEP 695 aliases, `for`
targets, class members in `if`/`try`, in-module bases followed and `object` ignored; longest
python root first; trees closed in `finally`; D1/D2 cross-file grandfathering only for names
that left their base file.

## 5. Staleness from history (built 2026-11-14)

`codehealth docs --stale` (docs_stale.py): per living doc, commits to its referenced code and
data files since the doc's last commit, plus unreferenced co-change partners changed since;
`diff` adds co-change review rows. Doc-to-doc references are not counted (a spec pointing at a
busy state file is not stale code-wise; the first trial on OpenFlowsheet was dominated by them).

## 6. Agent-doc budget (built 2026-11-14)

`codehealth docs --agent` (agent_docs.py): words per agent doc against `agent_words`, repeated
40+ word paragraphs across agent docs; a ratchet in `diff` (warn by default). OpenFlowsheet: 9 docs,
6,930 words, 3 repeated paragraphs.

## Later

A docs-sync review lane fed by "docs to review", size
and duplication budget for agent docs, exemplars with a strict ratchet, CLI-flag and
config-key vocabularies, C++ D1/D2 and names.
