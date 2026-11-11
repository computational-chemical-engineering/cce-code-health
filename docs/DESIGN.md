# cce-code-health: design (draft 0, for review)

## Goal

Raise and then keep the code health of a repository high, without changing its
behaviour. Three uses: a one-off refactor campaign (the loop), continuous standards for
ordinary coding agents (the playbook plus hooks), and a CI gate (the ratchet).
Languages: Python first, C++ second (Peclet suite).

## Two places

### Central repo (this one)

1. **Python package, CLI `codehealth`**, installable with
   `pip install git+https://github.com/computational-chemical-engineering/cce-code-health`.
   Language backends produce one common report format.
   - `scan`: score every file in scope, write a report.
   - `diff --base <ref>`: score files changed against a base; exit non-zero if any
     touched file's score drops below its baseline value, or a new or changed function
     crosses a hard threshold.
   - `hotspots`: rank files by change frequency × (10 − health).
   - `gate`: run the repo's behaviour gate (see below) and report pass/fail.
   - `baseline update`: rewrite `.codehealth/baseline.json` (only ever upward per file,
     unless forced with a recorded reason).
2. **Claude Code plugin** (marketplace in this repo, as pymrm does):
   - skill `setup`: inspects a repo, writes `.codehealth/config.toml`, detects the test
     command, proposes reference cases, adds CI workflow, pre-commit hook, first
     baseline, and a short CLAUDE.md snippet.
   - skill `refactor-loop`: the campaign (below).
   - skill `healthy-coding`: thresholds and how to use the repo playbook while coding.
   - hook: after edits, run `codehealth diff` on touched files and report regressions.
   - seed playbook: general patterns per language, copied into a repo at setup.
   The plugin provides skills and tools, not a competing agent set: repos with their own
   agent lanes (OpenFlowsheet) keep them.
3. **Reusable GitHub Actions workflow** calling `codehealth diff` and `codehealth gate`.

### Per repo: `.codehealth/` (committed)

```
config.toml    scope, exclusions, thresholds overrides, language backends,
               gate: build cmd, fast test cmd, full test cmd, reference cases
baseline.json  per-file scores at the last accepted state (the ratchet)
playbook.md    repo playbook: patterns with evidence (applied n, reverted m, example commit)
log.jsonl      append-only: one record per loop attempt (target, pattern, result, reason)
```

## Measurement (proxy health 1..10 per file)

Function-level smells: cognitive complexity > 15 (complex) and > 40 with NLOC > 70
(brain), NLOC > 70 (long), nesting ≥ 4, bumpy road (≥ 3 nested blocks), ≥ 6 parameters.
File-level: NLOC > 1000 / > 2000, duplicated-line fraction > 10 %. Penalties per smell
with caps per category; score = max(1, 10 − penalties). Project score: NLOC-weighted
mean. Python: lizard, complexipy, AST. C++: lizard, clang-tidy
(`readability-function-cognitive-complexity`, `readability-function-size`).
Duplication: token windows, language-independent.
Prototype: `baseline.py`; first run in `baselines/openflowsheet-a9e733e4/summary.md`.

## Behaviour gate

A refactor step is accepted only if:
1. build succeeds (C++);
2. fast tests pass (per-step), full tests pass (end of campaign, and before PR);
3. reference cases reproduce stored outputs **bit-identically**;
4. the target code was covered by tests before the step. If not, the loop first writes
   characterisation tests that pin current behaviour, commits them separately, and only
   then refactors.

C++ floating point: semantically neutral refactors can change bits (FMA contraction,
inlining, vectorisation). Proposal: gate builds use `-ffp-contract=off`, no fast-math;
a per-output tolerance is allowed only when declared in config with a reason.

## Refactor loop (one iteration)

1. pick target: highest hotspot rank in scope, not recently failed;
2. coverage check (gate rule 4);
3. read playbook, choose a pattern, make one small step;
4. gate; measure target file;
5. accept (commit) if gate passes and health improved without regressing other files;
   else revert;
6. append to `log.jsonl`.
Every N iterations: consolidate the log into `playbook.md` (patterns with counts and an
example commit; prune patterns that keep failing). Anti-gaming: reject steps that improve
the score by fragmenting code without improving readability (review criterion in the
skill, and CodeScene as external judge).
Campaign runs on its own branch in a worktree and ends in a PR.

## Playbook flow

Seed (central) → copied at setup → grows per repo through the loop → general lessons
promoted back to the central seed by a manual PR only.

## Demo

OpenFlowsheet, scope `src/`: CodeScene Community score before; run campaign; CodeScene
delta on the PR plus proxy before/after; all tests and reference cases unchanged.

## Open questions (for review)

1. Is the `.codehealth/` layout and the ratchet rule (per-file, never down) right, and how
   are new files, renames, and deliberate exceptions handled?
2. Gate rules, especially bit-identical vs tolerance, and the C++ floating-point policy.
3. Proxy score: are the smells, thresholds and penalties sensible; how to calibrate
   against CodeScene; how to keep the score from being gamed.
4. Playbook format and consolidation rule so it stays short and evidence-based.
5. Anything missing that will be expensive to change once several repos depend on it.
