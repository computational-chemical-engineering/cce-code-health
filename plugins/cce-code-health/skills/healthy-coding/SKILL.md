---
name: healthy-coding
description: Coding standard for ordinary feature and bug-fix work in a repository that has .codehealth/config.toml - follow the repo's playbook rules for new code, keep functions small and flat, respond to codehealth hook feedback, and check `codehealth diff` before finishing so the code-health debt does not rise. Use whenever writing or changing code in such a repo, also when not asked about code health. Not for refactor or optimisation campaigns (use refactor-loop or optimize-loop).
---

# Healthy coding

Campaigns lower the debt; this skill keeps it from coming back. It applies to every code
change in a repo with `.codehealth/config.toml`, by any agent.

**The repository's own process overrides this skill** (work packages, lanes, review rules).

## Before writing

1. Read `.codehealth/playbook.md`, section **Rules for new code** (at most 10 bullets,
   learned in this repo's campaigns). They are binding, like a style guide.
2. Note the profile thresholds in `.codehealth/config.toml` (Python profile python@0:
   cognitive complexity 15, function length 70 lines of code, nesting 3, `if`/loop
   "bumps" 2, parameters 5, boolean operators per condition 2).

## While writing

- One function, one job. Split a function into private phase helpers (`_parse`,
  `_validate`, `_compute`) before it reaches the thresholds, not after.
- Flat over nested: guard clauses for errors and early exits; a loop body with its own
  branching becomes a private helper.
- Name complex conditions (a local boolean or a small private predicate).
- More than 5 parameters that travel together: a private dataclass, only between private
  functions. Never change a public signature for health alone.
- A run of similar `if <check>: return <x>` rungs: an ordered table of
  `(predicate, result)` and one loop, never a growing ladder.
- No copy-paste of more than a few lines: extract a helper.
- Respect the repo's architecture rules (`[architecture]` in the config): no imports from a
  higher layer, no forbidden imports, no new import cycles. `codehealth arch` lists them.
- Avoid the anti-patterns in the seed playbook (helpers that only move the same
  complexity, grab-bag helpers, mutual recursion for a score drop).

## Hook feedback

If the plugin's edit hook reports `codehealth: <file>: debt a -> b` with function details,
fix it in the next edit: simplify the named function along the lines above. If the rise
is deliberate (for example a numerical kernel that is clearer as one function), say so in
your reply to the user with the reason; do not add a `codehealth: allow` suppression
unless the user agrees, and then always with `-- reason`.

## Docs (repos with a `[docs]` table)

- Docs change in the same commit as the code they describe. When you rename, move or remove
  a file, function, class or test, update every doc that names it; `codehealth diff` lists
  the docs that reference changed files under "review" and fails on references that stopped
  resolving.
- Write references that a tool can check: backticked repo paths (`src/pkg/x.py`), pytest ids
  (`tests/test_x.py::test_y`), dotted names (`pkg.mod.func`). Never cite a path, test or name
  you have not seen in the tree: `codehealth diff` reports every new reference that does not
  resolve (planned files in a brief are fine; say they are planned).
- Prefer a name over a line anchor (`pkg/x.py::func` or `pkg.x.func`, not `pkg/x.py:120`):
  anchors drift with every edit. In state files, anchors are fine for the current position.
- New public functions, classes and methods get a docstring; when a docstring lists
  parameters, keep it equal to the signature (the hook reports both).
- Do not edit records (decision logs, ADRs, reviews) to fix old references: they describe the
  past.

## Before finishing

Run `codehealth diff --base <merge-base with the main branch>` (the repo's CI runs the
same). It fails when debt over touched files rises, a new large file or brain function
appears, a suppression is invalid, (with `[architecture]`) a new import violation or
cycle appears, or (with `[docs]`) a doc reference rots or is invented, or a new public name
lacks a docstring. Fix it, or report it to the user with the numbers. Never edit
`.codehealth/config.toml` or the playbook to make it pass.

## Never

- Lower debt by gaming the metric: splitting a function into pieces with no meaning of
  their own, or hiding branches in unreadable data structures.
- Mix a large clean-up into a feature change: propose a refactor campaign instead.
