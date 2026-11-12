# cce-code-health: design (draft 1)

Draft 0 was reviewed on 2026-11-11 (think agent, Opus); its recommendations are folded in
here. Decisions are recorded in docs/DECISIONS.md.

## Goal

Raise and then keep the code health of a repository high, without changing its
behaviour. Three uses: a refactor campaign (the loop), standards for ordinary coding
agents (playbook rules plus an after-edit hook), and a CI gate (no new debt).
Languages: Python first, C++ second (Peclet suite).

## Two places

### Central repo (this one)

1. **Python package, CLI `codehealth`** (check the PyPI name before first release).
   Installed pinned to a tag, never from `main`; the reusable workflow pins the same tag.
   Language backends produce one common report format.
   - `scan`: score every file in scope.
   - `diff --base <ref>`: compare merge-base and head (see Ratchet).
   - `hotspots`: rank files by change frequency × debt, following renames.
   - `gate`: run the behaviour gate for a base/head pair.
   - `playbook stats`: compute recipe evidence from run logs.
   Exit codes: 0 pass, 1 regression, 2 tool error.
2. **Claude Code plugin** (marketplace in this repo, as pymrm does):
   - skill `setup`: writes `.codehealth/config.toml`, detects test commands, proposes
     reference cases, adds CI workflow and pre-commit hook, adds a two-line CLAUDE.md
     pointer to the `healthy-coding` skill.
   - skill `refactor-loop`: the campaign.
   - skill `healthy-coding`: the rules for new code (seed rules + repo rules).
   - hook: after edits, run a fast `codehealth diff` on touched files.
   - **seed playbook, shipped and versioned inside the plugin, never copied** into repos.
   The plugin provides skills and tools, not a competing agent set: repos with their own
   agent lanes (OpenFlowsheet) keep them.
3. **Reusable GitHub Actions workflow** calling `codehealth diff` (and optionally `gate`).

### Per repo: `.codehealth/` (committed)

```
config.toml           schema = 1; metric profile (e.g. python@1); scope, exclusions;
                      gate: build cmd, fast/full test cmds, environment pins,
                      reference cases
playbook.md           local rules and recipes only, plus `disable: <seed-id>` lines
runs/<campaign>.jsonl one file per campaign (no shared append-only file: no conflicts)
```
No committed baseline file.

## Metric

### Debt (the quantity that is gated and optimised)

Per function, uncapped, weighted sum of excess over thresholds:
`(cognitive − 15)+`, `(nloc − 70)+`, `(nesting − 3)+`, `(bumps − 2)+`, `(params − 5)+`,
with `*args`/`**kwargs` counted as +3 parameters. Nesting counts if/loops/try/match,
not `with` (as cognitive complexity). Weights in profile `python@0` (uncalibrated):
cognitive 1, nloc 0.1, nesting 3, bumps 2, params 1, boolops 1. To add: complex conditionals and
low cohesion (CodeScene smells; thresholds to be calibrated). File-level smells:
NLOC > 1000 / 2000, duplicated-line fraction > 10 %.
Debt does not depend on file boundaries, so splitting files or bundling parameters
cannot reduce it without improving functions.

### Health score (display only)

The 1..10 per-file score (prototype `baseline.py`) is for reports and comparison with
CodeScene, not for decisions.

### Calibration and versioning

Calibrate weights against CodeScene on files the demo will not touch (OpenFlowsheet
scripts and tests, pymrm): rank correlation, non-negative weight fit, target ≥ 0.7.
Freeze as a named profile (`python@1`, later `cpp@1`). A repo opts into a profile;
changing profiles is an explicit config change.

### Backends

Python: lizard, complexipy, AST. C++: lizard for now; tree-sitter preferred for both the
hook and CI (clang-tidy needs compile_commands.json and is too slow for the hook;
open question). Duplication: token windows, language-independent.
Function identity key: `path::Class.method` (C++: plus signature).

## Ratchet (CI and hook)

`diff` scores merge-base and head with the same tool version and the **base's**
config.toml (a PR cannot loosen its own thresholds). It fails if:
1. total function debt over the touched files rises (base paths and head paths
   together, with rename detection);
2. a file-level smell newly appears;
3. a new function exceeds the brain level.
New files, renames, moves and splits need no extra rules; concurrent branches never
conflict; a metric upgrade does not break consumers.

Exceptions: inline only, `# codehealth: allow <smell> -- <reason>` (reason mandatory),
listed in every PR report. The loop may never add one.

## Behaviour gate

A refactor step is accepted only if all hold:
1. **References from base, not committed goldens.** Reference cases are run at the
   base commit in the same environment during the campaign, and compared with head.
2. **Determinism first.** A case counts only if two base runs agree bitwise under
   pinned environment (PYTHONHASHSEED=0, BLAS/OMP threads = 1, fixed MPI ranks).
3. **Full-precision byte comparison**, never printed text. Comparator per case:
   `bitwise` (default) or `ulp:n` (only when declared by a human, with a reason).
4. **Commit separation.** Refactor commits touch neither tests, reference definitions
   nor `.codehealth/`. Characterisation tests go in a separate commit that passes at
   base.
5. **Coverage strength.** Target must have branch coverage, and 2 to 3 mutants injected
   into the target must be caught by tests or references. Otherwise write
   characterisation tests first (rule 4).
6. **Public API** (pymrm, peclet-core): snapshot of public signatures unchanged.
7. Fast tests per step; full tests at end of campaign and before PR.

C++: strict gate build with `-ffp-contract=off`, no `-Ofast`/`-ffast-math`/
`-fassociative-math`, same compiler at base and head; bitwise required there. Also build
with production flags and report max ULP difference (FMA contraction, e.g. under
`-march=native`, can change bits when code is extracted). If a project already builds
with contraction off, the second build is unnecessary.

Reference case definition format: command, output files, comparator.

### Gate tiers (from the OpenFlowsheet adopter study, docs/adopters/openflowsheet.md)

The gate is a list of tiers in config.toml, each a repo-supplied command with a cadence:
0 static (seconds, every step: frozen files byte-equal, protected literals, signatures,
lint/type); 1 impacted tests (every step: a repo command that maps base..worktree to test
ids, with full-suite fallback and a printed reason; plus the mutant check on that subset);
2 identity (every step when cheap, e.g. OpenFlowsheet F0 40 s); 3 full suite / CI (campaign
end; the repo's merge condition is never replaced by selection). Selection must account for
non-code inputs (data, schemas, texts), e.g. by recording file opens per test.
Frozen files may be generated by a command (OpenFlowsheet: F0 registry M9).

### Two change classes (2026-11-12)

- **refactor**: results bit-identical, benchmark may not slow down beyond `max_slowdown`,
  debt must decrease (loop) and may not rise (gate). Skill `refactor-loop`.
- **optimize**: results within a tolerance per reference tier declared by a human
  (`tolerance = {rel, abs, ulp}`), benchmark speedup >= `min_speedup`, debt may not rise.
  Skill `optimize-loop` (profile-driven target selection, performance recipes, review of
  the diff before PR). Never mixed with refactor changes in one commit; commit trailer
  `Codehealth-Class`. Frozen files are a per-repo choice (OpenFlowsheet: identity), not a
  default. Peclet and pymrm are expected to use both classes.

## Refactor loop (one iteration)

1. pick target: highest churn × debt in scope, not recently failed;
2. coverage strength check (gate rule 5);
3. read playbook, choose a recipe, make one small step (cap on code growth per step);
4. gate; compute debt;
5. accept (commit) if gate passes and debt decreased, no other file regressed, and a
   fresh-context reviewer (sees only the diff) judges readability improved;
   else revert, recording the reason: `gate-fail`, `no-gain` or `review-reject`;
6. append a record to `runs/<campaign>.jsonl`.
Loop may not edit config, suppressions or tests (tests only via rule 4 commits).
Campaign runs on its own branch in a worktree and ends in a PR, merged **without squash**
(recipe evidence links to commits).

Every log record carries: model, tool version, metric profile, base and head SHA,
target key, recipe id, debt before/after, result, reason.

## Playbook

Two sections:
- **Rules for new code**: at most 10 bullets. The only part ordinary agents read.
- **Refactor recipes**: loop only. Each: stable id, smell targeted, when to use, when not
  to use, a 3 to 6 line recipe, one evidence line.

Evidence is computed by `codehealth playbook stats` from run logs, never counted by an
LLM. Promote a candidate at ≥ 3 accepts across ≥ 2 files. Retire below 40 % acceptance
after 5 tries, or after any `gate-fail` without a written "when not". Size cap about
15 recipes and 120 lines.
Flow: seed (in plugin, versioned) + local recipes (repo) → general lessons proposed to
the seed by a manual PR only.

## Demo

OpenFlowsheet, scope `src/`: CodeScene Community score before; campaign; CodeScene delta
on the PR plus proxy and debt before/after; all tests and reference cases bit-identical.

## Open questions

1. C++ backend: tree-sitter vs lizard + clang-tidy.
2. Exact CodeScene thresholds for complex conditional and low cohesion (calibration).
3. Peclet build flags: is FMA contraction already off?
