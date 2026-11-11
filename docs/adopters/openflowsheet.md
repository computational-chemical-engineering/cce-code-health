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
