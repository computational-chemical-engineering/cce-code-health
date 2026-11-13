"""Edit-time feedback: `codehealth hook`, a Claude Code PostToolUse hook.

Per-file only (no duplication, no git log, no repo-wide scan). Silent (exit 0) unless the edit
raised the file's debt, added a function with debt, a brain function, a file smell or an invalid
suppression; then the report goes to stderr with exit code 2. Any failure inside is swallowed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import config, gitio
from .profiles import METRICS
from .python_backend import analyze
from .report import file_report

EPS = 1e-9
MAX_FUNCTIONS = 12
ADVICE = ("Simplify before moving on (rules: .codehealth/playbook.md, \"Rules for new code\"). "
          "If the rise is deliberate, say why in your reply.")


def _repo_root(path: Path) -> Path | None:
    try:
        out = gitio.git(path.parent, "rev-parse", "--show-toplevel").decode().strip()
    except (gitio.GitError, OSError):
        return None
    return Path(out).resolve() if out else None


def _report(rel: str, text: str, cfg: config.Config) -> dict | None:
    p = cfg.profile
    r = file_report(analyze(rel, text, p.varargs_params), 0, p)
    return None if r["error"] else r


def _invalid(r: dict | None) -> list:
    return [] if r is None else sorted(
        (tuple(s["smells"]), s["reason"]) for s in r["suppressions"] if not s["valid"])


def _fn_line(f: dict, cfg: config.Config) -> str:
    t = cfg.profile.thresholds
    parts = [f"{m} {f[m]} > {t[m]}" for m in METRICS if m in f["debt_parts"]]
    if f["brain"]:
        parts.append("brain function")
    qual = f["qualname"].split("::", 1)[1]
    return f"  {qual} (line {f['line']}): {', '.join(parts) or 'debt rose'}"


def check_edit(rel: str, old_text: str, new_text: str, cfg: config.Config) -> str | None:
    """The report text, or None when the edit is clean."""
    new = _report(rel, new_text, cfg)
    if new is None:
        return None
    old = _report(rel, old_text, cfg) if old_text else None
    old_fns = {f["qualname"]: f for f in old["functions"]} if old else {}
    old_brains = {q for q, f in old_fns.items() if f["brain"]}
    bad = []
    for f in new["functions"]:
        was = old_fns.get(f["qualname"])
        rose = f["debt"] > (was["debt"] if was else 0.0) + EPS
        if rose or (f["brain"] and f["qualname"] not in old_brains):
            bad.append(f)
    d0 = old["debt"] if old else 0.0
    smells = sorted(set(new["file_smells"]) - set(old["file_smells"] if old else []))
    inv = len(_invalid(new)) > len(_invalid(old))
    if not (new["debt"] > d0 + EPS or bad or smells or inv):
        return None
    lines = [f"codehealth: {rel}: debt {d0:.1f} -> {new['debt']:.1f}"]
    lines += [_fn_line(f, cfg) for f in bad[:MAX_FUNCTIONS]]
    if len(bad) > MAX_FUNCTIONS:
        lines.append(f"  ... and {len(bad) - MAX_FUNCTIONS} more")
    if smells:
        lines.append(f"  new file smell: {', '.join(smells)}")
    if inv:
        lines.append("  new invalid suppression (needs known smell and '-- reason')")
    return "\n".join(lines + [ADVICE])


def _message(stdin_text: str, base: str) -> str | None:
    data = json.loads(stdin_text)
    fp = (data.get("tool_input") or {}).get("file_path")
    if not isinstance(fp, str) or not fp.endswith(".py"):
        return None
    path = Path(fp)
    if not path.is_absolute():
        path = Path(data.get("cwd") or ".") / path
    path = path.resolve()
    root = _repo_root(path)
    if root is None or not (root / config.CONFIG_PATH).is_file() or not path.is_file():
        return None
    rel = path.relative_to(root).as_posix()
    cfg = config.load(root, None)
    if cfg.hook.get("enabled", True) is False or not cfg.in_scope(rel):
        return None
    try:
        old = gitio.read_files(root, base, [rel]).get(rel, "")
    except gitio.GitError:
        old = ""
    return check_edit(rel, old, path.read_text(encoding="utf-8", errors="replace"), cfg)


def run(stdin_text: str, base: str) -> int:
    if os.environ.get("CODEHEALTH_HOOK") == "0":
        return 0
    try:
        msg = _message(stdin_text, base)
    except Exception:  # noqa: BLE001 - the hook must never block the agent
        return 0
    if msg is None:
        return 0
    print(msg, file=sys.stderr)
    return 2


def cmd_hook(a) -> int:
    try:
        text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return 0
    return run(text, a.base)
