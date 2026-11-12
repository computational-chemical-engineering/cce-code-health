# Refactor recipes (seed, Python)

Shipped with the plugin; evidence starts empty. Repos record their own in
`.codehealth/playbook.md`. All recipes must keep behaviour bit-identical: same order of
side effects and floating-point operations, same exceptions and messages.

| id | smell | when | when not | recipe |
|---|---|---|---|---|
| extract-phase | long function, cognitive complexity | function does several phases in sequence (parse, validate, compute, format) | phases share many mutable locals | move each phase into a private `_phase(...)` returning what the next phase needs; caller reads as a list of steps |
| guard-clauses | deep nesting, bumpy road | nested `if` whose else-branches are early exits or errors | the else-branches carry real work in a specific order | invert the condition, return/raise early, dedent the main path; keep exception types and messages identical |
| decompose-conditional | complex conditional (boolops) | a condition with 3+ `and`/`or` | short-circuit order matters for side effects | name the parts as local booleans or a private predicate, preserving evaluation order |
| loop-body-extract | nesting inside loops | loop body with its own branching | body mutates many outer locals | move the body into `_handle_item(item, ...)` returning the update; keep iteration order |
| table-dispatch | many conditionals on one key | `if kind == "a": ... elif kind == "b": ...` with uniform branch shape | branches differ in arguments or control flow | dict from key to private handler; unknown-key path unchanged (same exception and message) |
| parameter-object | many parameters | the same group of 4+ parameters travels together through several private functions and forms one concept | public signatures (public_api rule); unrelated parameters | private dataclass for the group, used only between private functions |
| split-flag-argument | boolean flag parameter selecting behaviour | private function with a `mode`/`flag` switching two paths | public function | two private functions; callers call the right one |
| consolidate-duplicate | duplication | identical blocks in 2+ places in scope | blocks differ in literals or order | one private helper; both call sites call it |
| replace-temp-with-query | long function | temporaries computed once and read far below | computation is expensive or has side effects | small private function computing the value where needed, only if pure and cheap |
