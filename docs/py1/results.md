# python@1 implementation: acceptance results (2026-11-17)

Brief: docs/briefs/py1-implement.md. Branch py1, worktree ~/Code/cce-code-health-py1, own venv.
Scripts: docs/py1/check/ (g0.sh, g6g7g9.py, g10.py). Trees were read with `git archive` and
`scan --ref`; nothing was written to OpenFlowsheet or pymrm.

| # | check | criterion | result | verdict |
|---|---|---|---|---|
| 1 | G0 python@0 byte-identical | 8 files (OFS, pymrm, self scans JSON + stdout, selfdiff JSON + stdout) | 8 identical, 0 differ (reference taken before any code change; rerun after the last code commit) | PASS |
| 2 | full suite | passes (main 512) | 540 passed (512 + 28 new in tests/test_py1.py) | PASS |
| 3 | new tests | G5, G8 (a)-(e), bump runs, condition boolops, channel rules, clones | tests/test_py1.py, all pass | PASS |
| 4 | G9 clones | solve.py@230bbc3 groups 662-665/703-706 and 693-696/865-869; OFS src files with clones <= 50 of 277 | groups [(662,665),(703,706)] and [(693,696),(865,869)]; 42 of 277 | PASS |
| 5a | G6 bumpy road | functions with bumps >= 2 in 979..1197, per-file sum abs diff <= 420 | 1105 (CodeScene 1088), sum abs diff 383 (1138 files) | PASS |
| 5b | G7 complex conditional | per-file sum abs diff <= 150 | 122 (ours 442, CodeScene 550) | PASS |
| 6 | G10 formatting | 0 metric differences after ruff format at 60 and 120 | 367 functions, 0 differences at 60, 0 at 120 (cognitive, nesting, bumps, params, boolops, state_writes, clones, public) | PASS |
| 7 | G12 cost | python@1 scan <= 1.5 x python@0 on OFS a9e733e4 | python@0 22.47, 22.62, 22.82 s; python@1 27.03, 26.90, 26.86 s; ratio 1.19 (means 22.64, 26.93) | PASS |
| 8 | pymrm solve.py @13176bb | report (G3: flag on, health <= 8.0) | _NewtonRun state_writes 54 (35 channels), class_state flag on, solve.py display health 4.5 (python@0 9.4), debt 110.2 (python@0 25.0) | reported (meets G3's numbers) |

Python@0 reference values reproduced by the same scripts (they match the prototype): G6 562
functions, sum abs diff 530; G7 1021 functions, sum abs diff 579.

Notes
- G12 first measured 1.50 (python@0 22.6 s, python@1 34.0 s). Commit "cheaper measurement" removed
  repeated AST walks (python@0 bumps and boolops computed and then replaced, constructors computed
  three times per class, clone window sizes recounted, expressions visited for statement lists).
  G6, G7 and G9 outputs are identical before and after; the OFS python@1 scan totals too (debt
  41947.7, 3642 functions with debt).
- G7: 122 vs the prototype's 120. The design counts match guards; the prototype did not.
- Bumps walk a try's parts in source order (body, handlers, orelse, finalbody), as design s2 says;
  the prototype walked handlers last. G6 numbers equal the prototype's (1105, 383).
- Whole-repo scans (trial config, all files): OFS python@0 debt 26489.5, health 7.19; python@1 debt
  41947.7, health 6.35. pymrm 13176bb python@0 debt 223.0, health 9.67; python@1 625.6, 8.48.
- hook.py also passes the profile's py_rules (otherwise the edit hook crashes under python@1) and
  lists the profile's own metrics.
