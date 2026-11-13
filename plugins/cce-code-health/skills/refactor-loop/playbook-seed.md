# Refactor recipes (seed, Python)

Shipped with the plugin; evidence starts empty. Repos record their own in
`.codehealth/playbook.md`. All recipes must keep behaviour bit-identical: same order of
side effects and floating-point operations, same exceptions and messages.

| id | smell | when | when not | recipe |
|---|---|---|---|---|
| extract-phase | long function, cognitive complexity | function does several phases in sequence (parse, validate, compute, format) | phases share many mutable locals; the phase needs 10+ inputs (do `parameter-object` first) | move each phase into a private `_phase(...)` returning what the next phase needs; caller reads as a list of steps |
| guard-clauses | deep nesting, bumpy road | nested `if` whose else-branches are early exits or errors | the else-branches carry real work in a specific order | invert the condition, return/raise early, dedent the main path; keep exception types and messages identical |
| decompose-conditional | complex conditional (boolops) | a condition with 3+ `and`/`or` | short-circuit order matters for side effects | name the parts as local booleans or a private predicate, preserving evaluation order |
| loop-body-extract | nesting inside loops | loop body with its own branching | body mutates many outer locals | move the body into `_handle_item(item, ...)` returning the update; keep iteration order |
| table-dispatch | many conditionals on one key | `if kind == "a": ... elif kind == "b": ...` with uniform branch shape | branches differ in arguments or control flow | dict from key to private handler; unknown-key path unchanged (same exception and message) |
| parameter-object | many parameters | the same group of 4+ parameters travels together through several private functions and forms one concept | public signatures (public_api rule); unrelated parameters | private dataclass for the group, used only between private functions |
| split-flag-argument | boolean flag parameter selecting behaviour | private function with a `mode`/`flag` switching two paths | public function | two private functions; callers call the right one |
| consolidate-duplicate | duplication | identical blocks in 2+ places in scope | blocks differ in literals or order | one private helper; both call sites call it |
| check-ladder | long function, many conditionals, left over after extract-phase | a run of 4+ rungs with the same shape (`if <check>: return <result>` or `raise`), evaluated in order | rungs differ in shape, bind locals that later rungs read, or mutate state | an ordered tuple of `(predicate, result)` pairs (private functions or small lambdas) and one loop returning the first match; same order, same short-circuit, same fall-through; do it in one step, never by peeling the tail rung by rung |
| replace-temp-with-query | long function | temporaries computed once and read far below | computation is expensive or has side effects | small private function computing the value where needed, only if pure and cheap |

## Anti-patterns (review rejects; they lower the score without helping)

- **Lifting one branch out of a structural recursion** so the extracted helper and the
  original call each other: mutual recursion for a score drop. Keep a recursive walk in
  one function, or extract the whole per-node step.
- **Grab-bag helper**: its parameters feed disjoint statements, or its name covers only
  part of its body. Split by purpose or leave the code in place.
- **Peeling a ladder rung by rung**: each step moves the last `if ...: return` into a
  helper. Use `check-ladder` once instead.
- **Same complexity, new address**: a helper that carries all the branching of the
  original while the caller becomes one line.
- **A phase helper with a parameter list as long as the phase**: extract-phase output with
  more than 10 parameters is a new hotspot (many-parameters debt, often a brain function of its
  own), so the step gains almost nothing. Peclet dem S1 step 3: a 19-parameter helper, file
  debt 332.1 -> 331.2. Group the inputs with `parameter-object` (C++: a launch-params struct)
  first, or extract a smaller phase. In C++ the same coupling can hide in a lambda that
  captures the caller's locals by reference (`[&]`): the metric does not count captures, so
  review does.
- **Fixing findings by rote** (C++): a refactor step never changes a parameter or capture
  only to silence a clang-tidy check. Example: `performance-unnecessary-value-param` on
  `Kokkos::View` parameters; Views are reference-counted handles passed by value on purpose
  (that is how they reach device lambdas). The findings tier is a ratchet against new
  findings, not a to-do list.

Evidence: OpenFlowsheet S17 review 1 (2026-11-13), 12 steps, 2 review-rejects; Peclet dem S1
(2026-11-13), 3 extract-phase steps on demSolveContacts, all accepted.
