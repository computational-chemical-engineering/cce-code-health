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
step must be committed first (HEAD's tree must equal the gate's `head_tree`). Trial and self-test JSONs
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

## Claude Code plugin

```
/plugin marketplace add computational-chemical-engineering/cce-code-health
/plugin install cce-code-health@cce-code-health
```

Skills: `optimize-loop` (performance campaign; tolerances are declared by a human, never by
the loop). `refactor-loop`, `setup` and `healthy-coding` follow.
