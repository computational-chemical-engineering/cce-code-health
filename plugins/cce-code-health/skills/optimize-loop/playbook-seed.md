# Performance recipes (seed)

Seed recipes, shipped with the plugin. Evidence starts empty; repos record their own in
`.codehealth/playbook.md` (section "Performance recipes"). Round-off column: what a
reference comparison should expect. "none" means the step should still be bitwise.

## Python / NumPy / SciPy

| id | targets | when | when not | round-off |
|---|---|---|---|---|
| py-vectorize-loop | Python loop over array elements | element-wise or reduction loop in a hot function | loop carries a dependency between iterations; arrays are tiny | reductions change (NumPy pairwise sum) |
| py-hoist-invariant | repeated work inside a loop | expression or lookup that does not change across iterations | the "invariant" depends on mutated state | none |
| py-avoid-copies | temporaries, `.copy()`, fancy indexing | large arrays rebuilt every call | caller relies on getting a fresh array | none |
| py-inplace-out | `a = a + b` style updates | large arrays, ufuncs with `out=` | aliasing between inputs and output | none |
| py-reuse-factorisation | repeated sparse solves with the same matrix | `spsolve` in a loop, matrix unchanged | matrix changes every call | small |
| py-reuse-sparsity | sparse assembly every Newton step | pattern fixed, only values change: assemble data array into a fixed CSR structure | pattern changes | duplicates summed in a different order: small |
| py-batch-rhs | many solves or evaluations one by one | independent right-hand sides or states | memory limits | small |
| py-cache-pure | pure function called with repeated hashable arguments | measured repeat rate is high | arguments are arrays or mutable; memory growth matters | none |

## C++

| id | targets | when | when not | round-off |
|---|---|---|---|---|
| cpp-loop-order | nested loops over multi-dimensional data | innermost loop does not run over contiguous memory | loops contain a reduction whose order changes | none without reduction reorder |
| cpp-hoist-invariant | work repeated inside hot loops | invariant expressions, bounds, pointer lookups | compiler already hoists (check the profile, not the source) | none |
| cpp-no-alloc-in-loop | allocations in hot paths | `std::vector` or `new` inside a loop | object must outlive the iteration | none |
| cpp-aos-to-soa | structs of fields accessed one field at a time | vectorisable loops over one field | access is per object, all fields together | none |
| cpp-blocked-reduction | long sums, dot products | vectorised or blocked accumulation | bitwise results are required for this output | changes; often more accurate |
| cpp-explicit-fma | `a*b + c` in hot kernels | hardware FMA available, tolerance declared | build uses `-ffp-contract=off` for a bitwise gate of this output | changes |
| cpp-strength-reduction | `pow(x, 2)`, divisions by loop-invariant values | integer powers, repeated division | division must stay correctly rounded for the output | small |

## Evidence format (repo playbook)

    ### <id>
    - when / when not: ...
    - recipe: 3 to 6 lines
    - evidence: attempts N, accepted M, median speedup S, max deviation D (from runs/*.jsonl)
