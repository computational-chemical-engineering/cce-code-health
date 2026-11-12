# Decision register

| date | decision | rejected alternative | reason | evidence |
|---|---|---|---|---|
| 2026-11-11 | Central repo `cce-code-health`; per-repo state in `.codehealth/` | tooling copied into each repo | one versioned source, repos keep only their own state and playbook | chat discussion, docs/DESIGN.md |
| 2026-11-11 | Develop in the open (public, 0.x experimental), MIT | private until mature | public consumer repos cannot use private reusable workflows; pip/marketplace from private repo needs tokens everywhere | org repo list: openflowsheet, pymrm, peclet-* are public |
| 2026-11-11 | Loop optimises open-source metrics; CodeScene only judges | optimise against CodeScene directly | independent judge makes the demo credible; works on private repos and without an account | docs/DESIGN.md |
| 2026-11-11 | Python first, C++ (Peclet) second | both at once | C++ gate needs build/fp policy decisions first | docs/DESIGN.md |
| 2026-11-11 | Ratchet = base-vs-head comparison in `diff`, using base config | committed baseline.json, updated only upward | handles renames/splits, no merge conflicts between branches, survives metric upgrades | think review 2026-11-11, docs/DESIGN.md "Ratchet" |
| 2026-11-11 | Uncapped per-function debt is the gated and optimised quantity; 1..10 score display only | capped per-file score (baseline.py) | caps give no reward past 5 smells (local.py many_args x11) and per-file caps reward file splitting | think review; baseline.py:41-48 |
| 2026-11-11 | References regenerated from base commit, determinism checked, bitwise full-precision compare; mutant check for coverage | committed goldens, "covered" only | prevents behaviour changes slipping through as refactors | think review; docs/DESIGN.md "Behaviour gate" |
| 2026-11-11 | C++ strict gate built with -ffp-contract=off, no fast-math | tolerance by default | extracted code can change FMA fusion and bits; keep bitwise rule meaningful | think review |
| 2026-11-11 | Seed playbook ships in the plugin, never copied; repo holds local recipes + disables; evidence computed by CLI | copy seed into each repo; LLM-counted evidence | copied text drifts; LLM counts are unreliable | think review |
| 2026-11-11 | Per-campaign logs `runs/<campaign>.jsonl`; campaign PRs merged without squash | one shared log.jsonl; squash | shared append file conflicts; squash destroys recipe example commits | think review |
| 2026-11-12 | Two change classes: refactor (bitwise, no slowdown) and optimize (human-declared tolerance, measured speedup, debt not rising), separate commits and skills | one gate with tolerance for everything; strict bitwise only | performance-critical codes (Peclet, pymrm) need round-off-changing optimisations; separation keeps the bitwise detector sharp and bisect meaningful | user request 2026-11-12; tests/test_optimize.py |
