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

## Agreed with peclet_health (SMM-TRS2, 2026-11-13)

- Counterpart: session `peclet_health` on SMM-TRS2, checkouts `/home/frankp/Codes/suite/<repo>`;
  campaign worktrees `suite/<repo>-health-<topic>`. It runs all builds and tests on TRS2
  (48 cores, RTX 5080); code_health does not. Read-only ssh look is allowed.
- **First target: dem** (17k C++ lines, Kokkos + ArborX, optional MPI, nanobind; idle and
  unclaimed; byte gate `tests/regression/state_hash.py`). pnm is mostly frozen by the A7 paper
  branch; flow VoF/viscous and voro lagrangian/ are frozen for now. Tiny fallbacks for
  calibration: morton (2.4k lines; performance-tuned, so metrics may mislead), geom (0.4k,
  vendored into core).
- dem build: `cmake -S . -B build -DCMAKE_PREFIX_PATH=$PWD/../extern/install/nvidia-cuda
  -DPECLET_DEM_BUILD_TESTS=ON [-DPECLET_DEM_MPI=ON]`; tests exit 77 for "cannot run": that is
  SKIP, never pass. ctest exits 0 with skipped tests, so the gate needs an output pattern.
- Constraints: docs/DECISIONS.md binding (no swapping in a rejected alternative); never change
  numerics while restructuring; bitwise class on host/OpenMP only (CUDA differs by FMA
  contraction by design); no env var may change a result; public Python names per
  docs/NAMING.md; OpenMP always bounded (OMP_NUM_THREADS 2..8, OMP_PROC_BIND=false); MPI ctests
  `--bind-to none`; Snellius/LUMI out of scope; shared checkouts: own worktree and build dir,
  named-path staging; push straight to main (no PRs), umbrella pointer bumped last.
- Frozen-ish: cmake/PecletDeps.cmake, shared CMake modules, regression baselines, CLAUDE.md.
- Tools: clang-tidy 19.1.7, clang-format 18.1.8 (suite venv). Warnings and tidy as a ratchet,
  not -Werror. core needs Release (state_hash checks the toolchain string, SKIPPED otherwise).
- No (h, error) refinement script is known yet; the convergence tier waits for one.
- Timings: to be measured by peclet_health (dem build cold/incremental, ctest, state_hash).

## What cce-code-health provides now and what is missing

- Provides (in progress 2026-11-13): cpp@0 profile (lizard: cyclomatic, NLOC, nesting,
  params; uncalibrated) for scan, diff, hotspots and the gate's debt rule; change coupling;
  gate tiers, self-test, `--revert`, `codehealth log`; refactor and optimize skills.
- Missing before onboarding: tier `skip_exit` (77 = not run = fail) and `fail_regex`;
  decision 14 checks (flag identity and forbidden-flag scan in a
  step); C++ static rules (public headers, literal changes); a warnings/clang-tidy ratchet
  helper (count per check, base vs head); a convergence-order helper (observed order from
  (h, error) pairs, compared against a declared band); cpp@0 calibration against CodeScene.

## Smoke scan

cpp@0 on GitHub main clones of peclet-core, -flow, -dem and -voro (2026-11-13, scope
`include/` and `src/` C++ only; uncalibrated profile, so relative numbers matter more than
absolute ones): 200 files, 79,418 NLOC, debt 9,769.9 (123.0 per kNLOC; OpenFlowsheet src is
not comparable across profiles), display health 5.27; 3,703 functions,
89 brain functions; 14 large and 4 huge files; scan 1.3 to 4.5 s per repo.
Worst functions: dem `demSolveContacts` (debt 345, CCN 261, NLOC 912); flow `bind_diagnostics`
(NLOC 2,063) and `bind_solver` (nanobind binding tables: long but flat, a candidate for a
"binding table" exclusion or recipe); flow `Solver<Grid>::scalarCutAssembleSolve` (CCN 128);
voro `meshVolumeOptimize` (CCN 90, 14 params).
