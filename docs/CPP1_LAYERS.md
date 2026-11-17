# Design: zones (per-layer reporting) and cpp@1

Requested by peclet-health 2026-11-17 (user approved the request in that session). Brief:
docs/briefs/cpp1-layers-design.md (with Peclet's layer split as addendum). Design by `think`
(Opus xhigh) 2026-11-17, checked with a throwaway prototype on the nine Peclet clones:
docs/cpp1/proto.py, falsifiers docs/cpp1/f1.cpp (prototype code, not shipped; read it for the
rules, re-derive in the backend). cpp@0 stays frozen, bit-identical, and stays Peclet's gate until
cpp@1 is released.

## Order of work

1. Zones with `paths` only, plus a byte-identity test (no zones configured: scan, diff, gate output
   unchanged byte for byte).
2. Zone `markers` (function-level).
3. cpp@1 rules in order (1) preprocessor/if constexpr, (2) loop credit, (3) kernel lambdas,
   (4) captures. Release (freeze) cpp@1 only after a Peclet run.
Deferred: `[gate] zone_ratchet` (zone debt may not rise on its own; changes pass/fail), named-lambda
kernels (`auto b = [&]...; parallel_for(n, b)`), splitting non-kernel lambdas, functor members.

## Zones

- Config name `[[zones]]`, output keys `zone`/`zones` (the user's choice, Decision Desk card
  cce-zones-name, 2026-11-17; `[[architecture.layers]]` already means import order and `[gate] tier` is taken). Keep the key in one place in config.py.
- Each entry is one rule: `name`, optional `paths` (globs via config.match; default all), optional
  `markers` (identifiers in a C++ declaration head). Rules are ordered; first match wins; several
  rules may share a name.
  - Function zone: first rule whose paths match the file and (if it has markers) whose markers
    occur in the function head (lines from `_head_lines` first to the brace line; for a split
    lambda its introducer).
  - File zone: first rule without markers whose paths match.
  - Unmatched: zone "-", shown, never dropped; `config --check` warns when there is no catch-all.
- Peclet order: user (globs) > device (K-only header globs) > device (markers KOKKOS_INLINE_FUNCTION,
  KOKKOS_FUNCTION, KOKKOS_FORCEINLINE_FUNCTION, KOKKOS_LAMBDA, KOKKOS_CLASS_LAMBDA) > host (catch-all).
- Output only when zones exist: `zone` on every file and function record, top-level `zones` in
  scan JSON (additive optional keys; SCAN_SCHEMA stays 1). Per zone from functions: functions,
  fn_nloc, debt, debt per kNLOC (over fn_nloc), functions_with_debt, brain; from files by file
  zone: files, nloc, health, file_smells, errors. Scan prints a zone table; diff and the gate's
  debt detail add a line per zone (debt before/after, new brains). Pass/fail unchanged.
- Rejected: per-path or per-zone profiles (debts in different units cannot be summed by the
  touched-files rule; a move or an added marker would change the score with no code change; they
  cannot split host and device inside one header). Per-zone gate policy now ("ratchet-only" is a
  planning priority; the gate already ratchets everything).

## cpp@1: one preprocessed view, then lizard (never subtract counts by hand)

(1) Preprocessor and `if constexpr`. Per #if/#ifdef/#ifndef group keep one live branch, blank the
others and all of the group's directives (newlines kept). `#if 0`, `#if false`, `#elif 0` are dead;
under `#if 1` the other branches are dead. Keep the branch with the most decision tokens (if for
while catch case && || ?, counted recursively; an implicit empty #else counts 0; ties to the first).
If the chosen branch's brace balance differs from the first live branch's, keep the first live
branch. Our tokenizer splits && and || into two characters: count adjacent pairs. Blank the `if` of
`if constexpr` before lizard. Nesting: `if constexpr`, `else if constexpr` and their `else` open no
level. NLOC from the view.
(2) Loop credit: nesting = max over paths of (depth - 1 if the path holds at least two loops
(for, while, do), else depth). The literal "loop-only nest" reading saves 6 debt suite-wide.
(3) Kernel lambdas, split into their own function: macro lambdas (profile list KOKKOS_LAMBDA,
KOKKOS_CLASS_LAMBDA, then balanced (...), at most 12 specifier tokens, then `{`) and bracket lambdas
(`[...]` after ( or , as a direct argument of a call in the profile's kernel_calls: parallel_for,
parallel_reduce, parallel_scan), only inside a function lizard reports. Score the lambda by lizard
on `void L<params> <body>` padded to its original line with inner kernel-lambda bodies blanked;
score the parent by rerunning lizard with lambda bodies blanked. Invariant: sum of (CCN - 1)
unchanged by the split (prototype: 0 mismatches over 263 Peclet C++ files). Name
`parent::<Kokkos label>` from the raw text (tokens turn literals into '0'), else
`parent::lambda#k`; never a line number (base_brains matches names: gate.py:1403, cli.py:162).
A suppression covers a lambda from the line above the launch to the lambda's `{`.
(4) Captures, new metric `captures`, lambdas only: explicit capture entries, plus with a default
capture the names the body uses that are parameters or earlier locals of the enclosing function
(not lambda params, body locals, names after . -> ::); `this` counts 1.
Weights: ccn >10 w1, nloc >70 w0.1, nesting >3 w3 (with the credit), params >5 w1, captures >8 w0.5.
Brain: CCN > 25 and NLOC > 70; lambdas can be brains. Rejected: captures added to params at
threshold 5 (+4069 debt, +45 %).

Prototype numbers (nine clones, no suppressions): cpp@0 10753.8 / 88 brain; +(1) 10472.9 / 83;
+split 9482.9 / 62; +credit 9050.9; +captures 10094.4. 1302 kernel lambdas (1255 macro); captures
median 4, p90 11, max 30. Per zone cpp@0: U 658 / 2, H 8377 / 73, K 1719 / 13 (757 functions
marked K); cpp@1: U 650 / 2, H 6003 / 41, K 2990 / 19.

Known edge cases: lambdas whose braces come from a macro, and named kernels, stay in the parent;
`parallel_for<T>(` is missed (0 in Peclet); branch choice ignores correlated conditions (`#ifdef A`
with `#ifndef A`), the brace guard limits the damage; captures misread `x * y(` as a declaration and
miss members reached through an implicit this. Risk: captures charge a lambda while an equivalent
functor's members are free (incentive to convert lambdas to functors).

Rule (1) as shipped (src/cce_code_health/cpp_view.py, 2026-11-17; Peclet nine repos: cpp@0 10899.5 / 89
brains, cpp@1 10609.7 / 84): a literal condition must be the whole condition after comments are
removed (`#if 0 || defined(X)` is live); `#elif 1` and `#if true` make the later branches dead;
`#elifdef`/`#elifndef` act as `#elif`; the `if` of `if constexpr` is not a decision when branches are
compared; a runtime `else if constexpr` nests as an else-if. Limits found in review (think): a
block comment that starts on a group directive and runs over lines leaks its tail into the view;
a dangling else after a braceless `if constexpr` body is read as flat; duplication and zone markers
still read the raw source. Open (design choice, unchanged): the brace guard falls back to the first
live branch; taking the best branch among those whose balance equals the first's would be better.

Rule (2) as shipped (2026-11-17): the path rule above, in _NestingScan.level; Peclet nine repos
cpp@1 10609.7 -> 10162.7 debt, brains 84 unchanged.

Rule (3) as shipped (src/cce_code_health/cpp_kernels.py, 2026-11-17; Peclet nine repos cpp@1
10162.7 / 84 -> 9163.7 / 62 brains; 1302 kernel lambdas; sum of CCN - 1 and of function NLOC
unchanged in all 263 C++ files; per zone (zones trial config, cpp@1 without captures) device
2392.5 / 19, host 5974.9 / 40, user 1065.7 / 5). Beyond the design, after a think review:
- NLOC is exact: a function keeps its whole NLOC minus the whole NLOC of its direct kernel
  lambdas, so the launch and closing lines are not counted twice (sum per file conserved).
- Fallback: when the split loses a parent or a lambda, a lizard rerun fails, or the file's sum of
  CCN - 1 changes (a half-written file), the file is measured unsplit. Brackets pair only with
  their own kind.
- A macro lambda at file scope that lizard reads as a function named after the macro is that
  function, not split.
- Names: label whitespace runs become `_`; `lambda#k` counts the parent's unlabelled kernels;
  a repeated name gets `#2`; a nested kernel is named under its enclosing kernel.
- Record `introducer` (the kernel's macro, or the enclosing kernel's) is added to the zone head,
  because the head scan stops at the first `{` on a launch line (`MD({0,0},{n,n}), KOKKOS_LAMBDA`).
- The lambda is scored on its original lines (the prototype pulled the body up to the head line:
  one or two NLOC less for a `{` or specifiers on their own line).

Open notes (review): new brains are matched on the bare name across files (gate.py:1430,
cli.py:169, zones.py:144), so a repeated `run::lambda#1` elsewhere can hide a new brain; matching
on qualname would be stricter. A short `--target path::compute` can be ambiguous when a Kokkos
label repeats the parent's name. Adding an unlabelled kernel above others renumbers `lambda#k`
(advise labels). cpp@0 suppressions on a parent's head do not cover its lambdas. A lambda's head
scan also crosses a braceless `for (...)` line above the launch (suppression slightly wider).

Rule (4) as shipped (src/cce_code_health/cpp_kernels.py `captures`, `declared`, `outer_names`;
2026-11-17; Peclet nine repos cpp@1 9163.7 -> 9678.2, captures debt 514.5, brains 62 unchanged;
1302 kernel lambdas, captures median 4, p90 11, max 28, 228 above 8; cpp@0 byte-identical; sums of
CCN - 1 and NLOC conserved in all 263 files). After a think review:
- The metric is the capture count alone (> 8, w0.5), as written above. The prototype's debt
  function charged params + captures > 8 (Peclet: 958.5 instead of 514.5, total 10122.2; the
  10094.4 above used it). Not taken: a frozen metric must be the number it reports, kernel params
  are set by the launch policy (1 for a range, 2 for a reduce), and params have their own charge.
- The tokenizer splits `->`, `::` and `&&`, so the checks look two tokens back.
- `declared` after `*`, `&` or `>` needs a type before the run that starts a statement or a
  parameter (after `; { } ( ,`, `::` qualifiers and const/static/... skipped; `>` must close a
  `word<`) and one of `=` (not `==`), `;`, `{`, `:` next, or `(` right after `>` (local
  `View<double*> u("u", n)`). Operands such as `x(i) * dt;`, `i > 0 && mask[i]`, `a(i) > tol;`
  stay uses. Parameter lists come from lizard.
- An enclosing kernel's parameters count as names of the enclosing function (team, thread,
  vector nests).
- A name the capture list names explicitly is not counted again as an implicit use.
- `this`-capturing macros are profile data (`kernel_this_macros`); display smell `many_captures`
  (cpp@1 only); `allow captures` is a valid suppression only under a profile with the metric.
- Known edge cases: `[...xs = f(xs)]` does not list xs; a comma inside template arguments of an
  init-capture splits the entry; `i-- > n` reads as `->`.
- Only cpp@1 lists `captures` in its metrics, so cpp@0 output carries no new key.

## Falsifiers (docs/cpp1/f1.cpp; expected numbers)

- F1 if-constexpr chain with `?`: CCN 4 -> 2; nesting cpp@0 2, cpp@1 0.
- F2 `#if` with if / `#elif 0` with 2 whiles / `#else` with `if(a&&b)`: CCN 8 -> 3, NLOC 6 -> 4
  (the prototype gives 2 because of the && split; shipped code must give 3).
- F3 `#ifdef M if(false){ #else if(c>1){ #endif ...}` then more functions: cpp@0 loses every later
  function; cpp@1 has h at CCN 2 and keeps the rest.
- F4 for-for-for-if: nesting 4 -> 3, debt 3 -> 0; if-if-if-if stays 4, debt 3.
- F5 KOKKOS_LAMBDA with one if: lambda CCN 2, nesting 1; parent CCN 1; CCN sum conserved.
- F6 `[a,&b,c=2*n]`: 3 captures; a KOKKOS_LAMBDA using a, dt, b, n: 4.

## cpp@0 defects found (frozen; cpp@1 fixes them)

(a) Unbalanced braces across #ifdef/#else make lizard drop functions (dem src/step_solve_mpi.hpp:
19 functions under cpp@0, 27 in the one-branch view; core portable.hpp 25 vs 17 because each branch
duplicates the same functions). (b) cpp_backend.py:114-118: `if constexpr` opens a nesting level
and each `else if constexpr` adds a spurious one (a flat chain scores 3); twice in Peclet.
