# Adopter notes: OpenFlowsheet

Findings 2026-11-11 at OpenFlowsheet a9e733e4. Proposal only; nothing changed in OpenFlowsheet.

## Existing machinery

- **F0 corpus identity gate**: `PYTHONPATH=src .venv/bin/python scripts/corpus_identity.py --check`.
  Measures M1..M9: content and artifact hashes, every `state_sha256` bitwise on the reference
  platform, outcomes and checks, cache keys, v0.3.0 bundle replay, schema round-trip node ids,
  bytes of identity-bearing modules. Pins BLAS/OMP threads. **Measured: 40 s, PASS**
  (M1 50/50, M2..M5 61/61, M6 85/85, M7 52/52, M8 105/105, M9 35/35).
- **Test suite**: ~6,700 test functions (~15k collected items), no xdist, no coverage tooling.
  Last recorded full local gate 8,095 s. CI `check` 61 min (x86) and 83 min (arm).
- `scripts/gate.sh` is quiet (one summary line).

## Constraints for a refactor loop

1. **35 frozen modules** (F0 M9 registry, `benchmarks/f0/corpus.json` → `measures.M9`):
   any byte change breaks identity by design. Exclude permanently (generate the exclude list
   from the registry). They carry 565 of 6,924 src debt.
2. **Solver counters (R0) are identity**: a refactor that changes evaluation count or order
   breaks identity even if numbers stay equal (decision register: "a refactorization for the
   chord (moves R0 counters)").
3. **Hashed texts**: e.g. served MCP tool descriptions and schemas are hashed; string literal
   edits can move hashes.
4. **Process**: work packages `wp/<ID>`, evidence manifest `status: tested` before merge,
   reviewer pass; design-lane review for residuals, derivatives, phase logic, scaling,
   certificates, replay identity (OpenFlowsheet CLAUDE.md).

## Proposed gate tiers

| tier | when | what | cost |
|---|---|---|---|
| 0 static | each step | ruff, mypy on touched files; frozen files byte-equal; string literals and public signatures unchanged | seconds |
| 1 tests | each step | tests that execute the target (per-test coverage map built once per campaign at base, subprocess-aware) + mutant check on that subset | minutes |
| 2 identity | each step | F0 `--check` | 40 s |
| 3 full | campaign end | CI on both architectures (existing merge condition); local full gate only before RC | ~1.5 h CI |

Open for the owner: add `coverage` (and possibly `pytest-xdist`) to dev dependencies
(`requirements.lock`); first scope `src/openflowsheet/application/` (hotspots local.py,
revision_run.py, revision_binding.py, coupled_run.py; CodeScene 3.97 to 4.12); the
campaign as an OpenFlowsheet work package (e.g. `wp/CH1`).

## Plan from OpenFlowsheet-TR09 (2026-11-11), to start after the budget reset 2026-11-12 13:00

Approved by Frank as work package **S16** (branch wp/S16; CH1 did not fit the ID pattern). Tested manifest incl. recall check, one bounded reviewer pass. First refactor scope confirmed by Frank (2026-11-11): src/openflowsheet/application/; M9 frozen modules and hashed texts (bindings/descriptions, schemas, catalogue YAML) excluded; every step keeps F0 --check and the served MCP digest unchanged. Campaign starts after S16 select is merged.
1. coverage, pytest-cov, pytest-xdist in a separate hash-pinned `requirements-testmap.lock`
   (requirements.lock and pyproject.toml are identity-bound, D2.4 / R-140). Nothing in src/.
2. `scripts/test_impact.py build`: default-marker suite once at a base commit, coverage
   dynamic contexts per test node, subprocesses via COVERAGE_PROCESS_START + sitecustomize
   on PYTHONPATH for this run only. Map (file, line) -> node ids, keyed by base commit + tree.
3. Non-Python inputs (catalogue YAML, schemas, MCP description .md, benchmark data,
   fixtures): recorded per test with an audit hook on "open"; a non-.py change maps to the
   tests that opened the file.
4. `scripts/test_impact.py select --base <sha>`: node ids for base..worktree; full-suite
   fallback (with reason) on conftest / *_support.py / plugin change, new or deleted file,
   base not an ancestor, stale map.
5. Verified by recall on historical failing commits and a seeded-mutation spot check.

Constraints: selection is inner-loop only; merge condition stays CI green on both
architectures + F0 after merge (recorded as an OpenFlowsheet decision). xdist opt-in and
only after a measured identical run. Refactor exclusions: the 35 M9 modules plus anything
hashed into identity or a surface digest (bindings/descriptions, schemas, catalogue texts,
R0 counters). application/ local.py, revision_run.py, coupled_run.py are on the agent
surface chain (c_0..c_9): per-step check must include the served MCP digest besides F0.

Integration with cce-code-health: the per-step tier-1 command in `.codehealth/config.toml`
is OpenFlowsheet's `scripts/test_impact.py select`; tier-2 commands are F0 `--check` and
the served-digest check. After S16 lands, send the per-step gate to OpenFlowsheet-TR09
for a check against the repo's rules.
