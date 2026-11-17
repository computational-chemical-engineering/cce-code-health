# cce-code-health: state

Rewritten in place, about one screen. History: docs/STATE_LOG.md (append-only, grep it).
Design: docs/DESIGN.md. Decisions: docs/DECISIONS.md. Adopters: docs/adopters/*.md.

Objective: an agentic refactor loop (`codehealth` CLI + Claude Code plugin with the
refactor-loop, optimize-loop and healthy-coding skills) that raises code health without
changing behaviour and builds its own playbook; importable into any repo.
Public repo computational-chemical-engineering/cce-code-health (MIT, 0.x), main.

## Position (2026-11-17)

- Tagged v0.3.0 2026-11-17 at a2c3829 (plugin 0.3.0; cpp@1 frozen, captures > 12; zones). main 240294e, 512 tests.
- Work is reactive: adopter sessions report tool issues; each fix is committed, pushed to
  main, the commit id sent back, and one line appended to docs/STATE_LOG.md.
- Adopters and where they stand:
  - OpenFlowsheet S19 (repo-wide, W4 interface lane): re-pins to cf15245 for I-12a/I-12c (text
    callers follow `from M import` moves; private names may be renamed); `[gate] no_implicit_reexport` set. Nothing open.
  - pymrm c1 closed locally (debt 1282.8 -> 80.0, health 5.82 -> 9.57); its push waits
    for a Desk sign-off. Its trial-config report fixed in cc00407. Lane c1-opt: numba reports fixed
    2026-11-17 (5d9f1a0 bench NUMBA_CACHE_DIR, 240294e coverage of called numba functions); it re-gates opt-O1b.
  - Peclet (v0.3.0 pinned 2026-11-17, baseline cpp@1 9619.1 / 64; gates switch to cpp@1 with its next WO): session peclet-health (TRS2) replaces
    peclet_health and plans suite-wide work; status digest sent 2026-11-17 (per-package numbers,
    cpp@0 limits, cpp@1 ideas for device code). Builds and tests on TRS2 only through it. Nothing open.

## Next action

Campaign cpp@1 + zones CLOSED: v0.3.0 tagged 2026-11-17 at a2c3829 (Desk card cce-v030-release VERIFIED A,
executed). cpp@1 is frozen (any change needs cpp@2). Peclet sent the v0.3.0 pin; it switches its gate
from cpp@0 (719092b) to cpp@1 when it chooses. Work is reactive again (adopter reports).
Optional, not started: gate `zones` as data (debt_check out-param) for zone_ratchet/log; hide
unchanged zone rows in diff text; rule (3) open notes (brain match by bare name, --target
ambiguity, lambda#k renumbering).
Scratch tools (rebuild if gone): cpp@1 trial config
/tmp/claude-1000/-home-eajfpeters-Code/f09cf2c9-1eb8-4cbc-bc24-43d3e6ca2a4f/scratchpad/c1.toml (base.toml with cpp@1);
byte-identity script /tmp/claude-1000/-home-eajfpeters-Code/4c980d79-9d8f-4423-bf32-2b51015d63c9/scratchpad/ident.sh OUTDIR
(needs PATH=.venv/bin; reference in its before/); Peclet clones
/tmp/claude-1000/-home-eajfpeters-Code/6acb3080-ac42-4dda-bdfb-676e2b649813/scratchpad/peclet (re-clone with
`gh repo clone computational-chemical-engineering/peclet-<name> -- --depth 1`; cpp@0 baseline base.toml: debt 10899.5,
brain 89); conservation /tmp/claude-1000/-home-eajfpeters-Code/54215b1c-269c-47f3-8708-9fa8bc19bd46/scratchpad/conserve.py PECLET_DIR;
capture stats /tmp/claude-1000/-home-eajfpeters-Code/04d9439e-3ffc-4be1-b736-2e124d39fd21/scratchpad/capstats.py SCANDIR PECLET_DIR.
Run pytest as `.venv/bin/python -m pytest` (a PATH= prefix breaks 49 tier tests: exit 127).
Config key name `[[zones]]` (and output keys zone/zones) is final: user chose A on Desk card cce-zones-name (VERIFIED, executed).

## Open requests

- pymrm-health (2026-11-17): CodeScene scores pymrm solve.py 4.70, python@0 says 9.4 (debt 25, params only).
  It runs a blind review and will recommend (likely python@1 calibrated per file). Acked: python@0 frozen,
  python@1 queue (LCOM4, complexipy 9, nesting/conditional terms); priority goes to the user via Desk.
  Data: calibration/compare_codescene.py fetch 87687.
- (peclet-health: v0.3.0 pin sent 2026-11-17, bridge delivery unconfirmed; user-facing rounds
  gated with `gate --behaviour-only --mode refactor` + `diff`, no new mode wanted.)

## Later (not started)

CodeScene calibration (user: later); benchmark tiers that skip or relax when the step's
changed code is not run by the workload (pymrm c1: bench false rejections under load); restructure-mode warning for tests that patch a moved
name; docs-sync review lane, exemplars, C++ D1/D2 and public-header
rules; restructure with build-file edits; the flaky
tests/test_interface.py::test_exact_plan_passes_and_logs_interface (seen once, "HEAD's tree
differs from the tree the gate judged"); possibly a python@1 profile for complexipy 9.

## Key anchors

- Gate: src/cce_code_health/gate.py (`gate`, `_benchmark`, `_fill`, `literal_drops`).
- Interface rules: src/cce_code_health/interface.py (`public_api_problems`,
  `old_path_importers`, `_callers`).
- Clone detection: src/cce_code_health/duplication.py. Config keys: config.py.
- Backend pins: pyproject.toml (complexipy==8.3.0 for python@0, lizard==1.26.0 for cpp@0).
