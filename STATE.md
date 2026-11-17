# cce-code-health: state

Rewritten in place, about one screen. History: docs/STATE_LOG.md (append-only, grep it).
Design: docs/DESIGN.md. Decisions: docs/DECISIONS.md. Adopters: docs/adopters/*.md.

Objective: an agentic refactor loop (`codehealth` CLI + Claude Code plugin with the
refactor-loop, optimize-loop and healthy-coding skills) that raises code health without
changing behaviour and builds its own playbook; importable into any repo.
Public repo computational-chemical-engineering/cce-code-health (MIT, 0.x), main.

## Position (2026-11-16)

- Tagged v0.2.0 (plugin 0.2.0). main at 1bed7b9, 442 tests.
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

Campaign cpp@1 + zones (peclet-health request 2026-11-17; the user approved the request in that
session; cpp@0 stays frozen and stays Peclet's gate). Design DONE: docs/CPP1_LAYERS.md (think,
Opus xhigh, with prototype docs/cpp1/proto.py and falsifiers docs/cpp1/f1.cpp); decision row
2026-11-17 in docs/DECISIONS.md. Step 1 DONE (1bed7b9: src/cce_code_health/zones.py, tests/test_zones.py;
byte-identity script scratchpad ident.sh: scan 9 Peclet + self@cf15245 + self diff, 22 files).
Next: step 2 markers. Think review notes for it: annotate needs each function's head text (pass
texts from analyze_ref, or have cpp_backend expose the head / split-lambda introducer); file_zone
must skip rules with markers; catch-all must also mean "no markers". Step 3 note: once kernel
lambdas are split, the enclosing function's nloc must drop the lambda lines (fn_nloc overlap).
Later from the review: gate `zones` as data (debt_check out-param) for zone_ratchet/log; hide
unchanged zone rows in diff text. Then cpp@1 rules (1)-(4), each with its falsifier first.
Config key name `[[zones]]` is a DEFAULT: DECISION-REQUEST sent to hub (2026-11-17); keep the key
in one place in config.py so a rename is one edit. Calibration data: shallow clones of the nine
Peclet repos in the session scratchpad (.../scratchpad/peclet/peclet-<name>; if gone, re-clone
with `gh repo clone computational-chemical-engineering/peclet-<name> -- --depth 1`); cpp@0
baseline with scratchpad/peclet/base.toml: debt 10899.5, brain 89 (core 798.2, geom 22.0,
amr 1064.0, morton 11.0, flow 5614.9, pnm 319.3, dem 1485.8, voro 1438.9, coupling 145.4).
Peclet's suite-scan scope and layer globs: addendum of docs/briefs/cpp1-layers-design.md.

## Open requests

- peclet-health: cpp@1 + zones; step 1 pin 1bed7b9 sent 2026-11-17 (bridge, read not confirmed);
  send again for markers and cpp@1. Told it dem's clone has no src/*_kernels.hpp (its glob).
- User (via hub): public name of the zones config key (default `[[zones]]`). Desk card
  `decisions/cce-zones-name` (hub agrees with A); act only on a VERIFIED sign-off. Asked hub to
  extend the card to the output keys `zone`/`zones` (default: follow the config name); rename all
  in one commit when signed.

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
