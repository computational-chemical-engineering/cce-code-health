"""codehealth command line. Exit codes: 0 pass, 1 regression, 2 tool error."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import tempfile

from . import __version__, config, gitio
from . import gate as gatemod
from .duplication import duplicated_lines
from .python_backend import analyze
from .report import file_report

EPS = 1e-9


def analyze_ref(repo: Path, ref: str | None, cfg: config.Config,
                only: set[str] | None = None) -> dict[str, dict]:
    """Reports for in-scope files at ref (None = worktree). Duplication uses the whole scope."""
    files = [p for p in gitio.ls_files(repo, ref) if cfg.in_scope(p)]
    texts = gitio.read_files(repo, ref, files)
    dup = duplicated_lines(texts, cfg.profile.dup_window)
    p = cfg.profile
    return {path: file_report(analyze(path, texts[path], p.varargs_params), dup.get(path, 0), p)
            for path in files if path in texts and (only is None or path in only)}


def totals(reports: dict[str, dict]) -> dict:
    nloc = sum(r["nloc"] for r in reports.values())
    debt = sum(r["debt"] for r in reports.values())
    health = sum(r["health"] * r["nloc"] for r in reports.values()) / nloc if nloc else 10.0
    fns = [f for r in reports.values() for f in r["functions"]]
    return {"files": len(reports), "functions": len(fns), "nloc": nloc,
            "debt": round(debt, 2), "debt_per_kloc": round(1000 * debt / nloc, 2) if nloc else 0.0,
            "health": round(health, 2),
            "functions_with_debt": sum(f["debt"] > 0 for f in fns),
            "brain_functions": sum(f["brain"] for f in fns),
            "file_smells": sum(len(r["file_smells"]) for r in reports.values()),
            "errors": sum(r["error"] is not None for r in reports.values())}


def meta(repo: Path, cfg: config.Config, **extra) -> dict:
    return {"tool": "cce-code-health", "version": __version__, "profile": cfg.profile.name,
            "config": cfg.source, "repo": repo.name, **extra}


def write_json(path: str | None, data: dict) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=1))


def cmd_scan(a) -> int:
    repo = Path(a.repo).resolve()
    ref = gitio.rev(repo, a.ref) if a.ref else None
    cfg = config.load(repo, ref)
    reports = analyze_ref(repo, ref, cfg)
    t = totals(reports)
    write_json(a.json, {"meta": meta(repo, cfg, ref=ref or "worktree"), "totals": t, "files": reports})
    print(f"{'file':60} {'nloc':>6} {'debt':>8} {'health':>6}  file smells")
    for r in sorted(reports.values(), key=lambda r: -r["debt"])[:a.top]:
        print(f"{r['path'][-60:]:60} {r['nloc']:6} {r['debt']:8.1f} {r['health']:6.1f}  {','.join(r['file_smells'])}")
    print(f"SCAN {repo.name}@{(ref or 'worktree')[:10]} profile={cfg.profile.name}: "
          f"{t['files']} files, {t['nloc']} NLOC, debt {t['debt']} ({t['debt_per_kloc']}/kNLOC), "
          f"health {t['health']}, functions with debt {t['functions_with_debt']}/{t['functions']}, "
          f"brain {t['brain_functions']}, file smells {t['file_smells']}, errors {t['errors']}")
    return 0


def cmd_diff(a) -> int:
    repo = Path(a.repo).resolve()
    head = gitio.rev(repo, a.head) if a.head else None
    base = gitio.merge_base(repo, a.base, head or "HEAD")
    cfg = config.load(repo, base)                    # base config: a change cannot loosen it
    ch = [c for c in gitio.changes(repo, base, head)
          if (c[1] and cfg.in_scope(c[1])) or (c[2] and cfg.in_scope(c[2]))]
    base_paths = {c[1] for c in ch if c[1] and cfg.in_scope(c[1])}
    head_paths = {c[2] for c in ch if c[2] and cfg.in_scope(c[2])}
    rb = analyze_ref(repo, base, cfg, base_paths)
    rh = analyze_ref(repo, head, cfg, head_paths)
    db = sum(r["debt"] for r in rb.values())
    dh = sum(r["debt"] for r in rh.values())

    failures = []
    if dh > db + EPS:
        failures.append(f"debt over touched files rose {db:.2f} -> {dh:.2f}")
    counterpart = {c[2]: c[1] for c in ch if c[2]}
    base_brains = {f["name"] for r in rb.values() for f in r["functions"] if f["brain"]}
    rows = []
    for path, r in sorted(rh.items()):
        old = rb.get(counterpart.get(path) or "")
        new_smells = set(r["file_smells"]) - set(old["file_smells"] if old else [])
        if new_smells:
            failures.append(f"{path}: new file smell(s) {', '.join(sorted(new_smells))}")
        for f in r["functions"]:
            if f["brain"] and f["name"] not in base_brains:
                failures.append(f"{path}:{f['line']} {f['name']}: new brain function "
                                f"(cognitive {f['cognitive']}, nloc {f['nloc']})")
        old_sup = {(tuple(s["smells"]), s["reason"]) for s in (old["suppressions"] if old else [])}
        for s in r["suppressions"]:
            if not s["valid"]:
                failures.append(f"{path}:{s['line']}: invalid suppression (needs known smell and '-- reason')")
            elif (tuple(s["smells"]), s["reason"]) not in old_sup:
                rows.append(f"  new suppression {path}:{s['line']} {','.join(s['smells'])} -- {s['reason']}")
    for st, bp, hp in ch:
        d0 = rb[bp]["debt"] if bp in rb else 0.0
        d1 = rh[hp]["debt"] if hp in rh else 0.0
        if abs(d1 - d0) > EPS:
            name = hp if bp in (None, hp) else f"{bp} -> {hp}"
            rows.append(f"  {st} {name}: debt {d0:.1f} -> {d1:.1f}")
    write_json(a.json, {"meta": meta(repo, cfg, base=base, head=head or "worktree"),
                        "debt_base": round(db, 3), "debt_head": round(dh, 3),
                        "failures": failures, "base": rb, "head": rh})
    print("\n".join(rows))
    for f in failures:
        print(f"  FAIL {f}")
    verdict = "FAIL" if failures else "PASS"
    print(f"DIFF {verdict} {repo.name} base={base[:10]} head={(head or 'worktree')[:10]} "
          f"profile={cfg.profile.name}: {len(ch)} touched files, debt {db:.2f} -> {dh:.2f}, "
          f"{len(failures)} failure(s)")
    return 1 if failures else 0


def cmd_hotspots(a) -> int:
    repo = Path(a.repo).resolve()
    cfg = config.load(repo, None)
    reports = analyze_ref(repo, None, cfg)
    counts = gitio.commit_counts(repo, a.since)
    rows = sorted(({"path": p, "commits": counts.get(p, 0), "debt": r["debt"],
                    "health": r["health"], "nloc": r["nloc"],
                    "rank": counts.get(p, 0) * r["debt"]} for p, r in reports.items()),
                  key=lambda x: -x["rank"])
    write_json(a.json, {"meta": meta(repo, cfg, since=a.since), "hotspots": rows})
    print(f"{'file':60} {'commits':>7} {'debt':>8} {'health':>6} {'rank':>9}")
    for x in rows[:a.top]:
        print(f"{x['path'][-60:]:60} {x['commits']:7} {x['debt']:8.1f} {x['health']:6.1f} {x['rank']:9.0f}")
    print(f"HOTSPOTS {repo.name}: {len(rows)} files ranked by commits x debt"
          + (f" since {a.since}" if a.since else ""))
    return 0


def cmd_self_test(a, repo: Path, log: Path) -> int:
    base = gitio.rev(repo, "HEAD")
    cfg = (config.parse(Path(a.config).read_text(), f"override:{a.config}") if a.config
           else config.load(repo, base))
    ok, checks, what = gatemod.self_test(repo, cfg, log, a.mutate)
    write_json(a.json, {"meta": {"tool": "cce-code-health", "version": __version__, "self_test": True,
                                 "mutation": what, "log": str(log)},
                        "checks": [c.__dict__ for c in checks]})
    print(f"  mutation: {what}")
    for c in checks:
        label = "detected" if c.ok else "BASELINE" if "without mutation" in c.detail else "MISSED  "
        print(f"  {label} {c.name:12} {c.seconds:7.1f} s  {c.detail}")
    kind = gatemod.self_test_kind(cfg)
    print(f"SELF-TEST {'PASS' if ok else 'FAIL'} {repo.name}: "
          f"{sum(c.ok for c in checks)}/{len(checks)} {kind} tiers detected the mutation (log {log})"
          if what != "none (baseline failed)" else
          f"SELF-TEST FAIL {repo.name}: {len(checks)} tier(s) fail without mutation (log {log})")
    return 0 if ok else 1


def cmd_gate(a) -> int:
    repo = Path(a.repo).resolve()
    log = Path(a.log) if a.log else Path(tempfile.gettempdir()) / f"codehealth-gate-{repo.name}.log"
    if a.self_test:
        return cmd_self_test(a, repo, log)
    checks, base, ref_base = gatemod.gate(repo, a.base, a.cadence, log,
                                          Path(a.config).resolve() if a.config else None, a.mode,
                                          a.ref_base)
    write_json(a.json, {"meta": {"tool": "cce-code-health", "version": __version__, "base": base,
                                 "ref_base": ref_base, "head": "worktree", "cadence": a.cadence, "mode": a.mode, "log": str(log)},
                        "checks": [c.__dict__ for c in checks]})
    for c in checks:
        print(f"  {'ok  ' if c.ok else 'FAIL'} {c.name:12} {c.seconds:7.1f} s  {c.detail}")
    failed = [c.name for c in checks if not c.ok]
    verdict = "FAIL" if failed else "TRIAL" if a.config else "PASS"
    print(f"GATE {verdict} {repo.name} base={base[:10]} ref-base={ref_base[:10]} "
          f"head=worktree mode={a.mode} cadence={a.cadence}: {len(checks) - len(failed)}/{len(checks)} checks passed"
          + (f", failed: {', '.join(failed)}" if failed else "")
          + (" [TRIAL: config override, not valid as CI evidence]" if a.config else "") + f" (log {log})")
    return 1 if failed else 3 if a.config else 0


def cmd_config(a) -> int:
    repo = Path(a.repo).resolve()
    if not a.check:
        print("codehealth: error: config needs --check", file=sys.stderr)
        return 2
    path = Path(a.config) if a.config else repo / config.CONFIG_PATH
    if not path.is_file():
        print(f"CONFIG FAIL {path}: no such file")
        return 1
    info, problems = config.check(path.read_text())
    for line in info:
        print(f"  {line}")
    for p in problems:
        print(f"  PROBLEM {p}")
    print(f"CONFIG {'FAIL' if problems else 'OK'} {path}: {len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="codehealth", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (("scan", cmd_scan, "score every in-scope file"),
                            ("diff", cmd_diff, "ratchet: compare merge-base and head"),
                            ("hotspots", cmd_hotspots, "rank files by commits x debt"),
                            ("gate", cmd_gate, "behaviour gate: static rules and tiers, base vs worktree")):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        p.add_argument("--repo", default=".")
        p.add_argument("--json", help="write the full report to this file")
        if name in ("scan", "hotspots"):
            p.add_argument("--top", type=int, default=20)
    pc = sub.add_parser("config", help="validate the gate configuration")
    pc.set_defaults(fn=cmd_config)
    pc.add_argument("--repo", default=".")
    pc.add_argument("--json", help=argparse.SUPPRESS)
    pc.add_argument("--check", action="store_true", help="validate .codehealth/config.toml")
    pc.add_argument("--config", help="validate this file instead of the working tree's config")
    sub.choices["scan"].add_argument("--ref", help="commit to scan (default: working tree)")
    sub.choices["diff"].add_argument("--base", default="HEAD",
                                     help="base ref; its merge-base with head is used (default HEAD)")
    sub.choices["diff"].add_argument("--head", help="head ref (default: working tree)")
    sub.choices["hotspots"].add_argument("--since", help="only count commits since this date")
    sub.choices["gate"].add_argument("--base", default="HEAD", help="base ref (merge-base is used)")
    sub.choices["gate"].add_argument("--ref-base", help="campaign base for reference and benchmark tiers "
                                     "(merge-base is used; default: same as --base)")
    sub.choices["gate"].add_argument("--mode", choices=("refactor", "optimize"), default="refactor",
                                     help="refactor: bitwise, no slowdown; optimize: declared tolerance, speedup")
    sub.choices["gate"].add_argument("--cadence", choices=("step", "campaign"), default="step")
    sub.choices["gate"].add_argument("--log", help="full log of tier commands")
    sub.choices["gate"].add_argument("--config", help="trial only: use this config instead of the base's")
    sub.choices["gate"].add_argument("--self-test", action="store_true",
                                     help="mutate one literal in a temporary HEAD worktree; every reference tier must detect it")
    sub.choices["gate"].add_argument("--mutate", help="with --self-test: mutate the literal at path:line")
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (gitio.GitError, ValueError, OSError, RuntimeError, KeyError) as exc:
        print(f"codehealth: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
