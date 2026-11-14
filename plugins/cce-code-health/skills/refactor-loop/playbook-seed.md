# Refactor recipes (seed, Python)

Shipped with the plugin; evidence starts empty. Repos record their own in
`.codehealth/playbook.md`. All recipes must keep behaviour bit-identical: same order of
side effects and floating-point operations, same exceptions and messages.

| id | smell | when | when not | recipe |
|---|---|---|---|---|
| extract-phase | long function, cognitive complexity | function does several phases in sequence (parse, validate, compute, format) | phases share many mutable locals; the phase needs 10+ inputs (do `parameter-object` first); the remainder only threads the same locals through successive phases, or a step would gain less than about 2 points (stop there); the remainder carries state across loop iterations (a counter, a `continue`) or an exception handler reads locals the helper would assign | move each phase into a private `_phase(...)` returning what the next phase needs; caller reads as a list of steps |
| guard-clauses | deep nesting, bumpy road | nested `if` whose else-branches are early exits or errors | the else-branches carry real work in a specific order | invert the condition, return/raise early, dedent the main path; keep exception types and messages identical |
| decompose-conditional | complex conditional (boolops) | a condition with 3+ `and`/`or` | short-circuit order matters for side effects | name the parts as local booleans or a private predicate, preserving evaluation order |
| loop-body-extract | nesting inside loops | loop body with its own branching | body mutates many outer locals | move the body into `_handle_item(item, ...)` returning the update; keep iteration order |
| table-dispatch | many conditionals on one key | `if kind == "a": ... elif kind == "b": ...` with uniform branch shape | branches differ in arguments or control flow | dict from key to private handler; unknown-key path unchanged (same exception and message) |
| parameter-object | many parameters | the same group of 4+ parameters travels together through several private functions and forms one concept | public signatures (public_api rule); unrelated parameters; the fields are produced in different phases (the object would be built late for one consumer, or be mutable and partly filled) | private dataclass for the group, used only between private functions |
| launch-params-struct (C++) | many parameters on host launchers of device kernels | 4+ parameters (Views, material constants, schedules) travel together into several launchers and form one concept | the group is not one concept (a bag of the caller's locals); a member that is host-only or not trivially copyable would reach a device lambda | a struct passed by `const&` to the launcher, unpacked at the top into locals with the old names; kernel bodies and captures stay byte-identical, so review reads only the signature diff |
| split-flag-argument | boolean flag parameter selecting behaviour | private function with a `mode`/`flag` switching two paths | public function | two private functions; callers call the right one |
| consolidate-duplicate | duplication | identical blocks in 2+ places in scope | blocks differ in literals or order | one private helper; both call sites call it |
| check-ladder | long function, many conditionals, left over after extract-phase | a run of 4+ rungs with the same shape (`if <check>: return <result>` or `raise`), evaluated in order | rungs differ in shape, bind locals that later rungs read, or mutate state | an ordered tuple of `(predicate, result)` pairs (private functions or small lambdas) and one loop returning the first match; same order, same short-circuit, same fall-through; do it in one step, never by peeling the tail rung by rung |
| replace-temp-with-query | long function | temporaries computed once and read far below | computation is expensive or has side effects | small private function computing the value where needed, only if pure and cheap |

## Restructure (module splits, human-approved plans)

- A split moves top-level statements verbatim, so section-header comments travel with the
  first statement under them. After the split, check every header in the old and new files
  against what it now heads, and every docstring that names the old module as the home of a
  moved name; fix them in an accepted-neutral follow-up.
- Comments outside the campaign scope (frozen or other packages' files) that name a moved
  module fail the gate's `scope` and `frozen` checks: list them for the owner, never edit them
  in a gated step.
- Before each split, re-derive the move set and the importer list at the base and write the
  expected `codehealth arch` numbers into the plan before the run; a mismatch stops the split.

## Anti-patterns (review rejects; they lower the score without helping)

- **Lifting one branch out of a structural recursion** so the extracted helper and the
  original call each other: mutual recursion for a score drop. Keep a recursive walk in
  one function, or extract the whole per-node step.
- **Extracting from mutually recursive closures** over shared captured values: lift them all
  into a small class, or leave them.
- **Half a design table**: a dispatch whose branches are the families of one table; lifting
  only the most nested family earns nesting credit and splits rungs the families share. Lift
  every family or none.
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

Evidence: OpenFlowsheet S17 (merged 2026-11-13, d90b498f): 87 run-log entries, extract-phase 59
accepted, 5 gate-fails, 1 review-reject; 4 module splits all met their planned arch numbers;
reviews 1 to 11, 2 rejects (its .codehealth/playbook.md has the per-entry evidence); Peclet dem S1
(2026-11-13), 3 extract-phase steps on demSolveContacts, all accepted; Peclet dem S2 (2026-11-13),
3 launch-params-struct steps (12 halo kernels), all accepted on the first gate, debt 1858.2 -> 1799.3.
