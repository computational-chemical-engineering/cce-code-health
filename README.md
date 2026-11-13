# cce-code-health

> **Experimental (0.x).** Interfaces, file layouts and scores may change without notice.

Code-health tooling for the computational-chemical-engineering repositories:

- a measuring tool (`codehealth`) that scores Python and C++ code for the properties that
  make code hard to change (complexity, nesting, size, duplication, cohesion) and ranks
  hotspots by combining those scores with git history;
- a CI gate that blocks changes that make code worse (a ratchet, not an absolute bar);
- a Claude Code plugin with a behaviour-preserving **refactor loop** that improves code
  health step by step and builds up a per-repository **playbook**, which ordinary coding
  agents then follow as their coding standard.

Inspired by Adam Tornhill's work on code health and hotspots. The scores here are an
open-source proxy, not CodeScene's Code Health metric.

See [docs/DESIGN.md](docs/DESIGN.md). License: MIT.

## Usage (0.1)

```bash
pip install git+https://github.com/computational-chemical-engineering/cce-code-health@v0.1.0
codehealth scan                      # score in-scope files in the working tree
codehealth diff --base origin/main   # ratchet: fail (exit 1) if the change adds debt
codehealth hotspots --top 20         # rank files by commits x debt (follows renames)
```

Every command prints a one-line summary and can write the full report with `--json FILE`.
Exit codes: 0 pass, 1 regression, 2 tool error.

**Debt** is the quantity that is gated: per function, the weighted excess over thresholds
for cognitive complexity, lines of code, nesting, bumpy road, parameters and boolean
operators per condition (profile `python@0`, see `src/cce_code_health/profiles.py`).
The 1..10 **health** value is for display only.

`diff` compares the merge-base of `--base` and head (default: working tree) and uses the
base's `.codehealth/config.toml`. It fails if debt over the touched files rises, a file
smell (large file, duplication) appears, or a new brain function appears.

Optional `.codehealth/config.toml`:

```toml
schema = 1
profile = "python@0"
[scope]
include = ["src/*"]        # fnmatch; '*' also matches '/'
exclude = ["src/pkg/_generated/*"]
```

Suppress a smell for one function with a comment on the line above it or on its `def`
line; the reason is mandatory and every new suppression is listed by `diff`:
`# codehealth: allow params -- public API, kept for compatibility`.
File-level smells use `allow-file`.

### Change coupling

```bash
codehealth coupling --since 2026-01-01 --depth 3 --top 30
```

Files that change in the same commits (non-merge, renames followed, one `git log`).
Degree = shared commits / mean revisions of the two files. Pairs over `--min-shared`
(5) and `--min-degree` (0.5) are listed with a `cross-module` marker when the two
modules differ (module = first `--depth` directory components, default the whole
directory); a second table sums shared commits per module pair. Commits touching more
than `--max-files-per-commit` (30) files, such as reformatting, are skipped. Only
in-scope files at HEAD are considered. `--json FILE` writes both tables.

### Cohesion (informational)

`scan --json` lists per file `classes: [{name, line, end_line, methods, lcom4}]` and
`max_lcom4`; `hotspots` shows `max_lcom4` as a column. LCOM4 is the number of connected
components of a class's methods (excluding `__init__`, `__post_init__`, `__new__`),
where methods connect through a shared `self.<attr>` (`cls.<attr>`) or a `self.<name>`
call or access; `None` below 3 methods. 1 is cohesive, 2 or more suggests the class
could be split. It never affects debt, health or the gates.

### C++ (experimental)

`pip install 'cce-code-health[cpp]'` adds [lizard](https://github.com/terryyin/lizard), and
a `[profiles]` table switches C++ on. Without it, C++ files are out of scope; `profile` keeps
selecting the Python profile, as before.

```toml
schema = 1
profile = "python@0"       # Python, unchanged
[profiles]
cpp = "cpp@0"              # .cpp .cc .cxx .c++ .hpp .hh .hxx .h .ipp .tpp .cu .cuh
[scope]
include = ["include/*.hpp", "src/*.cpp", "src/*.hpp"]
```

Profile `cpp@0` is uncalibrated (thresholds and weights by analogy with `python@0`). Debt per
function: cyclomatic complexity (lizard's CCN, which counts if, for, while, case, catch, `&&`,
`||` and `?`) above 10, weight 1; NLOC above 70, weight 0.1; nesting above 3, weight 3;
parameters above 5, weight 1. A brain function has CCN > 25 and NLOC > 70. Binding modules
(`NB_MODULE`, `PYBIND11_MODULE`, `BOOST_PYTHON_MODULE`) are flat registration tables: their
length carries no debt and does not make them brain functions; their CCN still counts. File smells as for
Python: large above 1000 NLOC, huge above 2000, duplication above 10 % (8-line windows,
comments ignored). Names are qualified (`ns::Class::method`, no parameter list); lambdas count
towards the enclosing function. Nesting is computed by codehealth, not lizard: if, for, while,
do, switch and try open a level, braced or not; `else if`, else and catch do not. Suppressions
use `// codehealth: allow ccn,nesting -- reason` on the line above the declaration (above its
`template` line, if any) or on the lines up to its opening brace, and `allow-file`.

`scan`, `diff`, `hotspots` and the gate's debt check cover C++ files. The gate's static rules
`literals` and `public_api` and the self-test's literal mutation are Python-only: C++ static
rules (public headers, literals) are not implemented yet, and C++ files are skipped there.

## Behaviour gate (0.2.0.dev)

```bash
codehealth gate --base main                    # refactor mode: bitwise references, no slowdown
codehealth gate --base main --mode optimize    # declared tolerance, required speedup
codehealth gate --base main --cadence campaign # also the slow tiers
codehealth gate --base step-n --ref-base campaign-start --mode optimize  # tiers vs campaign base
codehealth config --check                      # validate .codehealth/config.toml
```

Static rules (frozen files, forbidden paths with `.codehealth/*` always forbidden, every
touched path in scope, string literals, public API including decorators, dunder methods,
module constants, `__all__`, re-exports and class fields, debt may not rise) and
repo-supplied tiers from `[gate]` in the base's config: `command` (exit code), `reference`
(outputs at head vs base; base run twice for determinism; bitwise in refactor mode,
`tolerance = {rel, abs, ulp}` in optimize mode; `.npy` shape and dtype must match, integers
compare exactly, non-finite values must match) and `benchmark` (warm-up, then ABBA paired
runs, median of the paired ratios, fresh TMPDIR, no writes into the tree). Benchmark tiers
require `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and `MKL_NUM_THREADS` in `[gate.env]`. In
optimize mode only the tier with `primary = true` must reach `min_speedup`; every other
benchmark tier may not slow down beyond `max_slowdown` (default 0.03).

Empty gates fail: an empty diff at cadence step, optimize mode without a reference tier or
a primary benchmark with `min_speedup`, refactor mode without a reference or command tier.
`--ref-base <ref>` (default: same as `--base`) is the campaign base: reference and benchmark
tiers compare head with it, so tolerances and slowdowns cannot compound over steps, while static
checks and debt keep using `--base`. Reference outputs of the ref-base are cached under
`<git-common-dir>/codehealth/cache/<sha>/<tier>/`; head reference runs twice and must be
deterministic. A reference tier with `coverage = true` must execute every changed function
(coverage.py, installed in the command's interpreter). In optimize mode the primary benchmark is
accepted when the 95 % distribution-free lower bound of the median paired speedup reaches
`min_speedup`; if only the median does, ABBA rounds are added up to `max_repeat` (default
4 x `repeat`). An informational `numeric-literals` check lists numeric constants removed or
added. `[gate] prefix` prepends a shell prefix to every tier command; `{gitdir}` and `{repo}`
work in `prefix` and `[gate.env]`.
With `--config` (a trial) a passing gate prints `GATE TRIAL` and exits 3. The base worktree
lives under `<git-common-dir>/codehealth/`.

```bash
codehealth gate --self-test                      # mutate one literal in a HEAD worktree
codehealth gate --self-test --mutate src/a.py:42 # ... or the literal on this line
```

The self-test passes (exit 0) only if every reference tier detects the mutation, which
shows the tiers exercise the checkout under test and not an installed copy. Every behaviour tier
must pass on the unmutated HEAD first ("tier X fails without mutation").
`--mutate path:line:N` picks the N-th numeric literal on the line. Self-test JSON checks carry
`outcome` (detected, missed, error), `exit_code`, `baseline_exit_code` and `detail`; a command tier
may set `detect_exit = [1, 5]` so that any other nonzero exit counts as an error, not a detection.

Tier commands also get `{ref_base}` (the campaign base sha; equal to `{base}` without `--ref-base`).
The self-test honours `--ref-base` for this placeholder only (without it `{ref_base}` is HEAD);
reference comparison there stays against the unmutated HEAD, and the self-test JSON `meta.ref_base`
records the value (null when not given).
Gate JSON `meta.head` is the HEAD sha when the worktree equals HEAD (a committed step), else
`worktree`; the GATE line prints `head=<sha10>` or `head=worktree`.
Gate JSON `meta` records `debt_before`, `debt_after`, `require_gain`, `trial` and `head_tree` (tree sha of
the judged worktree); each check records `exit_code`. `codehealth gate --require-gain` adds a static
check `gain` that fails unless debt over the touched files dropped.

```bash
codehealth log --gate-json g.json --campaign c1 --target src/a.py::f --recipe extract-function
```

`codehealth log` appends one validated JSON line to `<git-common-dir>/codehealth/runs/<campaign>.jsonl`
(`--out` overrides). The result is inferred (gate-fail, accepted, no-gain; in optimize mode all checks
ok means accepted) or given with `--result` (also review-reject, which needs `--reason`); an accepted
step must be committed first (HEAD's tree must equal the gate's `head_tree`; `--head SHA` names an
earlier commit on the branch instead, for logging after a later docs commit). `--result
accepted-neutral --reason <why>` records a kept refactor or restructure step without debt gain (for
example names and docstrings from review notes): the gate JSON must come from a run without
`--require-gain`, all checks ok, debt not higher. It is never inferred. Trial and self-test JSONs
and duplicates are refused. `codehealth scan --json` lists `qualname` and `end_line` per function.
For results other than accepted, `head_sha` is `meta.head` when it is a sha (a committed step that
failed keeps its evidence), else null.

```bash
codehealth gate --base <last-accepted> --revert <sha> [--revert <sha> ...]
```

`--revert` gates a commit that exactly undoes earlier accepted steps (review rejected them).
Static check `revert`: each SHA is a non-merge ancestor of the base and the worktree tree equals the
base with those commits reverted (newest first, via `git merge-tree --write-tree`, git >= 2.38; the
worktree and index are not touched). A conflict or a difference fails the check and lists up to 10
paths. The debt check passes even when debt rises ("rise allowed: exact revert"); `--require-gain`
is a usage error. Meta `revert` lists the full shas. `codehealth log` on a revert JSON needs
`--reason` and `--target`/`--recipe` of the rejected step, writes `class` = `revert` and
`reverts` = the shas, infers gate-fail or review-reject (nothing else is accepted), and for
review-reject requires the revert to be committed (HEAD's tree equals `head_tree`; `head_sha` = HEAD).
Non-revert records carry `reverts` = [].
See the docstring of `src/cce_code_health/gate.py` for the config format.

### Build-tool tiers (C++)

For CMake/ctest suites such as Peclet:

```toml
[profiles]
cpp = "cpp@0"                       # also switches the `flags` static check on
[gate]
flag_patterns = ["EIGEN_DONT_VECTORIZE"]   # optional extra regexes for the flags check
[gate.env]
OMP_NUM_THREADS = "1"
[[gate.tier]]
name = "build"
cmd = "cmake -S . -B build-ch -DCMAKE_EXPORT_COMPILE_COMMANDS=ON && cmake --build build-ch -j8"
[[gate.tier]]
name = "ctest"
cmd = "ctest --test-dir build-ch --output-on-failure"
skip_exit = 77                      # a test that cannot run is "not run", never a pass
fail_regex = '\*\*\*Skipped|Not Run|SKIPPED'   # ctest exits 0 when tests are skipped
[[gate.tier]]
name = "tidy"
kind = "findings"                   # ratchet: no (file, check) may gain findings
cmd = "cmake -S . -B build-tidy -DCMAKE_EXPORT_COMPILE_COMMANDS=ON >/dev/null && run-clang-tidy -quiet -p build-tidy"
cadence = "campaign"
[[gate.tier]]
name = "order"
kind = "convergence"                # head only: observed order of accuracy within band
cmd = "build-ch/bin/refine --out {out}/conv.txt"
outputs = ["conv.txt"]
order = 2.0
band = [1.8, 2.2]
last = 2
```

- `skip_exit` and `fail_regex` work on every tier kind and on every run of it (base and head).
  A match fails the tier ("not run (exit 77)", "output matched fail_regex: <line>") and counts
  as an error, not a detection, in the self-test.
- `same_lines` (reference tiers): a regex; matching output lines, with the tree path replaced by
  `{repo}`, must be the same at base and head. The base side runs in a worktree under the git
  directory, where a sibling checkout (`../core`) does not exist: a build that falls back to
  FetchContent or another install there compiles against a different dependency, and the
  bitwise comparison then measures the wrong thing. Match lines the build prints on **every**
  configure (your own `message()` lines naming resolved paths and versions; `message(STATUS)`
  prints them as `-- <text>`, so match `'^-- \[proj\] '`), and log every branch of a
  resolution symmetrically (sibling and fetched alike), or base and head differ falsely.
  CMake's `-- Found X` lines appear only when a cached result changes, so an incremental head
  configure omits them. A regex that matches nothing on both sides fails the tier. Keep configure output in the log (no `>/dev/null`). Pin such
  dependencies explicitly in the tier command as well (`FETCHCONTENT_SOURCE_DIR_<DEP>`,
  `FETCHCONTENT_FULLY_DISCONNECTED=ON`).
- `findings` runs `cmd` in the tree of `--base` (the last accepted step) and at head, parses
  `file:line:col: warning|error: ... [check]` (gcc, clang with `-fdiagnostics-show-option`,
  clang-tidy), maps absolute paths in either tree to repo-relative ones (others become
  `external:<path>`), follows renames, and fails when any (file, check) count rises, listing
  `path [check] 3 -> 5`. `ignore_exit` (default true) because clang-tidy exits nonzero when it
  finds something. The base tree is cleaned before each use, so `cmd` must configure its own
  build directory; base findings are cached per base sha, tier and cmd.
- `convergence` reads `h error` lines from its single output and requires the observed orders
  `log(e_i/e_{i+1}) / log(h_i/h_{i+1})` (the last `last`, default all) to lie in `band`.
- Static check `flags` (on with a C++ profile or `[gate] flags = true`, off with `flags = false`):
  compiler, floating-point and threading flags are human decisions, never part of a step. Added
  or removed lines in touched build files (CMakeLists.txt, *.cmake, CMake presets, meson.build,
  Makefile, *.mk, setup.py, pyproject.toml) and C/C++/CUDA sources that mention -ffast-math,
  -Ofast, -ffp-contract, -march=, -mavx, `#pragma omp ... num_threads`, `#pragma GCC optimize`
  and similar fail; in build files also any line with CMAKE_CXX_FLAGS, add_compile_options,
  target_compile_options or CMAKE_BUILD_TYPE. Comments are not exempt.

### Architecture rules

```toml
[architecture]
roots = ["src"]                 # src/pkg/mod.py -> pkg.mod, src/pkg/__init__.py -> pkg
type_checking = false           # false: imports under `if TYPE_CHECKING:` are ignored
cycle_depth = 2                 # cycles between packages truncated to this many dotted parts
[[architecture.layers]]
name = "app-layers"
order = ["pkg.application", "pkg.services", "pkg.domain"]   # top to bottom
[[architecture.forbid]]
name = "domain-pure"
from = ["pkg.domain"]
to = ["pkg.application", "requests"]
reason = "domain code has no I/O"
```

```bash
codehealth arch [--ref REF] [--json FILE]   # report violations by rule, and import cycles
```

"a.b" matches a.b and a.b.*. Imports are read from every in-scope .py file under a root, at any
nesting level; relative imports are resolved and `from pkg import name` counts as `pkg.name` when
that module exists. A module may not import a module of a higher layer; a `from` module may not
import a `to` module; cycles are strongly connected components of the internal import graph
truncated to `cycle_depth` parts, each edge with a file:line example. The table is optional
(absent: off) and checked by `config --check`. It is a ratchet: `codehealth diff` and the gate's
static check `architecture` (every mode) fail on a violation (rule, importer, imported) or a cycle
edge that is new at head, and list removed ones as improvements. Existing violations pass.

### Restructure steps

```bash
codehealth gate --base main --mode restructure --plan .codehealth/restructure/split-solver.toml
```

```toml
# .codehealth/restructure/split-solver.toml, committed (and reviewed) before the step
id = "split-solver"
approved_by = "jane"
reason = "solver.py mixes assembly and time stepping"
new_files = ["src/pkg/solver/assembly.py"]
new_public = ["src/pkg/solver/assembly.py:assemble"]
```

Moves code into new modules under a human-approved plan, read from the base commit (absent:
exit 2; the step itself cannot touch `.codehealth/`). Everything runs as in refactor mode (bitwise
references, no slowdown, debt may not rise, string literals as one multiset over all touched
files), plus: added files must match `new_files` (check `plan`); no public name may disappear from
any module, and a definition may become a re-export `from <new module> import Name` only when the
planned new file defines it with the identical API entry; new public names must be listed in
`new_public`, and each listed name must exist at head. Gate JSON `meta.plan` = {path, blob, id};
`codehealth log` records class `restructure` (accepted needs a debt drop, as for refactor).

### Documentation health

```bash
codehealth docs --stats            # references per docs class, kind, status and reason
```

An optional `[docs]` table (design and measurements: docs/DOCS_HEALTH.md) checks that
documentation for humans and agents (README, docs/, CLAUDE.md, .claude/, notebooks) still
points at real code:

```toml
[docs]
include = ["README.md", "CLAUDE.md", "docs/**/*.md", ".claude/**/*.md"]
exclude = ["CHANGELOG.md", "docs/archive/**"]
records = ["docs/*DECISIONS*.md", "docs/adr/**"]   # history: only new references are checked
python_roots = ["src"]
anchors = "warn"                                   # anchor drift: or "fail"
new = "warn"                                       # new unresolved references: or "fail"
docstrings = true
```

References are inline code spans and link targets outside fenced blocks that look like a repo
path (`src/pkg/x.py`, `pkg/x.py`, `x.py`, `dir/`), an anchor (`x.py:120`, `x.py:10-20,31`), a
pytest id (`tests/test_x.py::test_y`) or a Python name of a repo package (`pkg.mod.func`,
`pkg.mod:func`). `codehealth diff` fails when a reference that resolved at base no longer
resolves (rot, also in untouched docs; warns for `_private` names and anchor lines). A newly
written reference to a file, name or test that never existed warns (invented, or planned:
briefs name files before they exist); `new = "fail"` makes it fail. Records get only that
second check. Anchors into code that this change moved warn (`anchors = "fail"`
fails), and docs referencing changed files are listed as "review". The gate's static check
`docs` warns on rot (a step may not edit docs; fix it in a docs commit before merge). With `docstrings = true`, new public Python names need a docstring
(D1) and a documented parameter list must match the signature (D2): in diff, the gate (check
`docstrings`) and the edit hook. Existing gaps pass. Executable docs: wire the repo's own
runner (example notebooks, snippet checks) in as a gate tier.

## Claude Code plugin

```
/plugin marketplace add computational-chemical-engineering/cce-code-health
/plugin install cce-code-health@cce-code-health
```

Skills: `optimize-loop` (performance campaign; tolerances are declared by a human, never by
the loop). `refactor-loop`, `setup` and `healthy-coding` follow.

## Edit-time feedback (Claude Code hook)

The plugin registers a PostToolUse hook (`Edit|Write|MultiEdit`) that runs `codehealth hook`
after each edit of a `.py` file. It compares the file with its version at `HEAD` (per file only:
no duplication, no repo scan) and, when the edit raised the file's debt, added a function with
debt, a brain function, a file smell (large/huge) or an invalid suppression, prints a short
report to stderr and exits 2, so Claude Code shows it to the model. Otherwise it is silent. It
needs a `.codehealth/config.toml` in the repo and never blocks on an internal error.

`hooks/post_edit.sh` picks the binary in this order: `$CODEHEALTH_BIN`,
`<repo>/.venv-codehealth/bin/codehealth`, `codehealth` on `PATH`; if none exists it does nothing.

Disable it with the environment variable `CODEHEALTH_HOOK=0`, or per repo in the config:

```toml
[hook]
enabled = false
```

## CI

A reusable workflow runs the `codehealth diff` ratchet on pull requests and writes the output to
the job summary. Call it from the adopting repo, pinned by sha:

```yaml
jobs:
  codehealth:
    uses: computational-chemical-engineering/cce-code-health/.github/workflows/codehealth-diff.yml@<sha>
    with:
      codehealth-ref: <sha>      # version of cce-code-health to install
      # python-version: "3.12"
      # base-ref: main           # default: the PR base branch
```
