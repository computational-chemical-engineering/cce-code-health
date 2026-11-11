# code-health-lab: state

Objective: build an agentic refactor loop (agent + skills + measurement tools) that raises
code health of a Python repo to ~10 without changing behaviour, building its own playbook;
importable into any repo. Demo target: OpenFlowsheet, judged before/after by CodeScene
(free Community Edition, public repo), loop itself optimises open-source metrics.

Position (2026-11-11):
- baseline.py: open-source proxy health (lizard CCN/NLOC/params, complexipy cognitive,
  AST nesting + bumpy road, 8-line window duplication, git churn). Thresholds and
  penalties at top of file. Proxy, NOT CodeScene's metric.
- Baseline OpenFlowsheet@a9e733e4 in baselines/openflowsheet-a9e733e4/summary.md:
  all 7.16, src 6.93 (NLOC-weighted); src 277 files, 10 below 4, 77 at 10.
- Venv: .venv (lizard 1.17+, radon, complexipy). Run: .venv/bin/python baseline.py REPO OUT

Next action: user signs up OpenFlowsheet at codescene.io and reports score + hotspots;
compare with proxy; then design the loop (behaviour gate: tests + bit-identical golden outputs).

Open decisions: scope of refactor (src only vs also tests/scripts/docs); CodeScene CLI access.
