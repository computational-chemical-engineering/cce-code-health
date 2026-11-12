---
name: refactor-loop
description: Run a code-health refactor campaign with the codehealth tools - pick hotspots, make one small behaviour-preserving step at a time, accept a step only when the gate proves results bit-identical, no slowdown, and debt went down; record evidence and grow the repo's playbook, which ordinary coding agents then follow. Use when asked to improve code health, reduce complexity or technical debt, or run a refactor campaign in a repo with .codehealth/config.toml. Not for speed-ups that change round-off (use optimize-loop).
---

# Refactor loop

Goal: lower code-health debt in the configured scope **without any change in behaviour**.
Every accepted step is proven by the gate; every attempt is logged; the playbook learns.

**The repository's own process overrides this skill.** If the repo has work packages,
evidence manifests, lanes or review rules (OpenFlowsheet does: read its CLAUDE.md), the
campaign is one of its work packages and follows those rules.

## Preconditions (check all; stop and report if one fails)

1. `codehealth --version` works (the version pinned by the repo).
2. The base commit has `.codehealth/config.toml` with `[scope]`, `[gate]` static rules and
   behaviour tiers (tests, identity checks, reference cases). Never edit it during a
   campaign; the gate forbids it anyway.
   Every behaviour tier needs a `timeout` (a hung test otherwise stalls the loop; on
   timeout the gate kills the tier's whole process group). A timeout counts as detected
   in the self-test and as `gate-fail` in a step. Missing timeouts: ask the human.
3. Clean tree, campaign branch (repo naming convention), in a worktree if other agents or
   sessions use the repo.
4. `codehealth gate --self-test --mutate <path:line>` on a literal in a function the tests
   execute passes: the gate detects a real change. Without this, every later PASS is
   meaningless.
5. Baseline: `codehealth scan --json <git-common-dir>/codehealth/runs/<campaign>-base.json` and
   `codehealth hotspots --top 30` (record the numbers in the campaign log).

## One iteration

1. **Target.** Highest `commits x debt` file in scope from `codehealth hotspots`; within
   it the function with the largest debt (`codehealth scan --json`, `functions[].debt`).
   Skip frozen files and targets that failed in the last three attempts.
2. **Coverage strength.** The target must be exercised by the behaviour tiers:
   - with an impacted-test selector (OpenFlowsheet: `scripts/test_impact.py select --lines
     path:a-b`), at least one test must execute the target's lines;
   - if the target contains numeric literals, run `codehealth gate --self-test --mutate
     path:line` on one or two of them: the gate must detect the mutation.
   If not covered: either pick another target, or write characterisation tests first in a
   **separate commit** that passes at base, outside the refactor step (repo process
   decides who may add tests; in OpenFlowsheet that is a test-spec task).
3. **Recipe.** Read the playbook: `playbook-seed.md` next to this file, then the repo's
   `.codehealth/playbook.md` ("Refactor recipes" overrides seed ids; `disable: <id>`).
   Choose one recipe whose "when" fits and "when not" does not. State the expected debt
   change.
4. **One small step.** Typically one function. Behaviour-preserving by construction:
   same evaluation order of side effects and floating-point operations, same exceptions,
   same logging and messages, same public names and signatures, same string literals.
   New helpers are private (`_name`) and live in the same module.
5. **Gate.** `codehealth gate --mode refactor --base <last accepted commit>`.
   It checks scope, frozen and forbidden paths, literals, public API, debt (may not rise),
   then the repo's tiers (tests, identity, references bitwise, benchmark no slowdown).
6. **Accept** only if the gate passes **and** debt over the touched files went down
   (`codehealth diff --base <last accepted>` shows it). Commit:
   `refactor(<area>): <what>` with trailers `Codehealth-Class: refactor`,
   `Codehealth-Recipe: <id>`, `Codehealth-Debt: <before> -> <after>`.
   Otherwise **revert** (`git checkout -- <files>`) and record why: `gate-fail` (which
   tier), `no-gain`, or `review-reject`.
7. **Log** one JSON line per attempt. During the campaign the log lives outside the
   working tree (the gate rejects any change under `.codehealth/` in a step):
   `$(git rev-parse --git-common-dir)/codehealth/runs/<campaign>.jsonl`. At campaign end
   copy it to `.codehealth/runs/<campaign>.jsonl` in a separate docs commit.
   `{"campaign","step","class":"refactor","target":"path::qualname","recipe","result",
   "failed_tier","debt_before","debt_after","base_sha","head_sha","tool_version","model"}`.

## Review against gaming (every ~5 accepted steps)

A fresh-context reviewer (the repo's review lane, or an agent that sees only the diffs)
answers per step: is the code easier to read and change? Reject and revert steps that
lower debt by fragmenting logic into meaningless pieces, by moving complexity into a
helper with the same complexity, by bundling unrelated parameters, or by hiding branches
in data structures nobody can read. Record `review-reject` with the reason.

## Playbook consolidation (every ~10 attempts)

Compute per recipe: attempts, accepts, gate-fails, review-rejects, median debt reduction,
from the run logs with a short script (never from memory). Update `.codehealth/playbook.md`:
- **Rules for new code** (at most 10 bullets): the lessons ordinary coding agents must
  follow so the debt does not come back (e.g. "keep functions under cognitive 15; extract
  the parsing phase").
- **Refactor recipes**: id, when, when not, 3 to 6 line recipe, evidence line. Promote a
  candidate after 3 accepts in 2 files; retire below 40 % acceptance after 5 tries, or add
  a "when not" after any gate failure.
The playbook commit is a docs/config commit, separate from refactor commits.

## Stop and report

Stop when no target in scope has debt above the profile thresholds, three consecutive
targets yield nothing, or the budget is reached. Then run the campaign cadence
(`codehealth gate --mode refactor --cadence campaign --base <campaign base> --ref-base <campaign base>`) and the
repo's merge gate (OpenFlowsheet: CI green on both architectures, F0 on main). PR without
squash. Report in ~20 lines: debt and display health before/after (scan), per file;
accepted/attempted; gate-fails by tier; top recipes; anything needing a human decision
(for example a split into new modules, which changes the public API: propose, never do).

## Never

- Change behaviour, numerics, evaluation order of floating-point operations, messages,
  literals, public names or signatures in a refactor step.
- Touch tests, references, benchmarks, config, frozen files or the playbook in a refactor
  commit.
- Add `codehealth: allow` suppressions.
- Mix refactor and optimize changes.
