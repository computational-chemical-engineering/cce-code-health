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
Coverage/xdist in OpenFlowsheet: user wants it; OpenFlowsheet-TR09 plan received approved as wp/S16, starts after budget reset 2026-11-12 13:00; see adopter notes. Hub informed of this project. First scope confirmed: application/ (campaign after S16 merge).

codehealth gate built (static rules, command and reference tiers, cadence, trial --config); OpenFlowsheet trial: F0 misses an application/ behaviour change, so impacted tests (S16) are required.
Gate hardened after think review 2026-11-12 (55 tests; see DECISIONS). Skill optimize-loop updated (self-test precondition, interim noise rule).
Decisions 11-14 taken 2026-11-12; 11-13 implemented (--ref-base with cached base outputs, head twice; speedup lower confidence bound with extended rounds; numeric-literals report; coverage = true per reference tier). Adopter fixes from OpenFlowsheet (self-test baseline, config --check, prefix, glob). 69 tests. 14 when Peclet is onboarded.
Skill refactor-loop + seed recipes written 2026-11-12; self-test counts behaviour command tiers when no reference tier (lint tiers: behaviour = false). 56 tests.
OpenFlowsheet: S16 running since ~12:00 (wp/S16, opus-engineer, incl. run/select --lines/recall). Campaign = S17 (wp/S17): first work order installs codehealth (.venv-codehealth from requirements-codehealth.txt pinned to a full SHA), .codehealth/config.toml (my draft + frozen: application/bindings/descriptions/*, schemas/*, messages/catalogue/*, benchmarks/f0/*; MCP digest tier = pytest tests/test_t08_w2_surface_digest.py tests/test_m07a_surface_chain.py), informational CI diff job; starts when S16 merged. S18 = scale probe. Identity-epoch question answered after campaign 1.
Versioning: setuptools-scm since cec6dbeb (each commit a distinct pip version). OpenFlowsheet S17 pinned at 7628e737 (wp/S17 b63c9c73, their D10; five tier timeouts, config --check OK); campaign 1 starts when S16 recall is clean.
Tier processes run in their own process group; timeout or interrupt kills the whole group, stragglers of finished tiers are killed too (reported by OpenFlowsheet-TR09 2026-11-12, xdist worker survived a hung test). Skills require a timeout per behaviour tier. 72 tests.
Still to build: skills setup, healthy-coding; playbook stats.

Next action (old): `gate` with tiers in config (behaviour gate: base-regenerated references, determinism check,
bitwise compare, mutant coverage check), then `playbook stats`, then plugin skills
(setup, refactor-loop, healthy-coding) + reusable workflow. User: run CodeScene on
OpenFlowsheet (needed for python@1 calibration and demo).

Open decisions: scope of refactor (src only vs also tests/scripts/docs); CodeScene CLI access.
