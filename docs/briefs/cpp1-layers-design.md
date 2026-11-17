# Brief (think, design advice): cpp@1 profile and per-layer reporting

## Question

Design two additions to the cce-code-health tool (repo ~/Code/cce-code-health, main 66013c1),
requested by the Peclet suite session (C++20 + Kokkos HPC code, Python bindings via nanobind):

A. **Layer reporting.** Scan, diff and gate report debt, brain count and health per *layer*
   (Peclet: user-facing = Python, binding TUs, examples; host orchestration; device kernels), so a
   plan can set different priorities per layer. The Peclet plan treats device kernels as
   ratchet-only. They asked for "per-path profiles or layer tags". This matters more to them than
   the exact cpp@1 weights.
B. **cpp@1**, a new C++ profile (cpp@0 stays frozen, bit-identical, and stays Peclet's gate until
   cpp@1 exists): (1) `if constexpr` and preprocessor `#if/#ifdef/#elif/#else` branches do not
   count towards CCN (and, for #if, towards nesting: today both branches are tokenised);
   (2) nesting threshold 4 for loop-only nests (a 3-D i,j,k loop nest is not logic);
   (3) a kernel lambda is scored as its own function, not added to the enclosing launch site;
   (4) lambda captures count (a `[&]` lambda is as coupled as a long parameter list; today
   captures score nothing).

## Established facts

- Profiles: src/cce_code_health/profiles.py (63 lines). A profile is frozen once released; a new
  scoring needs a new profile version (standing rule). Debt per function =
  sum_m w_m * max(0, value_m - threshold_m). cpp@0: ccn>10 w1, nloc>70 w0.1, nesting>3 w3,
  params>5 w1; brain = ccn>25 and nloc>70; binding modules (NB_MODULE etc.) are flat tables.
- C++ backend: src/cce_code_health/cpp_backend.py (178 lines, read it whole). lizard 1.26.0 (pinned)
  gives functions with CCN, NLOC, params; lambdas count towards the enclosing function. Nesting is
  computed by our own token scan (`nesting_depth`); `if constexpr` already does not add a nesting
  level there but lizard still counts it in CCN. Preprocessor lines are dropped as tokens, but the
  code in both #if branches is kept.
- Config: src/cce_code_health/config.py. `[profiles] cpp = "cpp@0"` picks the profile per
  *language* (`Config.profile_for(path)`, `other_profiles`). `[scope] include/exclude` globs.
- Reporting: `scan --group-depth N` aggregates per directory prefix (cli.py:83 `group_totals`);
  report.py has `function_debt`, `is_brain`, `file_report`, `totals`. Diff and gate compare
  per-file reports between base and head (gate.py `debt_check`, `_benchmark`).
- Gate rules that use the profile: debt over touched files may not rise; no new brain function;
  no new file smell. JSON meta records `profile` (e.g. "python@0+cpp@0") and backend versions.
- Calibration data: the nine Peclet repos (core geom amr morton flow pnm dem voro coupling) are
  being cloned shallow into
  /tmp/claude-1000/-home-eajfpeters-Code/6acb3080-ac42-4dda-bdfb-676e2b649813/scratchpad/peclet/peclet-<name>.
  Peclet's own suite scan (on their host, not here): suite debt 11162.9, 91 brain functions under
  cpp@0. Kokkos patterns: `Kokkos::parallel_for("name", policy, KOKKOS_LAMBDA(const int i) {...})`,
  `KOKKOS_INLINE_FUNCTION`, functor classes with `operator()`, header-only .hpp.

## What I want back (at most ~70 lines, plain text, no em dashes)

1. Layer design: the config schema (e.g. `[[layers]] name, include` with first-match, vs per-path
   profiles), how a file in no layer is handled, what scan/diff/gate JSON and text gain, and
   whether a layer may carry its own profile or gate policy now (e.g. device = ratchet-only) or
   only later. Say what you reject and why. Keep the gate's existing rules unchanged when no layer
   is configured (bit-identical output).
2. cpp@1 design per item (1)-(4): exact counting rule, how to implement on lizard 1.26 output plus
   our token scan (is a lambda splitter on tokens sound? how to find a kernel lambda's span, its
   captures, and subtract it from the parent's CCN/NLOC), thresholds and weights you propose, and
   the brain rule. Name the edge cases that would make a rule unsound (e.g. a lambda inside a
   macro argument, nested lambdas, `#if` inside a function signature, `#if 0` blocks).
3. The cheapest falsifier for each cpp@1 rule (a small C++ snippet and the expected numbers).
4. Order of work and what to defer.

Advice only: do not edit files. Do not spawn sub-agents yourself.
