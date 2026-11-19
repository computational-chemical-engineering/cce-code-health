# python@1 design (think, 2026-11-17)

Advice for the python@1 campaign (brief docs/briefs/py1-design.md, state docs/PY1_STATE.md).
Numbers come from a prototype in docs/py1/proto/ (measure.py, clones.py, fit.py, ablate.py) run on
OpenFlowsheet a9e733e4 (CodeScene 8385360) and pymrm 230bbc3 (CodeScene 8475071 table in
pymrm-health-notes). The prototype reproduces python@0's Spearman values to 0.001.

## 1. Thresholds and weights

Debt per function = sum_m w_m * max(0, value_m - threshold_m), as in python@0.

| metric | python@0 | python@1 | what changes and why |
|---|---|---|---|
| cognitive | > 15, w 1 | > 9, w 1 | flag at 10. OFS functions >= 10: 2398 vs CodeScene Complex Method 2065 (src 564 vs 581) |
| nloc | > 70, w 0.1 | > 70, w 0.1 | docstrings already excluded; Large Method r 0.993 per file |
| nesting | > 3, w 3 | > 3, w 3 | Deep Nested: 267 vs 266 functions, r 0.951 |
| bumps | > 2, w 2 | > 1, w 2 | new definition (section 2); flag at 2 like CodeScene |
| params | > 5, w 1 | > 4, w 1 | public and private alike (section 5); 953 vs 822, src 484 vs 469, r 0.996 |
| boolops | > 2, w 1 | > 1, w 1 | measured in if, elif and while conditions and match guards only. Complex Conditional sum abs diff 579 -> 120 |
| state_writes | n/a | > 0, w 0.5 | new, section 3 |
| clones | n/a | > 0, w 0.2 | new, per cloned statement, section 4 |

Unchanged: brain (cognitive > 40 and nloc > 70), varargs 3, large 1000, huge 2000, cross-file
duplication (8-line window, file smell above 10 %), complexipy 8.3.0. Moving to complexipy 9 changes
cognitive values, so it is python@2. Display score: add `class_state` (1.0 per flagged class, cap
2.0); every other entry follows the new thresholds.

Rejected for python@1: LCOM4 in debt (queued 2026-11-13). LCOM4 rates _NewtonRun cohesive (max 1),
and the cheapest way to lower LCOM4 is to make methods share attributes: that rewards the
anti-pattern. CodeScene's Low Cohesion on numjac, coupling and convect is module-level and is fixed
by file splits, which refactor mode does not allow. LCOM4 stays informational; a decision row is
needed. Many Conditionals: no single-file rule reproduces it (best: file `if` count >= 24, accuracy
0.85 against a base rate of 0.76) and it overlaps cognitive. Positional tuple reads (pymrm proposal):
not about class state, and numpy indexing makes them noisy.

Spearman(CodeScene, -debt), cumulative ablation (ablate.py; state and clones use the prototype):

| step | tests | scripts | docs | bench | src (held out) | pymrm 14 |
|---|---|---|---|---|---|---|
| python@0 | 0.720 | 0.922 | 0.932 | 0.908 | 0.899 | 0.753 |
| + cognitive > 9 | 0.831 | 0.956 | 0.956 | 0.922 | 0.925 | |
| + params > 4 | 0.839 | 0.957 | 0.956 | 0.925 | 0.930 | |
| + boolops conditions > 1 | 0.846 | 0.951 | 0.954 | 0.927 | 0.939 | |
| + bumps runs > 1 | 0.839 | 0.949 | 0.952 | 0.929 | 0.942 | |
| + state, clones (python@1) | 0.839 | 0.949 | 0.953 | 0.929 | 0.943 | 0.908 |

Weights: grid cognitive {0.5, 1}, bumps {1, 1.5, 2}. All cells are within 0.012 of each other on
every fit area. Rule: keep the python@0 weight unless the mean fit-set gain is >= 0.01. No cell
qualifies (cognitive 0.5 gains 0.0055), so python@0's weights stay.

## 2. Bumpy road

A function's statements are walked in source order (header before body, nested defs included as
in python@0, classes skipped). Levels are counted by if, for, async for, while and match. Try and
with do not count, and elif stays on its if's level. level(s) = number of enclosing counted
statements, + 1 if s is itself counted. A bump is a maximal run of consecutive statements with
level >= 2; any statement with level < 2 ends it. bumps = number of runs.

Agreement with CodeScene (functions with bumps >= 2, OFS 1192 files): python@0 562 vs 1088, no
false file, 176 files missed; plain statement runs 1543 (115 false files); the level runs defined
here 1105 (sum abs diff 383, r 0.911); the same with try as a level 1146. pymrm: python@0 7
vs 16, runs 18 (solve 7 vs 5, numjac 3 vs 5). Functions: `python_backend.bumps_runs`.

## 3. Class-state smell

Per class C (nested classes on their own; methods are the defs directly in C's body):
- Constructors K: __init__, __post_init__, __new__, __init_subclass__, __setstate__, __enter__,
  setUp, setUpClass, setup_method and setup_class, plus constructor helpers. A constructor helper is
  a private, non-dunder method with at least one `self.m()` call site in C, all of them in K
  (fixpoint). Excluded methods X: staticmethod, classmethod, `*.setter`, `*.deleter`.
- Stores to self (the first positional parameter) count as writes to a key: `self.a` -> a,
  `self.a.b` -> a.b, `self.a["lit"]` -> a[lit] and a, `self.a[expr]` -> a,
  `setattr(self, "lit", v)` -> lit. Stores are Assign, AugAssign, AnnAssign with a value, Delete,
  for and with targets, with tuple unpacking.
- W(a) = methods outside K and X that write a. R(a) = methods outside K and X that load a.
  a is a channel when W(a) is not empty, a is not a method name, and either R(a) minus W(a) is not
  empty or |W(a)| >= 2.
- Function metric `state_writes(m)` = number of channels m writes (0 for K and X). Class
  state_writes = sum over channels of |W(a)|. Display smell `class_state` when class state_writes
  > 8. It goes in a new `class_smells` list, not in `file_smells`, so that moving a class to a new
  file is not a "new file smell".

Measured (prototype without the a.b and setattr keys): _NewtonRun 54 (35 channels, 4 dict keys);
OFS _Planner 18, docs Fluid 18; pymrm _Reconstructor 14; gallery _ThetaTVD 10, _Linear1D 10. Below
the flag: _Continuation 5 (blind review: very good), NumJac 6, BlockLayout 5. The constructor-helper
guard removed gallery Monolith (17 channels without it). OFS: 7 of 1345 classes have > 4.

Route into gate.py (the c2 requirement). (a) state_writes is a function metric, so it flows through
function_debt, file debt, debt_check totals, function_debts and zones without new plumbing.
(b) Debt alone can be traded, so debt_check gets a ratchet. Let S be the raw class state_writes,
ignoring allow comments, over the touched in-scope files. Error if S(head) > S(base), or if any
class whose qualname exists in the base touched files has a higher value at head. Message:
"class state rose: _NewtonRun 54 -> 57 (_phase1 writes a, b; _phase2 writes c)". (c) The mode is
passed in: error in refactor, restructure and interface modes; a note in optimize mode, where a
cache field is legitimate. (d) A step that extracts helpers communicating through new fields adds
channels, so S rises and the step fails, however far function debt falls. Making state local, or
returning a NamedTuple, lowers S and earns 0.5 per write. A split into a new state class leaves the
total unchanged and passes. `codehealth diff` gets the same ratchet over the branch.

Known blind spot (pymrm c3 N3, 04a811e, 2026-11-18): a field moved into a helper object whose one
method both writes and reads it (`_Residual.evaluate` counts `nfev`) stops being a channel, and the
owner's read `self.residual.nfev` is not seen (no writer in the owner class), so S falls by its
writers. Judged correct for a counter read only for reporting; it would hide coupling if the owner
branched on it. Seeing it needs cross-class reads matched to writes (attribute name or type
inference): a python@2 candidate, not a python@1 change. Adopter rule meanwhile: a moved field may
be read from another object only for reporting, never for a control decision.

Known blind spot (agentic_science, 2026-11-19): module-level code (top-level statements of a
script or a jupytext py:percent page) counts toward file NLOC and large_file but carries no
function debt, so a page written as top-level cells scores low debt. Open-weight page builds had
module NLOC share 0.70 to 0.89 against 0.55 for the reference page. Seeing it needs a module unit
(cognitive, nesting, bumps of the top-level body): a python@2 candidate. Meanwhile, report
module NLOC share (file nloc minus the sum of function nloc) next to debt for script-like files.

## 4. Short-block duplication (intra-file, AST)

The step sequences in solve.py differ in access paths (`be` vs `self.be`) and constants, so no
line window can see them. Within one file, over statement lists inside functions (docstrings
dropped): windows of K = 3 consecutive statements with >= 30 AST nodes and at least one compound
statement (if, for, while, with, try, match). The key is a structural dump with Name, Attribute,
Constant, JoinedStr and Subscript loads replaced by one atom; keyword names and node types are
kept. Equal keys at >= 2 non-overlapping places form a group. Function metric `clones` = number of
statements (nested ones included) of the function inside any occurrence. Counting statements, not
lines, keeps the metric stable under formatting. Without the compound guard, argparse lists and
assignment tables dominate (OFS cli.py 17 %). With it: solve.py groups 662-665/703-706 and
693-696/865-869 (two of the three copies), OFS src 42 of 277 files and 0.8 % of lines, tests
0.2 %, scripts 0.4 %. Spot check graph/matching.py: a real mirrored BFS. Cross-file duplication is
unchanged.

## 5. Public vs private parameters

Same charge for both. Refactor mode cannot change public signatures, so the gate delta is the same
either way. Charging them rewards interface-mode fixes (an options object). An underscore rename
must not move debt (the zones principle). It also matches CodeScene (r 0.996). Each function
record gains `public` (no `_`-prefixed component in the qualname, dunders public), so a campaign
can leave frozen-API debt out of its target selection.

## 6. Calibration and validation

Fit set: OFS tests, scripts, benchmarks and docs. Thresholds are CodeScene's observed flag levels,
confirmed by per-file agreement. Weights only by the grid rule in section 1. state_writes and
clones weights are policy (rank effect < 0.002 on OFS), validated by G3, G4, G8 and G11. Held out:
OFS src (G1), pymrm src post-c1 (G2), blind reviews (G3, G4). Caveat: this prototype printed the
src and pymrm columns. No choice differs from the fit-set choice, but any later change of a
threshold or weight spends the held-out sets, and a fresh CodeScene project is then needed.
Order: G0, then implement, G5 to G12, then G1 to G4 on the frozen candidate commit.

## 7. Files and functions

- profiles.py: PY1_METRICS = METRICS + ("state_writes", "clones"). New Profile fields `py_rules`
  (frozenset: "bump_runs", "cond_boolops", "state", "clones"), `class_state_threshold` = 8,
  `clone_window` = 3, `clone_min_nodes` = 30. PROFILES["python@1"] = replace(python@0, ...).
- python_backend.py: `analyze(..., rules=frozenset())` (python@0 path untouched), `bumps_runs`,
  `boolops_conditions`, FunctionMetrics gets `state_writes`, `clones` and `public` defaulting to
  absent. Allow names per profile: SMELL_NAMES for python@0 must not change, or python@0
  `suppressions[].valid` changes.
- New classstate.py (`channels(cls)`, `method_state_writes(cls)`) and pyclones.py
  (`clone_statements(tree, window, min_nodes) -> {qualname: n}`), both pure AST like cohesion.py.
- cli.py `_analyze`: pass p.py_rules. report.py: class records gain state_writes and channels,
  `class_smells`, the display entry. gate.py `debt_check`: ratchet and mode (3b, 3c). diff: the same.

## 8. For the orchestrator or the user

- G4 cannot run as written. Only 6 classes exceed 8 state writes across OFS, pymrm, pymrm-gallery,
  pymrm-book and particle_model, so 20 flagged classes cannot be sampled. Proposed G4': a blind
  reviewer (flags hidden) rates 20 classes: all flagged ones plus the highest unflagged by
  state_writes, shuffled. Pass if >= 5 of the 6 flagged are rated "methods communicate through
  shared mutable state, a real cost", and <= 3 of the unflagged are rated so with severity high.
- Decision rows needed: LCOM4 stays out of debt (reverses the 2026-11-13 queue item); public
  params charged; class-state ratchet is an error except in optimize mode.
- Unrelated, found on the way: the gate does not refuse new `# codehealth: allow` comments in a
  step, so a loop can suppress function debt. The ratchet in 3b counts raw values for that reason.
  A separate rule (valid allows over the touched files may not rise in refactor mode) closes the
  hole for every metric.
- Known escapes of the state measure: writes through a local alias (`s = self.s; s.x = v`) and
  mutating calls (`self.items.append`). A reviewer sees these; the playbook should name the
  anti-pattern "extract method that communicates through new mutable fields".
