---
name: optimize-loop
description: Run a performance optimisation campaign on a repository with the codehealth tools - profile, change one hot spot at a time, accept a step only when a gated benchmark shows a real speedup, numerical results stay within the human-declared tolerance and code-health debt does not rise; record evidence and grow the repo's performance playbook. Use when asked to speed up code, optimise a kernel or loop, or run a performance campaign in a repo that has .codehealth/config.toml. Not for pure code-health refactoring (that is bitwise, gate mode refactor).
---

# Optimisation loop

Goal: make the code faster **without making it less healthy** and without moving results
beyond what a human declared acceptable. Every accepted step carries measured evidence.

This is the companion of the refactor loop. The two never mix in one commit:
- **refactor** step: results bit-identical, no slowdown (`codehealth gate --mode refactor`);
- **optimize** step: results within the declared tolerance, measured speedup, debt not
  higher (`codehealth gate --mode optimize`).
If an optimisation needs a structural clean-up first, do the clean-up as a separate
refactor commit gated in refactor mode, then optimise.

## Preconditions (check all; stop and ask the human if one fails)

1. `codehealth --version` works (installed from the pinned tag in the repo's docs/CI).
2. `.codehealth/config.toml` at the base commit has:
   - at least one `kind = "reference"` tier with a `tolerance = {rel, abs, ulp}`, preferably
     with `coverage = true` (Python) so untested changes cannot pass;
   - exactly one `kind = "benchmark"` tier with `primary = true` and `min_speedup`
     (other benchmark tiers only guard against slowdown);
   - `[gate.env]` pins for determinism: OMP_NUM_THREADS, OPENBLAS_NUM_THREADS and
     MKL_NUM_THREADS are required by the gate, plus seeds as needed.
   **Never invent, widen or edit a tolerance, benchmark, min_speedup or any config.**
   Tolerances are a scientific decision of the human: they must sit well below the
   discretisation error and solver tolerances of the outputs they guard. If missing,
   propose values with reasoning and wait.
3. Clean working tree; work on a branch `perf/<campaign-id>` (in a worktree if other
   agents or sessions use the repo).
4. `codehealth gate --self-test` passes: a mutated constant must make every reference
   tier fail. This proves the base run really executes base code (an installed package or
   an absolute import path would compare head with head). Then make a no-op edit (comment)
   and run `codehealth gate --mode optimize --base HEAD`: the primary benchmark must report
   no speedup and every reference bitwise equal. Record the base times.

## Campaign files

- Run log: ``$(git rev-parse --git-common-dir)/codehealth/runs/<campaign-id>.jsonl` during the campaign (the gate forbids changes under `.codehealth/` in a step; copy it there in a separate docs commit at the end), one JSON object per attempt:
  `{"campaign","step","class":"optimize","target":"path::qualname","recipe","hypothesis",
  "result":"accepted|gate-fail|no-gain|review-reject","failed_tier","speedup",
  "max_deviation","debt_before","debt_after","base_sha","head_sha","tool_version","model"}`.
- Playbook: the seed recipes in `playbook-seed.md` (next to this file) plus the repo's
  `.codehealth/playbook.md`, section "Performance recipes" (repo recipes override seed
  recipes with the same id; `disable: <id>` lines switch a seed recipe off).

## One iteration

1. **Profile** the benchmark command at the current head (Python: `py-spy record` or
   `python -m cProfile -s cumtime`; C++: `perf record`/`perf report`, or the repo's own
   timers). Pick the hottest function that is in scope, not frozen, and not failed in
   the last three attempts. Write down where the time goes, with numbers.
2. **Choose a recipe** from the playbook whose "when" matches and whose "when not" does
   not. State a hypothesis: expected speedup and expected round-off effect.
3. **Make one change**, as small as possible. Keep the code healthy: extract kernels into
   well-named functions rather than inlining everything into a larger function; no
   copy-pasted special cases. Do not touch tests, benchmarks, reference cases, config,
   frozen files, string literals or public signatures.
4. **Gate**: `codehealth gate --mode optimize --base <last accepted commit> --ref-base <campaign base>`.
   Static rules and debt are checked against the last accepted commit; references and
   benchmarks against the campaign base, so tolerance and speed form one budget for the
   whole campaign. References must stay within tolerance (head run twice, deterministic);
   with `coverage = true` they must execute every changed function; the primary benchmark
   passes when the lower 95 % confidence bound of the paired ratios reaches `min_speedup`
   (the gate adds rounds itself when the median passes and the bound does not).
5. **Read the report**: the `numeric-literals` check lists every changed numeric constant.
   A changed tolerance, iteration limit, step size or grid size is doing less work, not
   optimising: revert it unless the human asked for exactly that.
6. **Accept or revert.**
   - Accept: commit with message `perf(<area>): <what>` and trailers
     `Codehealth-Class: optimize`, `Codehealth-Recipe: <id>`,
     `Codehealth-Speedup: <x>`, `Codehealth-Max-Deviation: <per output>`.
   - Revert (`git checkout -- <files>`): record `gate-fail` with the failing tier
     (tolerance, benchmark, debt, static) or `no-gain`.
7. Append the run record. One record per attempt, also for reverts.

Every ~10 attempts, **consolidate the playbook**: compute per recipe the number of
attempts, accepts and median speedup from the run logs with a script (never count from
memory), add or update repo recipes, and mark a recipe "when not" after a tolerance or
debt failure. Keep the section short: at most ~15 recipes.

## Stop and report

Stop when three consecutive targets give no accepted step, the remaining profile is
flat (no function above ~5 % of run time in scope), or the human's budget is reached.
Then:
1. `codehealth gate --mode optimize --cadence campaign --base <campaign base>` (all tiers,
   including slow ones) against the campaign base.
2. An independent review of the full diff before any PR: numerics are involved. Use the
   repo's review lane if it has one; otherwise a fresh-context reviewer that sees only the
   diff, the tolerances and the run log.
3. PR without squash (recipe evidence points at commits). Report in about 20 lines:
   total speedup (campaign base vs head, benchmark medians), max deviation per output
   against its tolerance, debt before/after, accepted/attempted steps, recipes that paid
   off, and anything that needs a human decision (for example a speedup that would need a
   looser tolerance: report the number, never take it).

## Never

- Widen a tolerance, lower `min_speedup`, change benchmark inputs, or add suppressions.
- Mix refactor and optimize changes in one commit.
- Change global compiler or interpreter flags (fast-math, `-march`, FMA contraction,
  thread counts): those are campaign-level decisions for the human, recorded in config.
- Claim a speedup that was not measured by the gate on this machine.
