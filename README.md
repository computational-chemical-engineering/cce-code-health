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

## Usage (0.2)

```bash
pip install git+https://github.com/computational-chemical-engineering/cce-code-health@v0.3.0
codehealth scan                      # score in-scope files in the working tree
codehealth diff --base origin/main   # ratchet: fail (exit 1) if the change adds debt
codehealth hotspots --top 20         # rank files by commits x debt (follows renames)
```

Every command prints a one-line summary and can write the full report with `--json FILE`.
Exit codes: 0 pass, 1 regression, 2 tool error. `scan`, `hotspots`, `coupling` and `arch` take
`--config FILE` for a read-only trial with another config (nothing in the repo changes).
`scan --group-depth N` adds debt, NLOC, health, files and brain functions per directory prefix of
N path components (text table and JSON `groups`).

**Scan JSON** (`meta.schema` = 1; bumped on a breaking change): `meta` {tool, version, profile,
profiles, config, repo, ref, schema}; `totals` {files, functions, nloc, debt, debt_per_kloc,
health, functions_with_debt, brain_functions, file_smells, errors}; `files`, an object keyed by
path, each {path, nloc, error, debt, file_smells, dup_fraction, health, functions, classes, ...};
`functions` a list of {name, qualname, line, end_line, nloc, <profile metrics>, debt, debt_parts,
brain}; `groups` (with `--group-depth`) an object keyed by prefix with the `totals` fields;
`zones` (with `[[zones]]`) an object keyed by zone, and `zone` on every file and function record.

**Zones** report debt per layer of a repo (for example user-facing, device and host code); they
never change pass/fail, and without them every output is unchanged. Each `[[zones]]` entry is a
rule with a `name`, optional `paths` (globs as in `[scope]`; absent = every file) and optional
`markers` (C++ identifiers). Rules are ordered and several may share a name. A file's zone comes
from the first rule without markers whose paths match it. A function's zone comes from the first
rule whose paths match its file and, if the rule has markers, one of whose markers occurs in the
function's head (from the first declaration line, template and return-type lines included, to the
opening brace; comments do not count). Markers apply to C++ only, so a header can split into
device and host functions. Anything no rule matches is in zone `-` (shown, never dropped;
`config --check` warns when no rule is a catch-all, and when a catch-all hides the rules after it).

```toml
[[zones]]
name = "user"
paths = ["**/*.py", "bindings/*.cpp"]
[[zones]]
name = "device"
paths = ["src/*_kernels.hpp"]
[[zones]]
name = "device"          # a marked function in any other file
markers = ["KOKKOS_INLINE_FUNCTION", "KOKKOS_FUNCTION"]
[[zones]]
name = "host"            # catch-all
```

`scan` prints a table per zone: files, NLOC, health and file smells from the files in the zone;
functions, their NLOC (`fn_nloc`), debt, debt per kNLOC over `fn_nloc`, functions with debt and
brain functions from the functions in the zone. `diff` adds a row per zone (debt over the touched
files before and after, new brain functions) and a JSON `zones`; the gate's `debt` detail adds
one `zones` line.

**Debt** is the quantity that is gated: per function, the weighted excess over thresholds
for cognitive complexity, lines of code, nesting, bumpy road, parameters and boolean
operators per condition (profile `python@0`, see `src/cce_code_health/profiles.py`). Cognitive
complexity comes from complexipy, pinned at 8.3.0 for `python@0` (complexipy 9 scores differently);
lizard is pinned at 1.26.0 for `cpp@0`; scan, diff and gate JSONs record the backend versions
under `meta.backends`.
The 1..10 **health** value is for display only. It measures something different: per file, 10
minus a penalty per smelly function (brain 1.0, complex and deep nesting 0.5, bumpy road 0.5,
long 0.3, many parameters 0.2, each capped per file) and per file smell, averaged over files
weighted by NLOC. Debt measures how far functions exceed the thresholds; health counts how many
places do. Splitting one brain function into two smaller brain functions lowers debt and
health together, on purpose: a reader now has two hard places instead of one (Peclet dem S3:
debt -15, brain functions 12 -> 15, health 5.49 -> 4.98). Such a step is an intermediate; the
follow-up brings the new functions under the brain thresholds, and health recovers.

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
comments, import statements, `__all__` declarations and Python docstrings ignored, multi-line name
lists included). Names are qualified (`ns::Class::method`, no parameter list); lambdas count
towards the enclosing function. Captures are not counted: a lambda that captures the caller's
locals by reference (`[&]`) is as coupled as a long parameter list, but scores none of it (cpp@1 rule 4 counts them, below). Nesting is computed by codehealth, not lizard: if, for, while,
do, switch and try open a level, braced or not; `else if`, else and catch do not. Suppressions
use `// codehealth: allow ccn,nesting -- reason` on the line above the declaration (above its
`template` line, if any) or on the lines up to its opening brace, and `allow-file`.

Profile `cpp@1` is frozen in v0.3.0 (released after a Peclet nine-repo run; design in
docs/CPP1_LAYERS.md). It has the thresholds and weights of `cpp@0`, plus the metric `captures`, and measures a preprocessed
view of each file. Rule (1), in place: of every `#if`/`#ifdef`/`#ifndef` group one branch is
measured (`#if 0`, `#if false` and `#elif 0` are dead; after `#if 1` the later branches are dead;
of the live branches the one with the most decisions, ties to the first, unless its brace balance
differs from the first live branch's), so code under `#ifdef`/`#else` no longer counts twice and
unbalanced braces across branches no longer make lizard drop the functions that follow.
`if constexpr` is not a decision, and `if constexpr`, `else if constexpr` and their else open no
nesting level. Rule (2), in place: a nesting path that holds two loops or more (for, while, do)
counts one level less, so a triple loop with an `if` inside scores nesting 3, not 4. Rule (3), in
place: a kernel lambda (`KOKKOS_LAMBDA(...)` or `KOKKOS_CLASS_LAMBDA(...)` then `{`, or a
`[...]` lambda passed directly to `parallel_for`, `parallel_reduce` or `parallel_scan`) inside a
function is scored as a function of its own, named `<enclosing>::<label>` after the call's
string label (else `<enclosing>::lambda#1`, `#2`, ...); the enclosing function no longer counts
its body (CCN, NLOC, nesting). A lambda can be a brain function. A suppression for it goes on
the line above the launch statement or on the lines up to the lambda's `{`; its zone is read
from that head, so `KOKKOS_LAMBDA` can be a zone marker. Lines and suppressions refer to the
source as written. Rule (4), in place: a kernel lambda carries the metric `captures` (above 12,
weight 0.5; other functions have 0): the entries of its capture list (`this` and `*this` count
one), plus, with a default capture (`[=]`, `[&]`, and every kernel macro), the parameters and
earlier locals of the enclosing function (and the parameters of enclosing kernels) that its
body uses and the list does not name.
`KOKKOS_CLASS_LAMBDA` captures `*this` too. Names after `.`, `->` or `::` are not captures;
members reached through an implicit `this` are not seen. Over 8 it shows the display smell
`many_captures`. Suppress with `allow captures`.

`scan`, `diff`, `hotspots` and the gate's debt check cover C++ files. The gate's static rules
`literals` and `public_api` and the self-test's literal mutation are Python-only: C++ static
rules (public headers, literals) are not implemented yet, and C++ files are skipped there.

## Behaviour gate (0.2)

```bash
codehealth gate --base main                    # refactor mode: bitwise references, no slowdown
codehealth gate --base main --mode optimize    # declared tolerance, required speedup
codehealth gate --base main --cadence campaign # also the slow tiers
codehealth gate --base step-n --ref-base campaign-start --mode optimize  # tiers vs campaign base
codehealth config --check                      # validate .codehealth/config.toml
```

At campaign cadence, paths that only docs/config commits touched since the base (commits that
touch no in-scope file: the playbook commit, the run-log copy, a docs-debt fix, a restructure
plan) pass `scope` and `forbid`, with a note; each step was gated on its own. Frozen paths and
uncommitted changes are never excused.

Static rules (frozen files, forbidden paths with `.codehealth/*` always forbidden, every
touched path in scope, string literals, public API including decorators, dunder methods,
module constants, `__all__`, re-exports and class fields, debt may not rise, no touched file
newly crosses a file-size or duplication threshold; a new brain function is noted, not failed)
and
repo-supplied tiers from `[gate]` in the base's config: `command` (exit code), `reference`
(outputs at head vs base; base run twice for determinism; bitwise in refactor mode,
`tolerance = {rel, abs, ulp}` in optimize mode; `.npy` shape and dtype must match, integers
compare exactly, non-finite values must match) and `benchmark` (warm-up, then ABBA paired
runs, median of the paired ratios, fresh TMPDIR, no writes into the tree). Benchmark tiers
require `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and `MKL_NUM_THREADS` in `[gate.env]`. In
optimize mode only the tier with `primary = true` must reach `min_speedup`; every other
benchmark tier may not slow down beyond `max_slowdown` (default 0.03).

String literals (`literals`, docstrings excluded) over the touched files: none may be new
or occur more often at head. A literal whose count drops passes only when it still occurs and
every scope that reached it at base and still exists reaches it at head: a function or method
(by qualname, nested functions included), a class body or a module's top level reaches a
literal it contains or that a function it references by name (called or passed) reaches. So
merging two duplicated blocks into one helper passes, and deleting one copy of a duplicated
check fails, naming the scope that lost it. Each allowed drop is listed in the check detail
for review. Interface plans with `literals_added`/`literals_removed` stay exact.

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
4 x `repeat`). A benchmark tier's `metric` picks what is timed: `"wall"` (default), `"cpu"`
(the children's user+sys CPU seconds, which a loaded machine inflates far less; it misses I/O,
GPU and sleep, and counts every thread, so a parallel speedup does not show) or `"both"` (a
slowdown guard fails only when both slow down; a speedup claim needs both). An informational `numeric-literals` check lists numeric constants removed or
added. `[gate] prefix` prepends a shell prefix to every tier command; `{gitdir}` and `{repo}`
work in `prefix` and `[gate.env]`.
With `--config` (a trial) a passing gate prints `GATE TRIAL` and exits 3; a `--config` file
byte-identical to the base's committed `.codehealth/config.toml` is not a trial. The base worktree
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
`{files}` is every touched head path, docs and data included (a `--behaviour-only` merge-check
sees the whole lane); `{files:*.py}` (comma-separated globs) keeps the matching ones, for a
linter. It can be empty: `set -- {files:*.py}; [ $# -eq 0 ] || ruff check "$@"`.
The self-test honours `--ref-base` for this placeholder only (without it `{ref_base}` is HEAD);
reference comparison there stays against the unmutated HEAD, and the self-test JSON `meta.ref_base`
records the value (null when not given).
Gate JSON `meta.head` is the HEAD sha when the worktree equals HEAD (a committed step), else
`worktree`; the GATE line prints `head=<sha10>` or `head=worktree`.
Gate JSON `meta` records `debt_before`, `debt_after`, `require_gain`, `trial` and `head_tree` (tree sha of
the judged worktree); each check records `exit_code`. `codehealth gate --require-gain` adds a static
check `gain` that fails unless debt over the touched files dropped.

`codehealth gate --behaviour-only` is a merge-check for a lane whose steps were each gated: over
`--base..head` it runs the config checks and the tiers of `--mode refactor` or `optimize`, with no
static checks (a whole interface or restructure lane edits its plans, tests and public names,
which each step passed under its own plan); debt is reported but not gated. The tiers come from
the base's config; a note check `tier_config` says when the head's `[gate]` table differs. Not
with `--plan`, `--revert` or `--require-gain`; `meta.behaviour_only` is true and `log` refuses the JSON.

```bash
codehealth log --gate-json g.json --campaign c1 --target src/a.py::f --recipe extract-function
```

`codehealth log` appends one validated JSON line to `<git-common-dir>/codehealth/runs/<campaign>.jsonl`
(`--out` overrides). The result is inferred (gate-fail, accepted, no-gain; in optimize mode all checks
ok means accepted) or given with `--result` (also review-reject, which needs `--reason`; a gate-fail
may carry an optional `--reason`, e.g. a review-note step refused on scope or frozen); an accepted
step must be committed first (HEAD's tree must equal the gate's `head_tree`; `--head SHA` names an
earlier commit on the branch instead, for logging after a later docs commit). `--result
accepted-neutral --reason <why>` records a kept refactor or restructure step without debt gain (for
example names and docstrings from review notes): the gate JSON must come from a run without
`--require-gain`, all checks ok, debt not higher. It is never inferred. A check run outside codehealth (a GPU or SASS check) that failed on a tree whose gate passed is
logged with `--result gate-fail --external-fail NAME --reason <what it reported>` (failed_tier
`external:NAME`). `gate_json` is logged relative to the repo root when the file lies inside it.
Trial and self-test JSONs and duplicates are refused, except a re-run: when every record for the campaign, tree and base is a
gate-fail (e.g. a flaky tier), `--rerun-of STEP --reason <why>` logs a new gate JSON for that tree as a
new record with `rerun_of` and `prior_gate_json`; `stats` then counts the superseded gate-fails as
re-runs, not tries. `codehealth scan --json` lists `qualname` and `end_line` per function.
`codehealth stats [--campaign C ...] [--runs FILE_OR_DIR ...]` summarises the run logs per recipe
(tries, accepted, accepted-neutral, no-gain, gate-fails by tier, review-rejects including reverted
accepts, rate, distinct files, median debt drop, median relocated share) with the playbook hints
(promote after 3 accepts in 2 files, retire below 40 % after 5 tries, add a when-not after a
gate-fail). Default sources: the live log and `.codehealth/runs/`, deduplicated.
The run log is one file per clone (`--git-common-dir`), shared by all its worktrees: engineers on
parallel branches in worktrees of one clone append to the same log under a file lock, and each
record carries `branch`. Separate clones keep separate logs.
For results other than accepted, `head_sha` is `meta.head` when it is a sha (a committed step that
failed keeps its evidence), else null. Debt in the gate is per touched file; for a function
target (`path::name`) the record also carries `target_debt`: the function's own debt before and
after (0 when absent; a name matches exactly or as the last `::`/`.` component) and
`new_in_file`, the new functions with debt in that file (an extracted helper that is a hotspot
itself), and `relocated`, their debt as a share of the function's drop (near 1: the complexity
moved to a new address; Peclet dem S3: 0.99 and 0.95 for two steps whose file gain was 0.3 and 5.4). The gate JSON's meta `function_debt` holds the per-function numbers it comes from.

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
kind = "findings"                   # ratchet: no check may gain findings over the touched files
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
  `external:<path>`), follows renames, and fails when a check's count summed over the touched
  files rises (`[check] 3 -> 5 over the touched files (path 1 -> 3)`; code moved between touched
  files takes its findings along) or a (file, check) count of an untouched file rises
  (`path [check] 3 -> 5`). `ignore_exit` (default true) because clang-tidy exits nonzero when it
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
codehealth arch --config trial.toml --depth 9 --edges   # design a layer order: module-level SCCs
```

`--depth N` overrides `cycle_depth` (a large value gives module-level cycles); `--edges` adds the
counts of distinct module-to-module imports between the groups at that depth (`*` marks pairs
present in both directions; JSON `edges`). A table with only `roots` (no rules) reports cycles
and edges, for designing a layer order before writing one.

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
references, no slowdown, debt may not rise, string literals over all touched
files), plus: added files must match `new_files` (check `plan`); no public name may disappear from
any module, and a definition may become a re-export `from <new module> import Name` only when the
planned new file defines it with the identical API entry; new public names must be listed in
`new_public`, and each listed name must exist at head. Gate JSON `meta.plan` = {path, blob, id};
`codehealth log` records class `restructure`. Accepted needs a gain: a debt drop, or debt not
rising while the size-smell weight over the touched files drops (huge_file 2, large_file 1;
meta `size_smells` [before, after]), so a verbatim split of a large file is accepted. In every
mode, and in `codehealth diff`, a new `duplication` smell is a note, not a failure, when the
duplicated lines over the touched files did not rise (a split concentrated existing duplicates).

### Interface steps (deliberate public-API changes)

```bash
codehealth gate --base <last accepted> --mode interface --plan .codehealth/interface/adr-0076-1.toml
```

```toml
id = "adr-0076-1"
approved_by = "jane"
reason = "ADR 0076: drop the compatibility re-exports of pkg.api"
removed = ["src/pkg/api.py:old_solve", "src/pkg/compat.py"]     # path:Name, or a whole module
renamed = {"src/pkg/a.py:Solver" = "src/pkg/core/solver.py:Solver", "src/pkg/b.py" = "src/pkg/core/b.py"}
retargeted = {"src/pkg/api.py:Solver" = "src/pkg/core/solver.py:Solver"}   # re-export dropped, callers go home
changed = ["src/pkg/run.py:run"]                                 # same name and module, new signature
added = ["src/pkg/core/solver.py:make_solver"]                   # new names that are not renames
new_files = ["src/pkg/core/*.py"]
callers = ["tests/**", "docs/examples/**"]                       # files that follow the change

[[literals_added]]                                               # deliberate new string literals
file = "src/pkg/core/region.py"
literal = "LIQUID"
count = 1                                                        # default 1
reason = "private copy of the verifier's K evaluation (R-016)"
# [[literals_removed]] has the same keys
```

For changes an ADR makes on purpose (names removed, renamed, moved or re-signed, no shims). The
plan is read from the base commit, one plan per step; the mode needs `[gate] public_api = true`
(exit 2 otherwise). Plan entries must be in scope; a name entry may not sit under a module the
plan removes or renames; `callers` may not be `**` or cover `.codehealth/`. Everything runs as
in refactor mode, except:
- `public_api` is exact over the touched in-scope Python files, counting every top-level
  binding (inside `if`, `try`, `with` and loops too, class-level assignments as members, a
  module `__getattr__` as a change). A removal must be listed (a class covers its members, a
  module path the whole module) and the name may not survive in any form (import shim, alias,
  `__getattr__`); a rename moves the name with the same API (signature, decorators, bases,
  members, constant value, re-export source); `changed` names keep name and module but their
  API must differ; an addition must be in `added` or be a rename's new side; any other
  difference fails, and `__all__` may change only by the names the plan lists for that file.
  `added = ["f.py:__all__"]` gives an existing module an `__all__` (a literal list of names bound in
  f.py at head; a new public name in it still needs its own `added` entry); `removed` takes it away.
  A listed change the step did not make fails. The check's JSON entry carries `interface`:
  the old and new API text of each planned change, for review.
  With `[gate] no_implicit_reexport = true` (needs `public_api = true`), the old module of a rename or retarget may keep a plain `from <new home> import N` that its
  own code reads: not a re-export under PEP 484. It still counts as a shim in an `__init__.py`,
  when `__all__` lists N or is anything but one literal assignment, when any import of N in
  the module (TYPE_CHECKING blocks too) has an alias or another source, when the module
  star-imports from the home, or when nothing reads N. In exchange, `public_api` scans every
  .py file in the working tree (tests, scripts and benchmarks included; the old module and the
  step's caller files excepted) and fails, with file:line, on any that still takes N through the
  old module: `from <old> import N [as x]` (relative imports resolved) or `<a>.N` with `a` bound
  to the old module by `import <old> [as a]` or `from <pkg> import <leaf> [as a]`. String patch
  targets (`setattr(mod, "N")`, `mock.patch("old.N")`) are not reads. So no importer can keep
  using the old path unnoticed, with or without a type checker over it.
- `literals`: with `literals_added` or `literals_removed` in the plan, the string-literal multiset
  of the touched files may differ from base by exactly the declared entries (no globs, one file
  each), and each entry must hold in its own file (that file's count of the literal moved by at
  least `count` in that direction). The check reports "as planned: ..." and gate meta `plan`
  carries the entries. Without them the rule is refactor's: no literal added or removed.
- `plan`: added in-scope files match `new_files`; touched paths outside the scope or under
  `forbid` must match `callers`. Frozen paths and `.codehealth/` stay refused.
- `retargeted`: the name leaves the old module (a dropped re-export, no shim) while its home
  already defines it at base, unchanged; callers move their imports to the home.
- `callers`: each caller file may only follow the plan. Both versions are normalised: every name
  and attribute chain bound by the file's own imports (per scope) becomes its full dotted target,
  as do `setattr(obj, "name")`, `patch.object(obj, "name")` targets and dotted strings such as
  `mock.patch("pkg.mod.Name")`; the plan's renames, moves and retargets are applied to the base.
  Head must then equal base statement by statement (decorators, bases, exception types and
  test expressions included), except that a statement using a removed name may be deleted (a
  compound one only when every non-trivial statement in it uses one) and the arguments of a
  call to a changed name may differ. Caller files may not be added, deleted or renamed (no new
  `conftest.py`); binary caller files (not strict UTF-8, or holding NUL) may not change;
  non-Python callers may only take the renames, plus one-line `from M import a, b as c` statements
  that follow the plan's moves: in place when every name lands in one module, else split into
  one line per target module (names that stay in M first, then target modules in the plan's
  order), and a split only where the line holds nothing else but an indent, a notebook quote with
  a following source line, or a doctest prompt (parenthesised imports: rewrite by hand). Python source inside a multi-line string with an
  import (a script a subprocess runs) is normalised like code, imports kept as their renamed
  targets, and its line count and comments must match. The check's JSON entry `callers` lists every deleted statement and the number of
  changed calls. Not supported (rejected, rewrite by hand outside the gate): deleting a helper
  together with a removed test, star imports that hide a renamed name.
- `literals`: the `__all__` elements of the files the plan names are left to `public_api`.
`codehealth log` records class `interface`; accepted needs all checks ok and needs no debt drop
(debt may still not rise). With `[docs] docstrings = true`, a renamed undocumented name counts
as new for D1: document it in the same step.

### Documentation health

```bash
codehealth docs --stats            # references per docs class, kind, status and reason
codehealth docs --stale            # living docs whose referenced or co-changing code moved on
codehealth docs --agent            # agent docs: words per doc against the budget, repeated paragraphs
```

`docs --agent` measures agent docs, which a model loads into its context (`[docs] agent`, default
CLAUDE.md, AGENTS.md, `.claude/**/*.md` and `**/SKILL.md` at any depth), against `agent_words`
(default 2500 words per doc) and lists paragraphs of 40+ words repeated across them. In
`codehealth diff` it is a ratchet like debt: a doc over the budget may not grow, one under it may
not cross it, a new one must fit (`agent_budget = "warn"`, the default, or `"fail"`), and a newly
repeated paragraph warns. OpenFlowsheet: 9 agent docs, 6,930 words, none over budget, 3 repeated
paragraphs (a token-economy block copied into 5 agent definitions).

`docs --stale` ranks living docs (records excluded) by the commits that changed their
referenced code and data files after the doc's own last commit (paths, anchors, test ids, and
Python names resolved to their module; references to other docs are not counted), and lists
co-change partners: files that historically change with the doc (`--min-shared` 3 commits,
`--min-degree` 0.5) and changed since, without being referenced. History follows renames and
skips merges and commits of more than 30 files. `codehealth diff` adds informational
`review <doc> (changes with <file> ...)` rows when a co-change partner is touched and the doc is
not. OpenFlowsheet (1,000+ commits, 760 docs): 2.9 s.

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
