# Calibration against CodeScene

## 2026-11-11: OpenFlowsheet @ a9e733e4, profile python@0 (uncalibrated)

CodeScene project 87669, analysis 8385360 (same commit). Data:
`baselines/openflowsheet-a9e733e4/codescene_files.json`; script:
`calibration/compare_codescene.py compare <codescene.json> <codehealth scan --json>`.

Project level: CodeScene weighted code health 7.07 (codehealth display health 7.18).
Hotspot code health 4.43 now, 5.81 a month earlier.

Spearman rank correlation of CodeScene per-file score with codehealth, 1137 Python files:

| area | n | vs display health | vs −debt |
|---|---|---|---|
| all | 1137 | 0.81 | 0.87 |
| src/ | 250 | 0.83 | 0.90 |
| tests/ | 594 | 0.62 | 0.72 |
| scripts/ | 135 | 0.85 | 0.92 |
| docs/ | 84 | 0.93 | 0.93 |
| benchmarks/ | 48 | 0.90 | 0.91 |

Target was ≥ 0.7: met everywhere for debt, before any fitting. Three worst src files
are the same in both (orchestrator/region.py, models/syn001/column.py,
studies/trust_region/projection.py).

Gaps (CodeScene smells without a codehealth counterpart): Low Cohesion / Potentially Low
Cohesion (107), Many Conditionals (285), Brain Class, Modularity Issue, trend smells.
Under-rated by debt: application/local.py (CodeScene 3.97, debt 37.7, 33 commits),
likely cohesion and argument counts.

Caveat for the demo: the calibration set includes src/. Fitting python@1 should use
files outside the refactor scope (tests, scripts, benchmarks, docs) and validate on src.
