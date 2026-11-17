# cce-code-health: state

Rewritten in place, about one screen. History: docs/STATE_LOG.md (append-only, grep it).
Design: docs/DESIGN.md. Decisions: docs/DECISIONS.md. Adopters: docs/adopters/*.md.

Objective: an agentic refactor loop (`codehealth` CLI + Claude Code plugin with the
refactor-loop, optimize-loop and healthy-coding skills) that raises code health without
changing behaviour and builds its own playbook; importable into any repo.
Public repo computational-chemical-engineering/cce-code-health (MIT, 0.x), main.

## Position (2026-11-16)

- Tagged v0.2.0 (plugin 0.2.0). main at e4688ce, 508 tests.
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

Campaign cpp@1 + zones: ALL FOUR RULES SHIPPED (design docs/CPP1_LAYERS.md; DECISIONS rows 2026-11-17).
peclet-health's own nine-repo trial (TRS2, pin 534fa62) came back 2026-11-17: suite 9947.6 / 64 brain
(cpp@0 11162.9 / 91), rules 1-3 right; on its advice captures threshold > 8 -> > 12 (e4688ce; my clones
9678.2 -> 9349.7, brains 62). Zones name FINAL (Desk card cce-zones-name VERIFIED A, executed, c82b1ea).
RELEASE PREPARED, NOT PUSHED: local branch release-0.3.0 at 46a0b5a (cpp@1 frozen, plugin 0.3.0,
README @v0.3.0) on top of main e4688ce. DECISION-REQUEST sent to hub 2026-11-17 (suggested card
cce-v030-release; A = fast-forward main to 46a0b5a, push, tag v0.3.0 at 46a0b5a, push tag; B hold;
C captures > 11). On VERIFIED A: do exactly that, write `executed` to the card, send peclet-health the
v0.3.0 pin, append STATE_LOG. If main moves before then, rebase release-0.3.0 and ask hub to update
the card's commit id (the action names 46a0b5a). On C: change threshold to 11 (profiles.py, tests
_wide sizes, README, CPP1_LAYERS, DECISIONS), new release commit, new card.
Optional meanwhile (not started): gate `zones` as data (debt_check out-param) for zone_ratchet/log;
hide unchanged zone rows in diff text; rule (3) open notes (brain match by bare name, --target
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

- hub/user: DECISION-REQUEST tag v0.3.0 (sent 2026-11-17; see Next action).
- peclet-health: told 2026-11-17 (bridge, delivery unconfirmed): captures > 12 at e4688ce, zones name
  final, v0.3.0 pending; it keeps cpp@0 at 719092b as gate until the v0.3.0 pin arrives.

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
