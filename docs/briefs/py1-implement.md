# Brief (implement): python@1 profile, per the adopted design

Do not spawn sub-agents yourself. Report in at most 30 lines; numbers go in docs/py1/results.md.

## Task

Implement the python@1 profile in the codehealth tool exactly as specified in
~/Code/cce-code-health/docs/py1/design.md, sections 1 to 5 and 7 (read the whole file first; it is
the spec). The prototypes in docs/py1/proto/ (measure.py, clones.py) compute the same metrics and
are the reference for edge cases. python@1 is opt-in through `[profiles] python = "python@1"`;
python@0 must stay byte-identical.

## Where

- Create a worktree: `git -C ~/Code/cce-code-health worktree add ~/Code/cce-code-health-py1 -b py1 main`
  (main is at 645af5c or later). Work only there.
- Its own venv (the main venv's editable install points at the main tree):
  `cd ~/Code/cce-code-health-py1 && python3 -m venv .venv && .venv/bin/pip install -q -e '.[test,cpp]'`.
- Run tests as `.venv/bin/python -m pytest -q > $LOG 2>&1; tail -1 $LOG` with LOG in
  /tmp/claude-1000/-home-eajfpeters-Code/76c895df-cc60-4b48-9e1f-e7dee45b0fb5/scratchpad/.
  Do not prefix PATH= (it breaks 49 tier tests). Read logs only with grep/tail on failure.

## Facts

- Files to change (design s7): src/cce_code_health/profiles.py, python_backend.py, cli.py
  (`_analyze`), report.py, gate.py (`debt_check`: class-state ratchet with mode; diff gets the same),
  new src/cce_code_health/classstate.py and pyclones.py. README: document python@1 (metrics,
  thresholds, class_state smell, the ratchet and its modes, `public` field). Update the skill docs in
  the repo's plugin directory wherever they list python@0 metrics or gate rules.
- Profile thresholds and weights (design s1), do not change them: cognitive>9 w1, nloc>70 w0.1,
  nesting>3 w3, bumps>1 w2 (run definition, s2), params>4 w1, boolops>1 w1 (conditions only),
  state_writes>0 w0.5, clones>0 w0.2. Class flag state_writes>8. Brain, file sizes, cross-file
  duplication, complexipy 8.3.0 unchanged. The held-out validation sets were already looked at during
  design; any threshold or weight change spends them. If a check below misses, REPORT the miss with
  numbers; do not tune.
- SMELL_NAMES / allow names for python@0 must not change (python@0 suppressions[].valid).
- Ratchet modes: error in refactor, restructure and interface modes; note in optimize mode. Raw
  values, allow comments ignored. Message format as in design s3(b).

## Acceptance (report each as PASS/FAIL with the number)

1. G0, first, before any code change: produce python@0 reference outputs with the worktree's
   venv while the branch still equals main:
   `codehealth scan --repo ~/Code/OpenFlowsheet --ref a9e733e4 --json OFS.json --group-depth 2`,
   the same for `~/Code/pymrm_suite/pymrm --ref 13176bb` and for `~/Code/cce-code-health --ref
   cf15245`, plus `codehealth diff --repo ~/Code/cce-code-health --base cf15245~30 --head cf15245
   --json selfdiff.json`; keep stdout too. Replace `"version": "..."` values with "X". After the
   change, rerun and `cmp` every file: all byte-identical. Never write to the OpenFlowsheet or pymrm
   repos (scan --ref is read-only; other sessions work there).
2. Full test suite passes (report count; main has 512).
3. New tests: G5 (docstring lines in neither function nor file nloc under python@1); G8 (a) to (e)
   exactly as in docs/PY1_STATE.md; unit tests for bump runs, condition-only boolops, channel rules
   (constructor helpers, setters, staticmethod, dict keys, setattr), and clones.
4. G9: on `git -C ~/Code/pymrm_suite/pymrm show 230bbc3:src/pymrm/solve.py`, clone groups cover
   662-665/703-706 and 693-696/865-869; OFS a9e733e4 src files with clones <= 50 of 277.
5. G6 and G7 with the data in ~/Code/cce-code-health/baselines/openflowsheet-a9e733e4/ (see
   docs/py1/proto/measure.py for how the prototype counted): bumpy-road functions 979..1197 and
   per-file sum abs diff <= 420; complex conditional per-file sum abs diff <= 150.
6. G10: copy pymrm src at 13176bb to the scratchpad, `ruff format` (pip install ruff into the venv)
   at line length 60 and at 120: every python@1 function metric except nloc identical (0 diffs).
7. G12: python@1 scan wall time on OFS a9e733e4 <= 1.5 x python@0 (report both).
8. pymrm solve.py at 13176bb under python@1: report _NewtonRun state_writes, class_state flag, file
   display health (G3 needs flag on and health <= 8.0; report, do not tune).

## Git

Commit on branch py1 in logical steps; stage named paths only, never `git add -A`. End each commit
message with:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015AcoezXz5dwiVjCQRxqDJD
Do not push, do not merge, do not tag. Write all numbers to docs/py1/results.md on the branch.
No em dashes in any text.
