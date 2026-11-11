# Decision register

| date | decision | rejected alternative | reason | evidence |
|---|---|---|---|---|
| 2026-11-11 | Central repo `cce-code-health`; per-repo state in `.codehealth/` | tooling copied into each repo | one versioned source, repos keep only their own state and playbook | chat discussion, docs/DESIGN.md |
| 2026-11-11 | Develop in the open (public, 0.x experimental), MIT | private until mature | public consumer repos cannot use private reusable workflows; pip/marketplace from private repo needs tokens everywhere | org repo list: openflowsheet, pymrm, peclet-* are public |
| 2026-11-11 | Loop optimises open-source metrics; CodeScene only judges | optimise against CodeScene directly | independent judge makes the demo credible; works on private repos and without an account | docs/DESIGN.md |
| 2026-11-11 | Python first, C++ (Peclet) second | both at once | C++ gate needs build/fp policy decisions first | docs/DESIGN.md |
