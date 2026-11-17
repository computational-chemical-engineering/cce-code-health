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

## Position

2026-11-17: campaign opened. Next: think design pass (brief docs/briefs/py1-design.md).

Consumer: pymrm c2 starts as soon as python@1 is pinnable (send pymrm-health the commit). Order
numjac.py, coupling.py, convect.py; solve.py _NewtonRun only after pymrm PR #48 merges. c2 needs:
python@1 selectable via `[profiles] python = "python@1"` on a new campaign base, and the gate in
refactor mode scoring python@1 debt AND the class-state smell (so it must reach the gate as debt or
as a file/class smell the gate checks).

## Open decisions (default in force until changed)

- none yet
