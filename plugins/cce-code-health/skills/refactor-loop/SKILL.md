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
   in the self-test and as `gate-fail` in a step (unless the tier sets `detect_exit`:
   then other nonzero codes, such as a usage error, count as `error`, not detected).
   Missing timeouts: ask the human. A tier that needs the campaign base (an impacted-test
   selector with one coverage map for the campaign) uses `{ref_base}`, not `{base}`.
3. Clean tree, campaign branch (repo naming convention), in a worktree if other agents or
   sessions use the repo. **Create every file the campaign will write before the campaign
   base commit**: `.codehealth/playbook.md` (a stub is fine), brief and notes locations.
   Add no files during the campaign: impacted-test selectors may fall back to the full
   suite for any file added after the base, which makes every later step slow.
4. `codehealth gate --self-test --mutate <path:line[:N]>` (N: the Nth literal on the
   line) on a literal in a function the tests
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
     path:a-b`, with a = `line` and b = `end_line` from the scan JSON), at least one test
     must execute the target's lines;
   - if the target contains numeric literals, run `codehealth gate --self-test --mutate
     path:line[:N]` on one or two of them: the gate must detect the mutation.
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
5. **Commit provisionally**, then gate the commit: `refactor(<area>): <what>` with trailers
   `Codehealth-Class: refactor` and `Codehealth-Recipe: <id>` (the run log keeps the debt
   numbers). Gating a commit rather than a dirty tree works for every repo, including
   those whose tests need a clean tree, and the gate JSON then names the commit (`head`).
6. **Gate.** `codehealth gate --mode refactor --base <last accepted commit>
   --ref-base <campaign base> --require-gain --json <gate.json>`.
   It checks scope, frozen and forbidden paths, literals, public API, debt (must go down
   with `--require-gain`), then the repo's tiers (tests, identity, references bitwise,
   benchmark no slowdown). Exit 0 means accept; the summary line shows `debt=a->b`.
   Otherwise drop the step: `git reset --hard <last accepted commit>`.
7. **Log** every attempt, right after the gate (before any reset): `codehealth log
   --gate-json <gate.json> --campaign <id> --target <path::qualname> --recipe <id>
   [--model <name>]`. It infers `accepted`, `gate-fail` (with the tier) or `no-gain`, and
   for an accepted step checks that HEAD's tree is the tree the gate judged (`--head <step sha>`
   when a later commit already sits on top).
   A deliberate step without gain (names, docstrings or comments from review notes) is gated
   without `--require-gain` and logged with `--result accepted-neutral --reason <review note>`.
   It is never a recipe step: it does not count as an accept in playbook statistics.
   **Never write log lines by hand.** The log lives outside the working tree during the
   campaign (the gate rejects any change under `.codehealth/` in a step):
   `$(git rev-parse --git-common-dir)/codehealth/runs/<id>.jsonl`. At campaign end copy it
   to `.codehealth/runs/<id>.jsonl` in a separate docs commit.

## Relocation and milestones

A step may lower a function's debt mostly by moving it into a new helper: the LOG line then
says `N% of it relocated` (record `target_debt.relocated`, with the new functions in
`new_in_file`). Above about 80 % the step is an intermediate, not a gain: say so in the log
(`--hypothesis`) and plan the follow-up that brings the new helper under the brain
thresholds. Close a milestone (a work order, a group of steps) only when, besides debt, the
number of brain functions in scope (`codehealth scan`) is back to at most its value at the
milestone's start. Peclet dem S3/S4: three relocating steps raised brain functions 12 -> 15;
this exit rule forced the follow-up, which brought the count back to 12 in 5 steps.

## Docs debt (repos with a `[docs]` table)

A step never edits docs (scope). The gate's `docs` check warns when a step makes a doc
reference rot (a moved private helper, a shifted anchor): note the warnings in the campaign
log. Before the campaign's merge, fix them in one separate docs commit (it is not a refactor
step; `codehealth diff` over the branch fails until it is done). Docs listed as "review" by
`codehealth diff --base <campaign base>` are read once at campaign end.

## Restructure (splitting large files: human-approved)

Debt inside a 1,000+ line module often needs new modules, which the refactor class forbids
(new files, new public names). Propose instead: list the target file, the new files and the
names that move, and why (`codehealth coupling` and `codehealth arch` give evidence). The
human commits `.codehealth/restructure/<id>.toml` (`id`, `approved_by`, `reason`,
`new_files`, `new_public`) before the campaign base. Steps then run with
`codehealth gate --mode restructure --plan .codehealth/restructure/<id>.toml ...`: new files
and public names only as planned, every old import path kept as a re-export, behaviour
bitwise. Commit trailer `Codehealth-Class: restructure`. Never write or edit the plan yourself.

## Review against gaming (every ~5 accepted steps)

A fresh-context reviewer (the repo's review lane, or an agent that sees only the diffs)
answers per step: is the code easier to read and change? Reject and revert steps that
lower debt by fragmenting logic into meaningless pieces, by moving complexity into a
helper with the same complexity, by bundling unrelated parameters, or by hiding branches
in data structures nobody can read, and the anti-patterns listed in the seed playbook.

A rejected step is undone by an exact revert, gated like a step but allowed to raise debt:
`git revert --no-edit <sha>...` (one commit for all rejected steps of the review, or one
per step), then `codehealth gate --mode refactor --base <sha before the revert>
--ref-base <campaign base> --revert <sha> [--revert <sha> ...] --json <gate.json>`. The
gate checks that the commit is exactly the inverse of those steps and runs the behaviour
tiers. Log it with `codehealth log --gate-json <gate.json> --campaign <id> --target ..
--recipe <recipe of the rejected step> --reason <why>`: it records `review-reject`. If the
revert does not apply cleanly (later steps built on it), rework the code as a normal step
instead.

## Playbook consolidation (every ~10 attempts)

Compute per recipe: attempts, accepts (`accepted` only), gate-fails, review-rejects, median debt reduction,
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
