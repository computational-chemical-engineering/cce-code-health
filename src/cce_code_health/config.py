"""Per-repo configuration: .codehealth/config.toml.

Scope patterns use fnmatch semantics on repo-relative paths, where '*' also matches '/':
"src/*" is everything under src/. "/**/" also matches a single "/": "a/**/*.md" matches
"a/x.md". Only files with a supported extension are analysed. `match` is the one matcher used
for scope, frozen and forbid patterns.
"""
from __future__ import annotations

import itertools
import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from . import gitio, profiles

CONFIG_PATH = ".codehealth/config.toml"
SCHEMA = 1
EXTENSIONS = {".py": "python"}


def _variants(pattern: str) -> list[str]:
    parts = pattern.split("/**/")
    if len(parts) == 1:
        return [pattern]
    return ["".join(x + sep for x, sep in zip(parts, seps + ("",)))
            for seps in itertools.product(("/**/", "/"), repeat=len(parts) - 1)]


def match(path: str, pattern: str) -> bool:
    """fnmatch ('*' matches '/') where '/**/' may also be a single '/'."""
    return any(fnmatchcase(path, v) for v in _variants(pattern))


@dataclass
class Config:
    profile: profiles.Profile
    include: list = field(default_factory=lambda: ["*"])
    exclude: list = field(default_factory=list)
    source: str = "default"
    gate: dict = field(default_factory=dict)   # raw [gate] table, interpreted by gate.py
    architecture: dict = field(default_factory=dict)   # raw [architecture] table (architecture.py)

    def in_scope(self, path: str) -> bool:
        return (Path(path).suffix in EXTENSIONS
                and any(match(path, pat) for pat in self.include)
                and not any(match(path, pat) for pat in self.exclude))


def parse(text: str, source: str) -> Config:
    data = tomllib.loads(text)
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{source}: schema must be {SCHEMA}, got {data.get('schema')!r}")
    scope = data.get("scope", {})
    return Config(profile=profiles.get(data.get("profile", "python@0")),
                  include=list(scope.get("include", ["*"])),
                  exclude=list(scope.get("exclude", [])), source=source,
                  gate=dict(data.get("gate", {})),
                  architecture=dict(data.get("architecture", {})))


def load(repo: Path, ref: str | None) -> Config:
    texts = gitio.read_files(repo, ref, [CONFIG_PATH]) if (
        ref is not None or (repo / CONFIG_PATH).is_file()) else {}
    if CONFIG_PATH not in texts:
        return Config(profile=profiles.get("python@0"))
    return parse(texts[CONFIG_PATH], f"{ref or 'worktree'}:{CONFIG_PATH}")


TOP_KEYS = {"schema", "profile", "scope", "gate", "architecture"}
SCOPE_KEYS = {"include", "exclude"}
GATE_KEYS = {"frozen", "frozen_cmd", "forbid", "literals", "public_api", "keep_going", "prefix",
             "env", "tier"}
TIER_KEYS = {"name", "cadence", "kind", "cmd", "outputs", "tolerance", "timeout", "primary",
             "min_speedup", "max_slowdown", "repeat", "max_repeat", "modes", "behaviour", "coverage", "detect_exit"}
KINDS = ("command", "reference", "benchmark")
THREAD_PINS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def check(text: str) -> tuple[list[str], list[str]]:
    """Validate a config text: (info lines, problems)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [f"not valid TOML: {exc}"]
    info, bad = [], []
    if data.get("schema") != SCHEMA:
        bad.append(f"schema must be {SCHEMA}, got {data.get('schema')!r}")
    else:
        info.append(f"schema {SCHEMA}")
    try:
        info.append(f"profile {profiles.get(data.get('profile', 'python@0')).name}")
    except ValueError as exc:
        bad.append(str(exc))
    bad += [f"unknown top-level key {k!r}" for k in sorted(set(data) - TOP_KEYS)]
    scope, gate = data.get("scope", {}), data.get("gate", {})
    bad += [f"unknown key {k!r} in [scope]" for k in sorted(set(scope) - SCOPE_KEYS)]
    bad += [f"unknown key {k!r} in [gate]" for k in sorted(set(gate) - GATE_KEYS)]
    if "architecture" in data:
        from .architecture import check_config  # noqa: PLC0415 (architecture imports config)
        arch_bad = check_config(data["architecture"])
        bad += arch_bad
        if not arch_bad:
            arch = data["architecture"]
            info.append(f"architecture: {len(arch.get('layers', []))} layer rule(s), "
                        f"{len(arch.get('forbid', []))} forbid rule(s), cycle_depth {arch.get('cycle_depth', 2)}")
    tiers = gate.get("tier", [])
    names = [t.get("name") for t in tiers]
    bad += [f"duplicate tier name {n!r}" for n in sorted({n for n in names if names.count(n) > 1})]
    for i, t in enumerate(tiers):
        where = f"tier {t.get('name', f'#{i + 1}')!r}"
        if not t.get("name"):
            bad.append(f"{where}: name missing")
        bad += [f"{where}: unknown key {k!r}" for k in sorted(set(t) - TIER_KEYS)]
        kind = t.get("kind", "command")
        if kind not in KINDS:
            bad.append(f"{where}: kind must be one of {', '.join(KINDS)}, got {kind!r}")
        if not t.get("cmd"):
            bad.append(f"{where}: cmd missing")
        if kind == "reference":
            if not t.get("outputs"):
                bad.append(f"{where}: reference tier needs outputs")
            from .compare import TOLERANCE_KEYS  # noqa: PLC0415
            bad += [f"{where}: unknown tolerance key {k!r} (allowed: {', '.join(TOLERANCE_KEYS)})"
                    for k in sorted(set(t.get("tolerance", {})) - set(TOLERANCE_KEYS))]
        de = t.get("detect_exit")
        if de is not None and not (isinstance(de, list) and de and all(
                isinstance(x, int) and not isinstance(x, bool) and x != 0 for x in de)):
            bad.append(f"{where}: detect_exit must be a non-empty list of nonzero integers, got {de!r}")
        if t.get("cadence", "step") not in ("step", "campaign"):
            bad.append(f"{where}: cadence must be step or campaign, got {t['cadence']!r}")
        bad += [f"{where}: modes entry {m!r} must be refactor or optimize"
                for m in t.get("modes", []) if m not in ("refactor", "optimize")]
        info.append(f"tier {t.get('name')}: {kind}, cadence {t.get('cadence', 'step')}")
    benches = [t for t in tiers if t.get("kind") == "benchmark"]
    if benches:
        env = gate.get("env", {})
        miss = [k for k in THREAD_PINS if k not in env]
        if miss:
            bad.append("benchmark tiers need [gate.env] to set " + ", ".join(miss))
    primaries = [t for t in benches if t.get("primary")]
    if any("min_speedup" in t for t in tiers) and len(primaries) != 1:
        bad.append(f"min_speedup is used: exactly one benchmark tier needs primary = true, found {len(primaries)}")
    elif len(primaries) > 1:
        bad.append(f"exactly one primary benchmark tier allowed, found {len(primaries)}")
    return info, bad
