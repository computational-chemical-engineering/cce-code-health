# cce-code-health: state

Objective: build an agentic refactor loop (agent + skills + measurement tools) that raises
code health of a Python repo to ~10 without changing behaviour, building its own playbook;
importable into any repo. Public repo-to-be: computational-chemical-engineering/cce-code-health (MIT, 0.x experimental). Design: docs/DESIGN.md; decisions: docs/DECISIONS.md. Demo target: OpenFlowsheet, judged before/after by CodeScene
(free Community Edition, public repo), loop itself optimises open-source metrics.

Position (2026-11-11):
- Public repo https://github.com/computational-chemical-engineering/cce-code-health (main).
- Package v0.1.0 (src/cce_code_health): CLI `codehealth scan|diff|hotspots`, debt metric
  profile python@0, base-vs-head ratchet with base config, rename-following hotspots,
  inline suppressions. 13 tests (tests/). docs/DESIGN.md is draft 1 (after think review).
- baseline.py = superseded prototype (capped score); baselines/openflowsheet-a9e733e4 kept.
- OpenFlowsheet@a9e733e4 scan: 1191 files, 422.5k NLOC, debt 26594 (62.9/kNLOC), health
  7.17, brain 158; src debt 6950, health 7.04. diff HEAD~50..HEAD: debt over touched
  files 1418.5 -> 2014.8, 6 failures. Scan 17 s, diff 5.5 s.

OpenFlowsheet adopter findings: docs/adopters/openflowsheet.md (F0 identity gate 40 s,
35 frozen modules, tiered gate proposal). Calibration: docs/CALIBRATION.md (rho 0.87, src 0.90).
Coverage/xdist in OpenFlowsheet: user wants it; handed to session OpenFlowsheet-TR09 (2026-11-11), awaiting its plan. Hub informed of this project. Open with user: first scope application/.

Next action: `gate` with tiers in config (behaviour gate: base-regenerated references, determinism check,
bitwise compare, mutant coverage check), then `playbook stats`, then plugin skills
(setup, refactor-loop, healthy-coding) + reusable workflow. User: run CodeScene on
OpenFlowsheet (needed for python@1 calibration and demo).

Open decisions: scope of refactor (src only vs also tests/scripts/docs); CodeScene CLI access.
