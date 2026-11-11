# Code-health baseline: OpenFlowsheet @ a9e733e4

Proxy score (1..10), NLOC-weighted per area. Not CodeScene's metric.

| area | files | NLOC | health | files < 4 | files 4..9 | files = 10 |
|---|---|---|---|---|---|---|
| tests | 595 | 153423 | 9.38 | 1 | 228 | 366 |
| src | 277 | 99484 | 6.93 | 10 | 190 | 77 |
| scripts | 135 | 77342 | 5.57 | 8 | 100 | 27 |
| docs | 94 | 58709 | 4.23 | 15 | 59 | 20 |
| benchmarks | 63 | 23227 | 6.40 | 4 | 37 | 22 |
| spikes | 27 | 9887 | 6.64 | 2 | 16 | 9 |
| **all** | 1191 | 422072 | 7.16 | | | |

## Function smells

| smell | count |
|---|---|
| complex_function | 1157 |
| many_args | 821 |
| long_function | 544 |
| deep_nesting | 292 |
| bumpy_road | 194 |
| brain_function | 151 |

## Hotspots (top 20 by commits)

| file | commits | churn lines | NLOC | health | main smells |
|---|---|---|---|---|---|
| tests/test_m07a_harvest.py | 62 | 476 | 104 | 10.0 |  |
| src/openflowsheet/application/local.py | 33 | 2504 | 1739 | 4.8 | many_argsx11, long_functionx4, complex_functionx3 |
| scripts/t08_support_matrix.py | 29 | 826 | 482 | 7.2 | complex_functionx3, bumpy_roadx1, long_functionx1 |
| tests/test_t08_w2_support_envelope.py | 25 | 582 | 351 | 9.0 | complex_functionx2 |
| src/openflowsheet/application/revision_binding.py | 22 | 2471 | 1553 | 3.9 | many_argsx4, complex_functionx3, bumpy_roadx3 |
| tests/test_t08_w2_inventory.py | 21 | 1279 | 784 | 5.7 | complex_functionx4, deep_nestingx2, brain_functionx1 |
| scripts/m07_journey.py | 19 | 2814 | 1952 | 4.2 | complex_functionx10, many_argsx9, bumpy_roadx2 |
| src/openflowsheet/application/revision_run.py | 19 | 1590 | 1026 | 1.8 | many_argsx8, complex_functionx4, long_functionx4 |
| tests/test_m06_w27_coverage.py | 19 | 1083 | 683 | 10.0 |  |
| tests/test_m07_journey.py | 18 | 1297 | 962 | 9.3 | complex_functionx1, many_argsx1 |
| tests/test_t07_w5e_application_results.py | 18 | 700 | 412 | 9.8 | many_argsx1 |
| tests/test_t08_w2_surface_digest.py | 18 | 701 | 256 | 10.0 |  |
| src/openflowsheet/application/cli.py | 17 | 1221 | 853 | 8.7 | complex_functionx2, long_functionx1 |
| scripts/v0_2_gate.py | 16 | 1774 | 1122 | 3.8 | complex_functionx10, many_argsx3, deep_nestingx2 |
| src/openflowsheet/application/jobs/runner.py | 16 | 1286 | 940 | 7.6 | many_argsx4, complex_functionx2, long_functionx2 |
| src/openflowsheet/application/types.py | 16 | 2165 | 1531 | 9.0 |  |
| src/openflowsheet/studies/nlp/closure.py | 16 | 1177 | 757 | 8.0 | complex_functionx2, long_functionx1, bumpy_roadx1 |
| tests/test_m05_study_record.py | 16 | 1325 | 922 | 7.5 | complex_functionx3, bumpy_roadx2 |
| tests/test_t07_global_state.py | 16 | 223 | 116 | 8.5 | complex_functionx2, deep_nestingx1 |
| scripts/m07a_rc.py | 15 | 658 | 356 | 10.0 |  |

## Worst functions (top 20 by cognitive complexity)

| file:line | function | cognitive | CCN | NLOC | nesting | bumps |
|---|---|---|---|---|---|---|
| docs/derivations/scripts/t04_reference.py:2077 | build | 394 | 323 | 1290 | 6 | 7 |
| docs/derivations/scripts/s10_reference.py:914 | build | 315 | 152 | 783 | 6 | 10 |
| docs/derivations/scripts/s10c_reference.py:1831 | build | 181 | 90 | 547 | 4 | 4 |
| docs/derivations/scripts/m01_reference.py:998 | build | 179 | 140 | 728 | 3 | 7 |
| benchmarks/m02/g11_coverage_v3.py:680 | summarise | 150 | 155 | 280 | 1 | 0 |
| scripts/m03_ipopt_inventory.py:750 | build_record | 150 | 124 | 324 | 4 | 7 |
| src/openflowsheet/orchestrator/region.py:1921 | _solve_region | 145 | 123 | 534 | 4 | 6 |
| scripts/m07_g11_experiments_audit.py:111 | audit_experiment | 142 | 120 | 169 | 3 | 13 |
| docs/derivations/scripts/t04_reference.py:1500 | seed_ptc | 135 | 55 | 102 | 6 | 1 |
| docs/derivations/scripts/t03_reference.py:897 | build | 132 | 135 | 618 | 5 | 7 |
| docs/derivations/scripts/t05_reference.py:2624 | run_claims | 127 | 102 | 528 | 3 | 7 |
| src/openflowsheet/numerics/anderson.py:175 | solve_recycle | 126 | 55 | 223 | 5 | 1 |
| docs/derivations/scripts/m06_w27_registration.py:1538 | classify_case | 124 | 71 | 213 | 5 | 6 |
| src/openflowsheet/models/syn001/column.py:1227 | declared_rows | 119 | 44 | 292 | 2 | 4 |
| docs/derivations/scripts/s07_measurements/m9_a31_allowances.py:44 | main | 116 | 38 | 133 | 6 | 2 |
| src/openflowsheet/numerics/newton.py:248 | solve_newton | 115 | 55 | 375 | 4 | 2 |
| docs/derivations/scripts/s10_reference.py:1869 | build_real | 113 | 63 | 335 | 4 | 3 |
| docs/derivations/scripts/t01_reference.py:1050 | tear_candidates | 113 | 64 | 114 | 4 | 1 |
| docs/derivations/scripts/t02_reference.py:885 | build | 112 | 81 | 577 | 3 | 2 |
| docs/derivations/scripts/t02_reference.py:250 | accelerate | 110 | 49 | 137 | 5 | 1 |
