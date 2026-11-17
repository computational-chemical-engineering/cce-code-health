# python@1 campaign: state

Rewritten in place. History: docs/STATE_LOG.md. Decisions: docs/DECISIONS.md. Briefs: docs/briefs/py1-*.md.
Data and notes: docs/py1/.

Objective: a new Python profile python@1 that ranks and flags files like CodeScene and blind reviews do,
plus a class-state smell neither sees. python@0 stays frozen and bit-identical. Release v0.4.0
(the tag needs its own Desk card).

Authority: Desk card cce-python1-start, choice A, VERIFIED 2026-11-17 (key 032ee0a5ae620a90):
"start the codehealth python@1 profile design and implementation now, with docstring lines excluded
from function and file size".

Input: pymrm-health recommendation (2026-11-17) and data in
~/Code/pymrm_suite/worktrees/pymrm-health-notes/{codescene_vs_python0.md,solve_py_blind_review.md}.

## Gates (written before any run; the orchestrator judges, a miss is recorded as a miss)

| gate | criterion | python@0 reference |
|---|---|---|
| G0 | python@0 unchanged: full suite passes; scan JSON byte-identical for python@0 on OpenFlowsheet a9e733e4 and pymrm main | n/a |
| G1 | OpenFlowsheet a9e733e4, CodeScene 8385360: Spearman(CodeScene, -debt) on the fit set (tests, scripts, benchmarks, docs) >= python@0 value per area; on src/ (held out) >= 0.90 | src 0.90; tests 0.72, scripts 0.92, docs 0.93, benchmarks 0.91 |
| G2 | pymrm src (15 files), CodeScene 8475071: Spearman(CodeScene, -debt) python@1 > python@0 | measure python@0 first |
| G3 | pymrm solve.py: class-state smell fires on _NewtonRun; python@1 display health <= 8.0 (blind review 6.5, CodeScene 4.70, python@0 9.4) | 9.4 |
| G4 | class-state smell precision: a blind reviewer agrees with >= 14 of 20 flagged classes sampled from OpenFlowsheet + pymrm | n/a |
| G5 | docstring lines count in neither function nloc nor file nloc (tests) | already true for python@0 |
Gates the design pass adds are appended below, before their runs.

Added by the think design pass 2026-11-17 (docs/py1/design.md; prototype numbers in docs/py1/proto/):

| gate | criterion | python@0 reference |
|---|---|---|
| G6 | bumpy road, OFS a9e733e4, all Python files with a CodeScene score: functions with bumps >= 2 within 10 % of CodeScene's 1088 (979..1197) and per-file sum abs diff <= 420 | 562 functions, sum abs diff 530 (prototype 1105, 383) |
| G7 | complex conditional, same files: functions with boolops >= 2 vs CodeScene Complex Conditional, per-file sum abs diff <= 150 | 579 (prototype 120) |
| G8 | ratchet tests: (a) a step extracting helpers that communicate through 2 new self fields fails debt_check with "class state rose" although debt falls; (b) the same with an allow comment on the helper still fails; (c) moving __init__ code into a private helper called only from __init__ passes; (d) turning a channel into a local or a returned NamedTuple passes and lowers state_writes; (e) (a) in optimize mode gives a note, not an error | n/a |
| G9 | clones: solve.py @230bbc3 groups cover 662-665/703-706 and 693-696/865-869; OFS src files with clones <= 50 of 277 | prototype 42 |
| G10 | formatting stability: after reformatting pymrm src with ruff format at line length 60 and 120, every python@1 metric except nloc is identical per function (0 differences) | n/a |
| G11 | Goodhart replay: re-gate each accepted pymrm c1 step (debt check only) under python@1: >= 1 step fails on class state, and each failing step's diff shows new self fields that methods use to communicate | n/a |
| G12 | cost: python@1 scan wall time on OFS a9e733e4 <= 1.5 x python@0 | measure python@0 first |
| G4' | blind reviewer (flags hidden) rates 20 classes: all flagged + highest unflagged by state_writes, shuffled. Pass: >= 5 of 6 flagged rated real shared-state cost, <= 3 unflagged rated so with high severity. Replaces G4 (decided 2026-11-17 before any run) | n/a |
| G4 note | G4 cannot run as written: 6 classes exceed the flag (state_writes > 8) across five local repos. Proposed G4' in docs/py1/design.md section 8; the orchestrator or user decides | n/a |

## Position

2026-11-17: design adopted (docs/py1/design.md, decisions logged). Next: implement in worktree
~/Code/cce-code-health-py1 (branch py1), brief docs/briefs/py1-implement.md; then G0, G5..G12, then
G1..G4' on the frozen candidate commit.
Running: implement agent (Opus) on brief docs/briefs/py1-implement.md since 2026-11-17, worktree
~/Code/cce-code-health-py1 branch py1. If this session resets while it runs: check
`git -C ~/Code/cce-code-health-py1 log --oneline main..py1` and docs/py1/results.md on py1; do not
start a second implementer on the same tree. After it: review the diff (think, multi-module), run
G11 and G1..G4' yourself, then merge py1 to main (no squash) and give pymrm-health the commit.

Consumer: pymrm c2 starts as soon as python@1 is pinnable (send pymrm-health the commit). Order
numjac.py, coupling.py, convect.py, then solve.py _NewtonRun (PR #48 merged as pymrm 13176bb; run G3 on that commit). c2 needs:
python@1 selectable via `[profiles] python = "python@1"` on a new campaign base, and the gate in
refactor mode scoring python@1 debt AND the class-state smell (so it must reach the gate as debt or
as a file/class smell the gate checks).

## Gate results (orchestrator, 2026-11-17, py1 at aa2f95e)

- Implementer (Opus): G0 PASS (8/8 byte-identical; my spot-check on pymrm 230bbc3..13176bb diff also
  identical), suite 540 PASS, G5 G8 PASS, G6 PASS 1105 / sum 383, G7 PASS 122, G9 PASS (42 files),
  G10 PASS 0 diffs, G12 PASS 1.19.
- G1 PASS: OFS rho(-debt) tests 0.84, scripts 0.95, docs 0.95, benchmarks 0.93, src 0.94 (all >= py0).
- G2 PASS: pymrm src (14 scored) rho(-debt) 0.75 -> 0.91 (CodeScene 8475071 = pymrm 230bbc3; same at 13176bb).
- G3 PASS: solve.py @13176bb class_state on _NewtonRun (54), health 4.5 (<= 8.0; below CodeScene 4.70
  and the blind 6.5: python@1 is harsher than both on this file).
- G11 PASS: 92 c1 steps, 7 fail the ratchet (65ee7c9, 1298ad2, bc09685 build _NewtonRun 0->54;
  655baaf _Reconstructor 14; 6e3c8fc +1). Known weakness: de4fcf8 (_Continuation 5, rated very good)
  and c167027 (_PseudoTransient 4) also fail: lifting closures with nonlocal state into a class makes
  invisible state visible. Script scratchpad g11/replay.py.
- G4' running (blind think review, 20 classes, key in scratchpad g4/key.json). think diff review running.

## Open decisions (default in force until changed)

- Allow-comment loophole (found by think): the gate does not refuse new `# codehealth: allow` comments
  in a step, so a loop can suppress function debt. Proposed rule: valid allows over touched files may
  not rise in refactor mode. Affects every profile and adopter, so NOT in this campaign by default;
  raise with adopters after python@1 ships. The class-state ratchet counts raw values regardless.
