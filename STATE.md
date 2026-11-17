# cce-code-health: state

Rewritten in place, about one screen. History: docs/STATE_LOG.md (append-only, grep it).
Design: docs/DESIGN.md. Decisions: docs/DECISIONS.md. Adopters: docs/adopters/*.md.

Objective: an agentic refactor loop (`codehealth` CLI + Claude Code plugin with the
refactor-loop, optimize-loop and healthy-coding skills) that raises code health without
changing behaviour and builds its own playbook; importable into any repo.
Public repo computational-chemical-engineering/cce-code-health (MIT, 0.x), main.

## Position (2026-11-16)

- Tagged v0.2.0 (plugin 0.2.0). main at 534fa62, 508 tests.
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

Campaign cpp@1 + zones (peclet-health request 2026-11-17; the user approved it in that session;
cpp@0 stays frozen and stays Peclet's gate). Design: docs/CPP1_LAYERS.md; decision rows 2026-11-17
in docs/DECISIONS.md. ALL FOUR cpp@1 RULES SHIPPED: paths 1bed7b9, markers 317db57, rule (1)
bee116e, (2) 5110771, (3) 8b03640, (4) captures 534fa62 (think-reviewed; capture count alone >8 w0.5,
not params+captures; display smell many_captures; profile field kernel_this_macros). Peclet nine
clones cpp@1 9678.2 / 62 brains (captures debt 514.5; median 4, p90 11, max 28).
Next: wait for peclet-health's own nine-repo cpp@1 trial table; then release cpp@1 (freeze; a tag
or release needs the user's go or a VERIFIED Desk card). Meanwhile, optional (not started): gate
`zones` as data (debt_check out-param) for zone_ratchet/log; hide unchanged zone rows in diff text;
rule (3) open notes (brain match by bare name, --target ambiguity, lambda#k renumbering).
Scratch tools (rebuild if gone): cpp@1 trial config
/tmp/claude-1000/-home-eajfpeters-Code/f09cf2c9-1eb8-4cbc-bc24-43d3e6ca2a4f/scratchpad/c1.toml (base.toml with cpp@1);
byte-identity script /tmp/claude-1000/-home-eajfpeters-Code/4c980d79-9d8f-4423-bf32-2b51015d63c9/scratchpad/ident.sh OUTDIR
(needs PATH=.venv/bin; reference in its before/); Peclet clones
/tmp/claude-1000/-home-eajfpeters-Code/6acb3080-ac42-4dda-bdfb-676e2b649813/scratchpad/peclet (re-clone with
`gh repo clone computational-chemical-engineering/peclet-<name> -- --depth 1`; cpp@0 baseline base.toml: debt 10899.5,
brain 89); conservation /tmp/claude-1000/-home-eajfpeters-Code/54215b1c-269c-47f3-8708-9fa8bc19bd46/scratchpad/conserve.py PECLET_DIR;
capture stats /tmp/claude-1000/-home-eajfpeters-Code/04d9439e-3ffc-4be1-b736-2e124d39fd21/scratchpad/capstats.py SCANDIR PECLET_DIR.
Run pytest as `.venv/bin/python -m pytest` (a PATH= prefix breaks 49 tier tests: exit 127).
Config key name `[[zones]]` is a DEFAULT pending the user (see Open requests); key in one place in config.py.

## Open requests

- peclet-health: cpp@1 + zones; pins 1bed7b9 (paths) and 317db57 (markers, with nine-repo zone
  table) sent 2026-11-17; bee116e (cpp@1 rule 1), 5110771 (rule 2) and 8b03640 (rule 3) and 534fa62 (rule 4, last rule) sent 2026-11-17 (bridge delivery unconfirmed).
  It trials at its next round boundary (WO-28 on dem running) and sends its own nine-repo table,
  which decides the cpp@1 release.
- User (via hub): public name of the zones config key (default `[[zones]]`). Desk card
  `decisions/cce-zones-name` (hub agrees with A); act only on a VERIFIED sign-off. Asked hub to
  extend the card to the output keys: card v3 options A keep zones/zone/zones, B layers/layer/layers,
  C groups/group/group_zones (scan already has `groups`). Rename all in one commit when VERIFIED.

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
