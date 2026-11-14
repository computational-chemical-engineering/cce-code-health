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
Adopter feedback round 2 (OpenFlowsheet-TR09, 2026-11-13): {ref_base} placeholder, exit codes and self-test outcome (detected|missed|error) in JSON, per-tier detect_exit, debt_before/after + head_tree in gate JSON, --require-gain (exit 0 = accept), `codehealth log` (validated run-log lines, HEAD tree must equal gated tree), scan end_line/qualname, --mutate path:line:N. Skills: create all campaign files before the campaign base. 88 tests.
Round 3 (2026-11-13): self-test honours --ref-base for {ref_base}; meta.head = HEAD sha when the tree is clean; gate --revert SHA (exact inverse via merge-tree, debt may rise; log records class revert, result review-reject). Skills: commit provisionally, then gate, reset --hard on failure; seed recipe check-ladder and review anti-patterns. 97 tests.
2026-11-13 user items 1, 2, 4 (beyond the procedure): built, think-reviewed (12 findings fixed), pushed at 092aaeea (215 tests):
- prevention: `codehealth hook` + plugin PostToolUse hook (post_edit.sh), healthy-coding skill, reusable CI workflow .github/workflows/codehealth-diff.yml;
- architecture: `codehealth coupling` (test pairs dropped), LCOM4 informational, `[architecture]` layers/forbid/cycles ratchet (`codehealth arch`, diff, gate), change class restructure (plan at base);
- Peclet prep: cpp@0 (lizard) + `[profiles] cpp`; docs/adopters/peclet.md; counterpart session peclet_health on SMM-TRS2 (/home/frankp/Codes/suite): first target dem; it runs all builds; send it the install command after push.
- C++ gate: skip_exit/fail_regex, findings tier (clang-tidy/warnings ratchet), flags static check (decision 14), convergence tier; scan/hotspots --config for read-only trials.
- dem scan done by peclet_health (debt 1939, 12 brain). Fixes: 4a092f16 cpp@0 binding tables (NB_MODULE etc.) carry no length debt and are never brain; 8a10c858 `coupling --config`. Agreed order: demSolveContacts phases, launcher parameter structs, colouring dedup; *Kokkos renames before CB.
- OpenFlowsheet S17 pinned at 8a10c858 (scan at CB bit-identical), WO-2 binding.py 134.2 -> 43.6, application/ 855.0 -> 683.5. Its feedback: `log --result accepted-neutral --reason` (no-gain step gated without --require-gain) and `log --head SHA` (late logging), 222 tests.
- Docs health (user 2026-11-13): recon of OpenFlowsheet and Peclet (both run their examples; neither checks agent docs or docstrings); design docs/DOCS_HEALTH.md (think-reviewed); built `[docs]` table, `codehealth docs --stats`, docs ratchet in diff/gate (check `docs`), docstrings D1/D2 in diff/gate/hook (check `docstrings`). OpenFlowsheet trial: 763 docs, 26k refs, 2.9 s; replay 60 commits: 0 rot failures, new unresolved refs warn by default; after the implementation review (15 findings fixed) replay 0 failures in 60 commits. Self-test mutates C++ literals (--mutate path:line[:N]). dem config draft sent to peclet_health (docs/adopters/dem-config-draft.toml). 259 tests. Later 2026-11-13: reference tier `same_lines` (dependency resolution identical at base and head; peclet_health bug A: base worktree fetched core v1.3.0), restructure _binders fix for kw-only params without default (OpenFlowsheet blocker). Later: staleness via co-change, docs-sync review lane, agent-doc size budget, exemplars, C++ D1/D2.
- Peclet dem S1 closed (peclet_health 2026-11-13): CB 3fd9b93, 3 extract-phase steps on demSolveContacts, all GATE PASS 13/13 bitwise, logged accepted; function 345 -> 258.3, dem 1871.8 -> 1858.2. Its feedback, done: log `target_debt` (function before/after + new functions in the file, from gate meta `function_debt`); seed anti-pattern "phase helper with 10+ parameters" (parameter-object first); README notes that cpp@0 does not count `[&]` captures. 260 tests. dem S2 next: launch-params struct.
- dem S2-S4 closed (2026-11-13/14): debt 1858.2 -> 1693.5, brain back to 12 after S3's relocation (exit rule now in the refactor-loop skill); log `target_debt.relocated`; README health vs debt; GPU path not deterministic (envelope check, no device reference tier). S5 (dedup colouring) running.
- OpenFlowsheet S17 merged d90b498f (2026-11-14): debt 855.0 -> 301.8, health 7.22 -> 8.49, brain 6 -> 0, arch 11 violations/83 cycle edges -> 2/6. Its restructure rules and extract-phase when-nots folded into the seed; `log --reason` allowed on gate-fail. 262 tests.
- OpenFlowsheet S19 (repo-wide) requests, done 2026-11-14: `arch --config/--depth/--edges` (roots-only table reports cycles and group edge counts; trial on d90b498f matches their script: 10 cycles, largest 17 modules/51 edges, 2.6 s), `scan --group-depth`, scan JSON schema documented (`meta.schema` 1), run log shared across worktrees under flock with `branch` field. 266 tests.
- Gate `--mode interface --plan .codehealth/interface/<id>.toml` (OpenFlowsheet S19 W4, ADR 0076): exact public-API rule over every binding (removed, renamed, changed, added), caller files may only follow the plan (check `callers`), needs public_api = true; think review found 4 bugs + 5 gaps, all fixed with regression tests; the callers rule itself is unreviewed. 290 tests. Later: restructure-mode warning for tests that patch a moved name through its old module (seed rule added now).
- Open C++ issues in docs/adopters/peclet.md.
Still to build: playbook stats; python@1 calibration (LCOM4 into debt); cpp@0 calibration vs CodeScene; C++ public-header/literal static rules.

Next action (old): `gate` with tiers in config (behaviour gate: base-regenerated references, determinism check,
bitwise compare, mutant coverage check), then `playbook stats`, then plugin skills
(setup, refactor-loop, healthy-coding) + reusable workflow. User: run CodeScene on
OpenFlowsheet (needed for python@1 calibration and demo).

Open decisions: scope of refactor (src only vs also tests/scripts/docs); CodeScene CLI access.
