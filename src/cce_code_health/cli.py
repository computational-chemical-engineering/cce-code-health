"""codehealth command line. Exit codes: 0 pass, 1 regression, 2 tool error."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import tempfile

from . import __version__, architecture, config, coupling, gitio
from . import gate as gatemod
from .hook import cmd_hook
from .duplication import duplicated_lines
from .python_backend import analyze
from .report import file_report

EPS = 1e-9


def _load(a, repo: Path, ref: str | None) -> config.Config:
    """The repo's config at ref, or a trial config file (scan, hotspots) for a read-only look."""
    path = getattr(a, "trial_config", None)
    if path:
        return config.parse(Path(path).read_text(), f"trial:{path}")
    return config.load(repo, ref)


def analyze_ref(repo: Path, ref: str | None, cfg: config.Config,
                only: set[str] | None = None) -> dict[str, dict]:
    """Reports for in-scope files at ref (None = worktree). Duplication uses the whole scope
    of each language. The backend follows the file's profile (config.EXTENSIONS)."""
    files = [p for p in gitio.ls_files(repo, ref) if cfg.in_scope(p)]
    texts = gitio.read_files(repo, ref, files)
    dup: dict[str, int] = {}
    for p in [cfg.profile, *cfg.other_profiles.values()]:
        group = {k: v for k, v in texts.items() if cfg.profile_for(k) is p}
        if p.language == "cpp":
            from .cpp_backend import blank_comments  # noqa: PLC0415 (optional backend)
            group = {k: blank_comments(v) for k, v in group.items()}
        dup.update(duplicated_lines(group, p.dup_window))
    return {path: file_report(_analyze(path, texts[path], cfg.profile_for(path)), dup.get(path, 0),
                              cfg.profile_for(path))
            for path in files if path in texts and (only is None or path in only)}


def new_errors(rb: dict[str, dict], rh: dict[str, dict], changes) -> list[str]:
    """Head files whose analysis failed (parse, complexipy, lizard) while their base version's
    did not: such a file scores 0 debt, so the debt ratchet must not count it as clean."""
    return [f"{hp}: analysis error: {rh[hp]['error']}" for _, bp, hp in changes
            if hp in rh and rh[hp]["error"] and not (bp in rb and rb[bp]["error"])]


def _analyze(path: str, text: str, p):
    if p.language == "cpp":
        from .cpp_backend import analyze as analyze_cpp  # noqa: PLC0415 (needs lizard)
        return analyze_cpp(path, text)
    return analyze(path, text, p.varargs_params)


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
    langs = {"profiles": {k: p.name for k, p in cfg.other_profiles.items()}} if cfg.other_profiles else {}
    return {"tool": "cce-code-health", "version": __version__, "profile": cfg.profile.name, **langs,
            "config": cfg.source, "repo": repo.name, **extra}


def write_json(path: str | None, data: dict) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=1))


def cmd_scan(a) -> int:
    repo = Path(a.repo).resolve()
    ref = gitio.rev(repo, a.ref) if a.ref else None
    cfg = _load(a, repo, ref)
    reports = analyze_ref(repo, ref, cfg)
    t = totals(reports)
    write_json(a.json, {"meta": meta(repo, cfg, ref=ref or "worktree"), "totals": t, "files": reports})
    print(f"{'file':60} {'nloc':>6} {'debt':>8} {'health':>6}  file smells")
    for r in sorted(reports.values(), key=lambda r: -r["debt"])[:a.top]:
        print(f"{r['path'][-60:]:60} {r['nloc']:6} {r['debt']:8.1f} {r['health']:6.1f}  {','.join(r['file_smells'])}")
    print(f"SCAN {repo.name}@{(ref or 'worktree')[:10]} profile={cfg.label()}: "
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
    failures += new_errors(rb, rh, ch)
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
                bm = cfg.profile_for(path).brain_metric
                failures.append(f"{path}:{f['line']} {f['name']}: new brain function "
                                f"({bm} {f[bm]}, nloc {f['nloc']})")
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
    arch = architecture.compare_refs(repo, base, head, cfg)   # None without [architecture]
    if arch:
        failures += arch[0]
        rows += [f"  {g}" for g in arch[1]]
        rows += [f"  WARNING {w}" for w in arch[2]]
    from . import docs as docsmod, docstrings  # noqa: PLC0415
    doc_res = docsmod.compare(repo, base, head, cfg)          # None without [docs]
    if doc_res:
        failures += doc_res.failures
        rows += [f"  WARNING {w}" for w in doc_res.warnings]
        rows += [f"  review {d} (references {t}, changed here)" for d, t in sorted(set(doc_res.review))[:20]]
    ds = docstrings.compare_refs(repo, base, head, cfg, gitio.changes(repo, base, head))
    failures += ds or []
    write_json(a.json, {"meta": meta(repo, cfg, base=base, head=head or "worktree"),
                        "debt_base": round(db, 3), "debt_head": round(dh, 3),
                        "failures": failures, "base": rb, "head": rh,
                        **({"architecture_improvements": arch[1]} if arch else {}),
                        **({"docs": {"warnings": doc_res.warnings, "notes": doc_res.notes,
                                     "review": sorted(set(doc_res.review))}} if doc_res else {})})
    print("\n".join(rows))
    for f in failures:
        print(f"  FAIL {f}")
    verdict = "FAIL" if failures else "PASS"
    print(f"DIFF {verdict} {repo.name} base={base[:10]} head={(head or 'worktree')[:10]} "
          f"profile={cfg.label()}: {len(ch)} touched files, debt {db:.2f} -> {dh:.2f}, "
          f"{len(failures)} failure(s)")
    return 1 if failures else 0


def cmd_arch(a) -> int:
    repo = Path(a.repo).resolve()
    ref = gitio.rev(repo, a.ref) if a.ref else None
    cfg = config.load(repo, ref)
    res = architecture.analyse(repo, ref, cfg)
    if res is None:
        raise ValueError(f"{cfg.source}: no [architecture] table")
    write_json(a.json, {"meta": meta(repo, cfg, ref=ref or "worktree"), **architecture.to_json(res)})
    print("\n".join(architecture.report_lines(res)))
    for w in res.warnings:
        print(f"  WARNING {w}")
    print(f"ARCH {repo.name}@{(ref or 'worktree')[:10]}: {res.files} files, {len(res.imports)} imports, "
          f"{len(res.violations)} violation(s), {len(res.cycles)} cycle(s)")
    return 0


def cmd_docs(a) -> int:
    from . import docs as docsmod  # noqa: PLC0415
    repo = Path(a.repo).resolve()
    ref = gitio.rev(repo, a.ref) if a.ref else None
    cfg = _load(a, repo, ref)
    res = docsmod.analyse(repo, ref, cfg)
    if res is None:
        raise ValueError(f"{cfg.source}: no [docs] table")
    d = cfg.docs
    st = docsmod.stats(res, d)
    res.tree.close()
    broken = [(doc, o) for doc, occs in res.docs.items() if not docsmod.is_record(doc, d)
              for o in occs if o.status == "broken"]
    if a.stats:
        for (rec, kind, status, why), n in sorted(st.items()):
            print(f"  {rec:6} {kind:6} {status:6} {why:24} {n}")
    for doc, o in broken[:a.top]:
        print(f"  {doc}:{o.line}{f' ({o.where})' if o.where else ''} {o.ref.kind} `{o.ref.text}`: {o.reason}")
    write_json(a.json, {"meta": meta(repo, cfg, ref=ref or "worktree"),
                        "stats": [{"docs": k[0], "kind": k[1], "status": k[2], "reason": k[3], "n": n}
                                  for k, n in sorted(st.items())],
                        "broken": [{"doc": doc, "line": o.line, "where": o.where, "kind": o.ref.kind,
                                    "text": o.ref.text, "reason": o.reason} for doc, o in broken]})
    refs = sum(st.values())
    print(f"DOCS {repo.name}@{(ref or 'worktree')[:10]}: {len(res.docs)} docs, {refs} references, "
          f"{sum(n for k, n in st.items() if k[2] == 'ok')} ok, {len(broken)} broken in living docs, "
          f"{sum(n for k, n in st.items() if k[2] == 'broken' and k[0] == 'record')} in records")
    return 0


def cmd_hotspots(a) -> int:
    repo = Path(a.repo).resolve()
    cfg = _load(a, repo, None)
    reports = analyze_ref(repo, None, cfg)
    counts = gitio.commit_counts(repo, a.since)
    rows = sorted(({"path": p, "commits": counts.get(p, 0), "debt": r["debt"],
                    "health": r["health"], "nloc": r["nloc"],
                    "rank": counts.get(p, 0) * r["debt"],
                    "max_lcom4": r["max_lcom4"]} for p, r in reports.items()),
                  key=lambda x: -x["rank"])
    write_json(a.json, {"meta": meta(repo, cfg, since=a.since), "hotspots": rows})
    print(f"{'file':60} {'commits':>7} {'debt':>8} {'health':>6} {'rank':>9} {'lcom4':>5}")
    for x in rows[:a.top]:
        lc = "-" if x["max_lcom4"] is None else x["max_lcom4"]
        print(f"{x['path'][-60:]:60} {x['commits']:7} {x['debt']:8.1f} {x['health']:6.1f} {x['rank']:9.0f} {lc:>5}")
    print(f"HOTSPOTS {repo.name}: {len(rows)} files ranked by commits x debt"
          + (f" since {a.since}" if a.since else ""))
    return 0


def cmd_coupling(a) -> int:
    repo = Path(a.repo).resolve()
    cfg = _load(a, repo, None)
    keep = {p for p in gitio.ls_files(repo, None) if cfg.in_scope(p)}
    commits, skipped = coupling.commit_file_sets(repo, a.since, a.max_files_per_commit, keep.__contains__)
    res = coupling.compute(commits, a.depth, a.min_shared, a.min_degree, a.include_test_pairs)
    write_json(a.json, {"meta": meta(repo, cfg, since=a.since, max_files_per_commit=a.max_files_per_commit,
                                     min_shared=a.min_shared, min_degree=a.min_degree, depth=a.depth,
                                     include_test_pairs=a.include_test_pairs,
                                     commits=len(commits), skipped_commits=skipped), **res})
    print(f"{'file a':45} {'file b':45} {'shared':>6} {'degree':>6}")
    for x in res["pairs"][:a.top]:
        print(f"{x['a'][-45:]:45} {x['b'][-45:]:45} {x['shared']:6} {x['degree']:6.2f}"
              + ("  cross-module" if x["cross_module"] else ""))
    if res["module_pairs"]:
        print(f"\n{'module a':45} {'module b':45} {'shared':>6}")
        for x in res["module_pairs"][:a.top]:
            print(f"{x['a'][-45:]:45} {x['b'][-45:]:45} {x['shared']:6}")
    print(f"COUPLING {repo.name}: {len(res['pairs'])} file pairs over thresholds, "
          f"{len(res['module_pairs'])} cross-module pairs, {len(commits)} commits used, {skipped} skipped (large)")
    return 0


def cmd_self_test(a, repo: Path, log: Path) -> int:
    base = gitio.rev(repo, "HEAD")
    cfg = (config.parse(Path(a.config).read_text(), f"override:{a.config}") if a.config
           else config.load(repo, base))
    ref_base = gitio.merge_base(repo, a.ref_base, "HEAD") if a.ref_base else None
    ok, checks, what = gatemod.self_test(repo, cfg, log, a.mutate, ref_base)
    write_json(a.json, {"meta": {"tool": "cce-code-health", "version": __version__, "self_test": True,
                                 "mutation": what, "ref_base": ref_base, "log": str(log)},
                        "checks": [c.__dict__ for c in checks]})
    print(f"  mutation: {what}")
    for c in checks:
        label = ("detected" if c.outcome == "detected" else
                 "BASELINE" if "without mutation" in c.detail else
                 "ERROR   " if c.outcome == "error" else "MISSED  ")
        print(f"  {label} {c.name:12} {c.seconds:7.1f} s  {c.detail}")
    kind = gatemod.self_test_kind(cfg)
    print(f"SELF-TEST {'PASS' if ok else 'FAIL'} {repo.name}: "
          f"{sum(c.ok for c in checks)}/{len(checks)} {kind} tiers detected the mutation (log {log})"
          if what != "none (baseline failed)" else
          f"SELF-TEST FAIL {repo.name}: {len(checks)} tier(s) fail without mutation (log {log})")
    return 0 if ok else 1


def _resolves(repo: Path, ref: str) -> bool:
    try:
        gitio.rev(repo, ref)
        return True
    except gitio.GitError:
        return False


def cmd_gate(a) -> int:
    repo = Path(a.repo).resolve()
    log = Path(a.log) if a.log else Path(tempfile.gettempdir()) / f"codehealth-gate-{repo.name}.log"
    if a.self_test:
        return cmd_self_test(a, repo, log)
    revert = a.revert or []
    if revert and a.require_gain:
        raise ValueError("--revert and --require-gain are incompatible: a revert raises debt")
    head_tree = gitio.worktree_tree(repo)
    head = (gitio.rev(repo, "HEAD")
            if head_tree == gitio.git(repo, "rev-parse", "HEAD^{tree}").decode().strip() else "worktree")
    info: dict = {}
    checks, base, ref_base = gatemod.gate(repo, a.base, a.cadence, log,
                                          Path(a.config).resolve() if a.config else None, a.mode,
                                          a.ref_base, require_gain=a.require_gain, info=info,
                                          revert=revert, head_tree=head_tree, plan=a.plan)
    if revert:
        revert = [gitio.rev(repo, s) for s in revert if _resolves(repo, s)]
    write_json(a.json, {"meta": {"tool": "cce-code-health", "version": __version__, "base": base,
                                 "ref_base": ref_base, "head": head, "revert": revert, "cadence": a.cadence, "mode": a.mode,
                                 "log": str(log), **info, "require_gain": a.require_gain,
                                 "trial": bool(a.config), "head_tree": head_tree},
                        "checks": [c.__dict__ for c in checks]})
    for c in checks:
        print(f"  {'ok  ' if c.ok else 'FAIL'} {c.name:12} {c.seconds:7.1f} s  {c.detail}")
    failed = [c.name for c in checks if not c.ok]
    verdict = "FAIL" if failed else "TRIAL" if a.config else "PASS"
    print(f"GATE {verdict} {repo.name} base={base[:10]} ref-base={ref_base[:10]} "
          f"head={head[:10]} mode={a.mode} cadence={a.cadence} "
          f"debt={info['debt_before']}->{info['debt_after']}"
          + (f" revert={len(revert)}" if revert else "")
          + f": {len(checks) - len(failed)}/{len(checks)} checks passed"
          + (f", failed: {', '.join(failed)}" if failed else "")
          + (" [TRIAL: config override, not valid as CI evidence]" if a.config else "") + f" (log {log})")
    return 1 if failed else 3 if a.config else 0


RESULTS = ("accepted", "accepted-neutral", "gate-fail", "no-gain", "review-reject")
KEPT = ("accepted", "accepted-neutral")   # results whose commit stays: must be committed


def _infer_result(checks: list, mode: str, before: float, after: float) -> str:
    if any(not c["ok"] for c in checks):
        return "gate-fail"
    return "accepted" if mode == "optimize" or after < before - EPS else "no-gain"


def _check_log_args(a, data: dict) -> tuple[list, dict, str]:
    """Validate the gate JSON and the arguments; returns (checks, meta, result)."""
    meta_ = data.get("meta", {})
    if meta_.get("self_test") or "checks" not in data:
        raise ValueError("--gate-json is a self-test or not a gate JSON; log needs `codehealth gate --json`")
    if meta_.get("trial"):
        raise ValueError("--gate-json is a trial gate (--config): not valid evidence, refusing")
    for k in ("base", "ref_base", "mode", "head_tree", "debt_before", "debt_after"):
        if k not in meta_:
            raise ValueError(f"--gate-json meta lacks {k!r} (written by an older codehealth?)")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.campaign):
        raise ValueError(f"--campaign must match [A-Za-z0-9._-]+, got {a.campaign!r}")
    reverting = bool(meta_.get("revert"))
    if reverting:
        if a.result not in (None, "gate-fail", "review-reject"):
            raise ValueError(f"--result {a.result} is not allowed for a revert gate JSON "
                             "(gate-fail or review-reject)")
        if not a.reason:
            raise ValueError("a revert gate JSON needs --reason")
    else:
        if a.result in ("review-reject", "accepted-neutral") and not a.reason:
            raise ValueError(f"--result {a.result} needs --reason")
        if a.reason and a.result not in ("review-reject", "accepted-neutral"):
            raise ValueError("--reason is only for --result review-reject or accepted-neutral")
    checks = data["checks"]
    failed = [c for c in checks if not c["ok"]]
    gained = meta_["mode"] == "optimize" or meta_["debt_after"] < meta_["debt_before"] - EPS
    if reverting:
        result = a.result or ("gate-fail" if failed else "review-reject")
    else:
        result = a.result or _infer_result(checks, meta_["mode"], meta_["debt_before"], meta_["debt_after"])
    if result == "gate-fail" and not failed:
        raise ValueError("--result gate-fail but every check in the gate JSON passed")
    if result != "gate-fail" and failed:
        raise ValueError(f"--result {result} but check {failed[0]['name']} failed in the gate JSON")
    if result == "accepted" and not gained:
        raise ValueError(f"--result accepted but debt did not drop ({meta_['debt_before']} -> {meta_['debt_after']}); "
                         "a deliberate step without gain (gated without --require-gain) is accepted-neutral")
    if result == "accepted-neutral":
        if meta_["mode"] == "optimize":
            raise ValueError("--result accepted-neutral is for refactor and restructure; optimize uses accepted")
        if meta_.get("require_gain") or any(c["name"] == "gain" for c in checks):
            raise ValueError("--result accepted-neutral needs a gate run without --require-gain")
        if meta_["debt_after"] > meta_["debt_before"] + EPS:
            raise ValueError(f"--result accepted-neutral but debt rose ({meta_['debt_before']} -> {meta_['debt_after']})")
    return checks, meta_, result


def _logged_commit(repo: Path, head: str | None, m: dict) -> str:
    """The commit that carries the judged tree: HEAD, or --head SHA (an ancestor of HEAD, for late logging)."""
    name = head or "HEAD"
    sha = gitio.rev(repo, name)
    tree = gitio.git(repo, "rev-parse", f"{sha}^{{tree}}").decode().strip()
    if tree != m["head_tree"]:
        hint = "" if head else "; commit the gated change first, or name the step commit with --head SHA"
        raise ValueError(f"{name}'s tree differs from the tree the gate judged "
                         f"({name} {tree[:10]}, gate {m['head_tree'][:10]}){hint}")
    if head:
        try:
            gitio.git(repo, "merge-base", "--is-ancestor", sha, "HEAD")
        except gitio.GitError:
            raise ValueError(f"--head {head} is not an ancestor of HEAD: the step is not on this branch") from None
    return sha


def cmd_log(a) -> int:
    repo = Path(a.repo).resolve()
    gj = Path(a.gate_json).resolve()
    data = json.loads(gj.read_text())
    checks, m, result = _check_log_args(a, data)
    reverts = list(m.get("revert") or [])
    head_sha = m.get("head") if re.fullmatch(r"[0-9a-f]{40}", str(m.get("head"))) else None
    if result in KEPT or (reverts and result == "review-reject"):
        head_sha = _logged_commit(repo, a.head, m)
        tree = gitio.git(repo, "rev-parse", f"{head_sha}^{{tree}}").decode().strip()
        if result in KEPT and tree == gitio.git(repo, "rev-parse", f"{m['base']}^{{tree}}").decode().strip():
            raise ValueError(f"{a.head or 'HEAD'}'s tree equals the base's tree: nothing was committed")
    elif a.head:
        raise ValueError(f"--head is only for kept results ({', '.join(KEPT)}) and committed reverts")
    out = (Path(a.out) if a.out else gatemod._common_dir(repo) / "runs" / f"{a.campaign}.jsonl").resolve()
    lines = [ln for ln in out.read_text().splitlines() if ln.strip()] if out.is_file() else []
    for ln in lines:
        old = json.loads(ln)
        if (old.get("campaign"), old.get("head_tree"), old.get("base_sha")) == (a.campaign, m["head_tree"], m["base"]):
            raise ValueError(f"duplicate: campaign {a.campaign} already has step {old.get('step')} "
                             f"for this tree and base")
    failed = next((c["name"] for c in checks if not c["ok"]), None)
    rec = {"campaign": a.campaign, "step": len(lines) + 1, "class": "revert" if reverts else m["mode"], "target": a.target,
           "recipe": a.recipe, "result": result, "failed_tier": failed, "reason": a.reason,
           "debt_before": m["debt_before"], "debt_after": m["debt_after"], "base_sha": m["base"],
           "ref_base_sha": m["ref_base"], "head_sha": head_sha, "head_tree": m["head_tree"],
           "tool_version": m.get("version"), "logged_with": __version__, "model": a.model,
           "hypothesis": a.hypothesis, "reverts": reverts, "checks": {c["name"]: c["ok"] for c in checks},
           "details": {c["name"]: c.get("detail", "") for c in checks},
           "gate_json": str(gj),
           "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"LOG {a.campaign} step {rec['step']} {result} {a.target} "
          f"debt {m['debt_before']} -> {m['debt_after']} ({out})")
    return 0


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
                            ("arch", cmd_arch, "architecture rules: layers, forbidden imports, cycles"),
                            ("gate", cmd_gate, "behaviour gate: static rules and tiers, base vs worktree")):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        p.add_argument("--repo", default=".")
        p.add_argument("--json", help="write the full report to this file")
        if name in ("scan", "hotspots"):
            p.add_argument("--top", type=int, default=20)
            p.add_argument("--config", dest="trial_config",
                           help="read-only trial: use this config file instead of the repo's")
    pk = sub.add_parser("coupling", help="change coupling: files that change together in history")
    pk.set_defaults(fn=cmd_coupling)
    pk.add_argument("--repo", default=".")
    pk.add_argument("--json", help="write the full report to this file")
    pk.add_argument("--since", help="only use commits since this date")
    pk.add_argument("--max-files-per-commit", type=int, default=30, help="skip larger commits (default 30)")
    pk.add_argument("--min-shared", type=int, default=5, help="min shared commits (default 5)")
    pk.add_argument("--min-degree", type=float, default=0.5, help="min coupling degree (default 0.5)")
    pk.add_argument("--config", dest="trial_config",
                    help="read-only trial: use this config file instead of the repo's")
    pk.add_argument("--include-test-pairs", action="store_true",
                    help="keep pairs of a test file with a non-test file (dropped by default)")
    pk.add_argument("--depth", type=int, help="module = first N directory components (default: whole directory)")
    pk.add_argument("--top", type=int, default=30)
    pd = sub.add_parser("docs", help="documentation references: broken paths, anchors, test ids, Python names")
    pd.set_defaults(fn=cmd_docs)
    pd.add_argument("--repo", default=".")
    pd.add_argument("--json", help="write the full report to this file")
    pd.add_argument("--ref", help="commit to analyse (default: working tree)")
    pd.add_argument("--stats", action="store_true", help="counts per docs class, kind, status and reason")
    pd.add_argument("--top", type=int, default=30, help="broken references to list (living docs)")
    pd.add_argument("--config", dest="trial_config",
                    help="read-only trial: use this config file instead of the repo's")
    pc = sub.add_parser("config", help="validate the gate configuration")
    pc.set_defaults(fn=cmd_config)
    pc.add_argument("--repo", default=".")
    pc.add_argument("--json", help=argparse.SUPPRESS)
    pc.add_argument("--check", action="store_true", help="validate .codehealth/config.toml")
    pc.add_argument("--config", help="validate this file instead of the working tree's config")
    ph = sub.add_parser("hook", help="Claude Code PostToolUse hook: per-file debt feedback from stdin JSON")
    ph.set_defaults(fn=cmd_hook)
    ph.add_argument("--base", default="HEAD", help="ref to compare the edited file against (default HEAD)")
    pl = sub.add_parser("log", help="append one validated line to a campaign run log")
    pl.set_defaults(fn=cmd_log)
    pl.add_argument("--repo", default=".")
    pl.add_argument("--gate-json", required=True, help="output of `codehealth gate --json`")
    pl.add_argument("--campaign", required=True, help="campaign id [A-Za-z0-9._-]+")
    pl.add_argument("--target", required=True, help="path::qualname")
    pl.add_argument("--recipe", required=True)
    pl.add_argument("--result", choices=RESULTS, help="default: inferred from the gate JSON")
    pl.add_argument("--reason", help="required for --result review-reject, accepted-neutral and revert gate JSONs")
    pl.add_argument("--head", help="the commit that carries the judged tree (default HEAD); must be an "
                    "ancestor of HEAD, for logging after later commits")
    pl.add_argument("--model")
    pl.add_argument("--hypothesis")
    pl.add_argument("--out", help="default: <git-common-dir>/codehealth/runs/<campaign>.jsonl")
    sub.choices["scan"].add_argument("--ref", help="commit to scan (default: working tree)")
    sub.choices["arch"].add_argument("--ref", help="commit to analyse (default: working tree)")
    sub.choices["diff"].add_argument("--base", default="HEAD",
                                     help="base ref; its merge-base with head is used (default HEAD)")
    sub.choices["diff"].add_argument("--head", help="head ref (default: working tree)")
    sub.choices["hotspots"].add_argument("--since", help="only count commits since this date")
    sub.choices["gate"].add_argument("--base", default="HEAD", help="base ref (merge-base is used; default HEAD). Ignored with "
                                     "--self-test, which mutates HEAD")
    sub.choices["gate"].add_argument("--ref-base", help="campaign base for reference and benchmark tiers "
                                     "(merge-base is used; default: same as --base); also exposed "
                                     "to tier commands as {ref_base}")
    sub.choices["gate"].add_argument("--mode", choices=("refactor", "optimize", "restructure"), default="refactor",
                                     help="refactor: bitwise, no slowdown; optimize: declared tolerance, speedup; "
                                     "restructure: refactor plus new modules under --plan")
    sub.choices["gate"].add_argument("--plan", help="restructure mode: approved plan under "
                                     ".codehealth/restructure/, read from the base commit")
    sub.choices["gate"].add_argument("--cadence", choices=("step", "campaign"), default="step")
    sub.choices["gate"].add_argument("--log", help="full log of tier commands")
    sub.choices["gate"].add_argument("--config", help="trial only: use this config instead of the base's")
    sub.choices["gate"].add_argument("--self-test", action="store_true",
                                     help="mutate one literal in a temporary HEAD worktree; every reference tier must detect it")
    sub.choices["gate"].add_argument("--mutate", help="with --self-test: mutate the literal at path:line "
                                     "or path:line:N (N-th numeric literal on the line, 1-based)")
    sub.choices["gate"].add_argument("--revert", action="append", metavar="SHA",
                                     help="gate a commit that exactly undoes this earlier accepted step "
                                     "(repeatable); debt may rise; incompatible with --require-gain")
    sub.choices["gate"].add_argument("--require-gain", action="store_true",
                                     help="add a static check `gain`: debt over touched files must drop")
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (gitio.GitError, ValueError, OSError, RuntimeError, KeyError) as exc:
        print(f"codehealth: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
