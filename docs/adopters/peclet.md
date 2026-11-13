# Adopter notes: Peclet suite (preparation, 2026-11-13)

Status: **prepared, not onboarded.** Nothing in the Peclet repositories has been changed.
Facts from a read-only recon of shallow clones (GitHub org computational-chemical-engineering);
onboarding itself is a work package for a Peclet session, under Peclet's own process.

## Facts

- Suite: umbrella `peclet` (submodules) plus `peclet-core`, `-flow`, `-dem`, `-amr`, `-voro`,
  `-pnm`, `-morton`, `-coupling`, `-geom`, `-examples`. C++20 (morton C++17), Kokkos backends
  (OpenMP, Serial, Threads, CUDA, HIP), optional MPI. Size (C++/CUDA/headers, approx.):
  flow 102k lines, amr 32k, core 31k, voro 27k, dem 25k, morton 12k, pnm 4k.
- Process: `peclet/CLAUDE.md`, per-repo CLAUDE.md (flow 507 lines), `docs/DECISIONS.md` +
  `decisions/`, `docs/QUALITY_PLAN.md` (packages A to H done before 1.0.0, incl. G "Structure":
  `flow_ibm.hpp` 11,886 -> 4,688 lines plus twelve domain headers, verbatim moves, bit-exact).
  Lesson recorded there: "A green battery is not a gate; a byte comparison is."
- Build: host builds already use `-ffp-contract=off` (core/CMakeLists.txt:120-132,
  flow/CMakeLists.txt:118-130); `-march` opt-in only; no `-ffast-math`. Warnings
  `-Wall -Wextra -Wpedantic` only in voro; no `-Werror`; no sanitizer options.
- clang-tidy: `.clang-tidy` in core, flow, dem, amr, pnm, voro (curated set, from
  QUALITY_PLAN §3.D.7), but not run in CI. clang-format 18.1.8 is blocking in CI.
- Tests: ctest with custom harnesses; flow has 285 registered ctests (166 MPI at np 1..8,
  48 Python), label `bench` excluded by default; regression/verify tests with a committed hash
  gate; refinement studies under flow/tests/study. CI runs `ctest -j1 --timeout 1200 -LE bench`
  then regression/verify with `--timeout 3600`. Thread pins OMP_NUM_THREADS=2.
- Claude setup: umbrella has `.claude/agents/design-implementer.md` and skills.

## Proposed gate per repository (to agree with the Peclet session)

| tier | kind | cadence | what |
|---|---|---|---|
| build | command, behaviour=false | step | host-openmp Release build, fixed flags, `-ffp-contract=off`; decision 14: identical flags both sides, base build cached per sha |
| warnings | command, behaviour=false | step | build with `-Wall -Wextra -Wpedantic`; warning count per file may not rise (ratchet script; `-Werror` only where already clean) |
| kernels | command | step | `ctest -j1 -LE bench -E '^(regression_|verify_)'`, timeout, `detect_exit` |
| regression | reference | step | the committed hash gate / regression outputs, bitwise in refactor mode |
| convergence | reference with tolerance | campaign | grid-refinement studies: observed order within a declared band (optimize mode guard) |
| sanitizers | command | campaign | Serial backend build with `-fsanitize=address,undefined`, kernel ctests |
| clang-tidy | command, behaviour=false | campaign | existing `.clang-tidy`; count of findings per check may not rise |
| benchmark | benchmark | campaign (optimize: step) | `bench`-labelled ctests or `vof_perf` scripts, ABBA, thread pins |

Scope for a first campaign: one repository (pnm or morton: small, already tidy) to calibrate
cpp@0 and the build cost per step; flow only after that.

## What cce-code-health provides now and what is missing

- Provides (in progress 2026-11-13): cpp@0 profile (lizard: cyclomatic, NLOC, nesting,
  params; uncalibrated) for scan, diff, hotspots and the gate's debt rule; change coupling;
  gate tiers, self-test, `--revert`, `codehealth log`; refactor and optimize skills.
- Missing before onboarding: decision 14 checks (flag identity and forbidden-flag scan in a
  step); C++ static rules (public headers, literal changes); a warnings/clang-tidy ratchet
  helper (count per check, base vs head); a convergence-order helper (observed order from
  (h, error) pairs, compared against a declared band); cpp@0 calibration against CodeScene.

## Smoke scan

(to be filled from the cpp@0 smoke test)
