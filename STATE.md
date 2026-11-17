# cce-code-health: state

Rewritten in place, about one screen. History: docs/STATE_LOG.md (append-only, grep it).
Design: docs/DESIGN.md. Decisions: docs/DECISIONS.md. Adopters: docs/adopters/*.md.

Objective: an agentic refactor loop (`codehealth` CLI + Claude Code plugin with the
refactor-loop, optimize-loop and healthy-coding skills) that raises code health without
changing behaviour and builds its own playbook; importable into any repo.
Public repo computational-chemical-engineering/cce-code-health (MIT, 0.x), main.

## Position (2026-11-16)

- Tagged v0.2.0 (plugin 0.2.0). main at cf15245, 416 tests.
- Work is reactive: adopter sessions report tool issues; each fix is committed, pushed to
  main, the commit id sent back, and one line appended to docs/STATE_LOG.md.
- Adopters and where they stand:
  - OpenFlowsheet S19 (repo-wide, W4 interface lane): re-pins to cf15245 for I-12a/I-12c (text
    callers follow `from M import` moves; private names may be renamed); `[gate] no_implicit_reexport` set. Nothing open.
  - pymrm c1 closed locally (debt 1282.8 -> 80.0, health 5.82 -> 9.57); its push waits
    for a Desk sign-off. Its trial-config report fixed in cc00407. Nothing open.
  - Peclet (dem landed, flow F1-F3): session peclet-health (TRS2, from 2026-11-17) replaces
    peclet_health and plans suite-wide work; status digest sent 2026-11-17 (per-package numbers,
    cpp@0 limits, cpp@1 ideas for device code). Builds and tests on TRS2 only through it. Nothing open.

## Next action

Campaign cpp@1 + layers (peclet-health request 2026-11-17, approved by the user in that
session's chat; cpp@0 stays frozen and stays Peclet's gate). Order: layer reporting first
(matters most to Peclet), then cpp@1. Design brief docs/briefs/cpp1-layers-design.md sent to
`think` (Opus xhigh, advice only); its answer goes to docs/CPP1_LAYERS.md before any code.
Calibration data: shallow clones of the nine Peclet repos in the session scratchpad
(.../scratchpad/peclet/peclet-<name>; re-clone with `gh repo clone ... -- --depth 1` if gone);
cpp@0 baseline there with scratchpad/peclet/base.toml: total debt 10899.5, brain 89
(core 798.2, geom 22.0, amr 1064.0, morton 11.0, flow 5614.9, pnm 319.3, dem 1485.8,
voro 1438.9, coupling 145.4). Peclet's own suite scan: 11162.9, 91 brain (its scope).
Asked peclet-health for its suite-scan.toml scope and its layer globs.

## Open requests

None from the user or from other sessions.

## Later (not started)

CodeScene calibration (user: later); benchmark tiers that skip or relax when the step's
changed code is not run by the workload (pymrm c1: bench false rejections under load); restructure-mode warning for tests that patch a moved
name; cpp@1 (`[&]` captures); docs-sync review lane, exemplars, C++ D1/D2 and public-header
rules; restructure with build-file edits; the flaky
tests/test_interface.py::test_exact_plan_passes_and_logs_interface (seen once, "HEAD's tree
differs from the tree the gate judged"); possibly a python@1 profile for complexipy 9.

## Key anchors

- Gate: src/cce_code_health/gate.py (`gate`, `_benchmark`, `_fill`, `literal_drops`).
- Interface rules: src/cce_code_health/interface.py (`public_api_problems`,
  `old_path_importers`, `_callers`).
- Clone detection: src/cce_code_health/duplication.py. Config keys: config.py.
- Backend pins: pyproject.toml (complexipy==8.3.0 for python@0, lizard==1.26.0 for cpp@0).
