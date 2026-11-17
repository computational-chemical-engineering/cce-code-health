# Brief (think, design advice): python@1 profile

Do not spawn sub-agents yourself. Advice only: do not edit files under src/ or tests/.
Write your answer to docs/py1/design.md (at most about 150 lines) and reply with a 20-line summary.

## Question

Design python@1, a new Python metric profile for the codehealth tool (repo ~/Code/cce-code-health,
main). python@0 stays frozen (thresholds, weights, measurement, output bit-identical); python@1 is
opt-in through `[profiles] python = "python@1"`. The user signed: start now, with docstring lines
excluded from function and file size. Goal: python@1 ranks and flags files the way CodeScene and
blind expert reviews do, without reproducing CodeScene's score, and adds a class-state smell that
neither tool sees. Decide, with reasons: (1) thresholds and weights, (2) the bumpy-road definition,
(3) the class-state smell (definition, how it enters debt or file smells, false-positive guards),
(4) short-block duplication, (5) public vs private parameters, (6) calibration and validation
procedure (which files fit, which are held out), (7) any extra gates with numbers, to append to the
gate table in docs/PY1_STATE.md. Name the files and functions each change touches.

## Established facts

- Profile and debt: src/cce_code_health/profiles.py (87 lines). Debt per function
  = sum_m w_m * max(0, value_m - threshold_m). python@0: cognitive>15 w1, nloc>70 w0.1, nesting>3 w3,
  bumps>2 w2, params>5 w1, boolops>2 w1; brain = cognitive>40 and nloc>70; varargs count 3 params;
  large file nloc>1000, huge>2000; duplication window 8 normalised lines, file smell above 10 %.
- Measurement: src/cce_code_health/python_backend.py (182 lines). Cognitive from complexipy 8.3.0
  (pinned). `nesting_depth`, `bumps` (line 86: top-level body statements that reach nesting >= 2),
  `boolops` (largest operator count in one condition), `params`, `code_lines` (nloc already excludes
  blank, comment and docstring lines). Display score and smells: src/cce_code_health/report.py.
- Cohesion: src/cce_code_health/cohesion.py, LCOM4 per class, informational, never in debt. DECISIONS
  2026-11-13 queues "LCOM4 into debt in python@1".
- Duplication: src/cce_code_health/duplication.py (window-based, docstrings and imports excluded).
- Gate (src/cce_code_health/gate.py): touched-file debt may not rise, no new brain function, no new
  file smell. A refactor loop optimises against these numbers (Goodhart risk: pymrm c1 did).
- DECISIONS 2026-11-11: the loop optimises open-source metrics; CodeScene only judges.
- Calibration so far: docs/CALIBRATION.md. OpenFlowsheet a9e733e4 vs CodeScene 8385360, 1137 files,
  Spearman(CodeScene, -debt) src 0.90, tests 0.72, scripts 0.92, docs 0.93, benchmarks 0.91.
  Data baselines/openflowsheet-a9e733e4/codescene_files.json; script
  calibration/compare_codescene.py (compare | fetch <project>). CodeScene gaps there: Low Cohesion
  (107 files), Many Conditionals (285), Brain Class, Modularity Issue. Advice in that file: fit on
  files outside the refactor scope, validate on src.
- pymrm (post-campaign c1, optimised against python@0): per-file table in
  ~/Code/pymrm_suite/worktrees/pymrm-health-notes/codescene_vs_python0.md (read it). At CodeScene-like
  thresholds (args>4, boolops>=2, cognitive>=10, function length incl. docstring >70) our counts match
  CodeScene's closely; bumpy road is under-counted (solve 5 vs 2, numjac 5 vs 2); CodeScene Low
  Cohesion in numjac, coupling, convect where our max LCOM4 is 1. solve.py: CodeScene 4.70, python@0 9.4.
- Blind review of pymrm src/pymrm/solve.py (think, no scores shown):
  ~/Code/pymrm_suite/worktrees/pymrm-health-notes/solve_py_blind_review.md (read it). 6.5/10; real
  cost is ~28 init=False mutable dataclass fields plus a parallel state dict through which small
  methods communicate; c1's extractions moved complexity into shared state; LCOM4 rates it cohesive.
  The step/commit sequence is written 3x and was not caught by duplication.
- pymrm-health's proposal for python@1: cognitive 10; params 4 for private functions, public
  (frozen) signatures reported but not charged in step debt; boolops flag at 2; bumps counted like
  CodeScene; class-state smell = mutable instance attributes written by >= 3 methods (write fan-in),
  dataclass fields with init=False, dict-as-struct (dict attribute with fixed literal keys read and
  written across methods), positional reads of tuples returned by own methods; short-block
  duplication; docstrings out of size; validate per file against CodeScene and blind reviews, also on
  post-campaign code; playbook anti-pattern "extract method that communicates through new mutable
  fields". Treat this as input, not as settled.
- Other python@1 candidates on record: complexipy 9 (STATE "Later"), Many Conditionals.

## Constraints

- python@0 must stay bit-identical. A frozen profile is never edited later; get python@1 right.
- Every new metric must be computable from the AST of one file (no cross-file analysis), cheap,
  and stable under formatting.
- No em dashes in any text.
