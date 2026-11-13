"""Per-repo configuration: .codehealth/config.toml.

Scope patterns use fnmatch semantics on repo-relative paths, where '*' also matches '/':
"src/*" is everything under src/. "/**/" also matches a single "/": "a/**/*.md" matches
"a/x.md". Only files with a supported extension are analysed. `match` is the one matcher used
for scope, frozen and forbid patterns.

Profiles: `profile` (default python@0) measures Python. Other languages are measured only
when a [profiles] table names a profile for them, e.g. `[profiles] cpp = "cpp@0"`; their
files are otherwise out of scope. [profiles] may also name the Python profile.
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
EXTENSIONS = {".py": "python",
              **dict.fromkeys((".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h", ".ipp",
                               ".tpp", ".cu", ".cuh"), "cpp")}


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
    hook: dict = field(default_factory=dict)   # raw [hook] table, interpreted by hook.py
    architecture: dict = field(default_factory=dict)   # raw [architecture] table (architecture.py)
    docs: dict = field(default_factory=dict)           # raw [docs] table (docs.py, docstrings.py)
    other_profiles: dict = field(default_factory=dict)   # language -> Profile, not Python

    def profile_for(self, path: str) -> profiles.Profile | None:
        lang = EXTENSIONS.get(Path(path).suffix)
        return self.profile if lang == "python" else self.other_profiles.get(lang)

    def label(self) -> str:
        """Profile names for reports: "python@0", or "python@0+cpp@0" with other languages."""
        return "+".join([self.profile.name] + [p.name for p in self.other_profiles.values()])

    def in_scope(self, path: str) -> bool:
        return (self.profile_for(path) is not None
                and any(match(path, pat) for pat in self.include)
                and not any(match(path, pat) for pat in self.exclude))


def parse(text: str, source: str) -> Config:
    data = tomllib.loads(text)
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{source}: schema must be {SCHEMA}, got {data.get('schema')!r}")
    scope = data.get("scope", {})
    python, other = _profiles(data)
    return Config(profile=python, include=list(scope.get("include", ["*"])),
                  exclude=list(scope.get("exclude", [])), source=source,
                  gate=dict(data.get("gate", {})), hook=dict(data.get("hook", {})),
                  architecture=dict(data.get("architecture", {})), other_profiles=other,
                  docs=dict(data.get("docs", {})))


def _profiles(data: dict) -> tuple[profiles.Profile, dict]:
    """The Python profile and {language: profile} for the others; ValueError when invalid."""
    table = dict(data.get("profiles", {}))
    if "profile" in data and "python" in table and table["python"] != data["profile"]:
        raise ValueError(f"profile {data['profile']!r} and [profiles] python = "
                         f"{table['python']!r} disagree")
    table["python"] = table.get("python", data.get("profile", "python@0"))
    langs = sorted(set(EXTENSIONS.values()))
    out = {}
    for lang, name in table.items():
        if lang not in langs:
            raise ValueError(f"[profiles]: unknown language {lang!r}; known: {langs}")
        p = profiles.get(name)
        if p.language != lang:
            raise ValueError(f"[profiles] {lang} = {name!r} is a {p.language} profile")
        out[lang] = p
    python = out.pop("python")
    return python, out


def load(repo: Path, ref: str | None) -> Config:
    texts = gitio.read_files(repo, ref, [CONFIG_PATH]) if (
        ref is not None or (repo / CONFIG_PATH).is_file()) else {}
    if CONFIG_PATH not in texts:
        return Config(profile=profiles.get("python@0"))
    return parse(texts[CONFIG_PATH], f"{ref or 'worktree'}:{CONFIG_PATH}")


TOP_KEYS = {"schema", "profile", "profiles", "scope", "gate", "hook", "architecture", "docs"}
HOOK_KEYS = {"enabled"}
SCOPE_KEYS = {"include", "exclude"}
GATE_KEYS = {"frozen", "frozen_cmd", "forbid", "literals", "public_api", "keep_going", "prefix",
             "env", "tier", "flags", "flag_patterns"}
TIER_KEYS = {"name", "cadence", "kind", "cmd", "outputs", "tolerance", "timeout", "primary",
             "min_speedup", "max_slowdown", "repeat", "max_repeat", "modes", "behaviour", "coverage", "detect_exit",
             "skip_exit", "fail_regex", "format", "ignore_exit", "order", "band", "last"}
KINDS = ("command", "reference", "benchmark", "findings", "convergence")
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
        python, other = _profiles(data)
        info.append(f"profile {python.name}")
        info += [f"profile {p.name} ({lang})" for lang, p in other.items()]
    except ValueError as exc:
        bad.append(str(exc))
    bad += [f"unknown top-level key {k!r}" for k in sorted(set(data) - TOP_KEYS)]
    scope, gate = data.get("scope", {}), data.get("gate", {})
    bad += [f"unknown key {k!r} in [scope]" for k in sorted(set(scope) - SCOPE_KEYS)]
    bad += [f"unknown key {k!r} in [gate]" for k in sorted(set(gate) - GATE_KEYS)]
    hook = data.get("hook", {})
    bad += [f"unknown key {k!r} in [hook]" for k in sorted(set(hook) - HOOK_KEYS)]
    if "enabled" in hook and not isinstance(hook["enabled"], bool):
        bad.append(f"[hook] enabled must be a bool, got {hook['enabled']!r}")
    if "architecture" in data:
        from .architecture import check_config  # noqa: PLC0415 (architecture imports config)
        arch_bad = check_config(data["architecture"])
        bad += arch_bad
        if not arch_bad:
            arch = data["architecture"]
            info.append(f"architecture: {len(arch.get('layers', []))} layer rule(s), "
                        f"{len(arch.get('forbid', []))} forbid rule(s), cycle_depth {arch.get('cycle_depth', 2)}")
    if "docs" in data:
        from .docs import check_config as docs_check  # noqa: PLC0415 (docs imports config)
        docs_bad = docs_check(data["docs"]) if isinstance(data["docs"], dict) else ["[docs] must be a table"]
        bad += docs_bad
        if not docs_bad:
            dd = data["docs"]
            info.append(f"docs: {len(dd['include'])} include glob(s), {len(dd.get('records', []))} record "
                        f"glob(s), anchors {dd.get('anchors', 'warn')}, docstrings {dd.get('docstrings', False)}")
    if isinstance(data.get("profiles"), dict) and "cpp" in data["profiles"]:
        info += [f"WARNING [gate] {k} = true is not checked for C++ files (Python only; the check "
                 f"passes and says 'not checked')" for k in ("public_api", "literals") if gate.get(k)]
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
        bad += _build_tier_problems(t, kind, where)
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
    bad += _flags_problems(gate)
    primaries = [t for t in benches if t.get("primary")]
    if any("min_speedup" in t for t in tiers) and len(primaries) != 1:
        bad.append(f"min_speedup is used: exactly one benchmark tier needs primary = true, found {len(primaries)}")
    elif len(primaries) > 1:
        bad.append(f"exactly one primary benchmark tier allowed, found {len(primaries)}")
    return info, bad


def _regex_problem(rx, what: str) -> list[str]:
    if not isinstance(rx, str):
        return [f"{what} must be a string, got {rx!r}"]
    try:
        re.compile(rx)
    except re.error as exc:
        return [f"{what} is not a valid regex: {exc}"]
    return []


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _build_tier_problems(t: dict, kind: str, where: str) -> list[str]:
    """skip_exit and fail_regex (any kind), findings and convergence keys."""
    bad = []
    se = t.get("skip_exit")
    ints = se if isinstance(se, list) else [se]
    if se is not None and not (ints and all(isinstance(x, int) and not isinstance(x, bool) for x in ints)):
        bad.append(f"{where}: skip_exit must be an integer or a non-empty list of integers, got {se!r}")
    if "fail_regex" in t:
        bad += _regex_problem(t["fail_regex"], f"{where}: fail_regex")
    if kind == "findings":
        if t.get("format", "gcc") != "gcc":
            bad.append(f"{where}: format must be gcc, got {t['format']!r}")
        if not isinstance(t.get("ignore_exit", True), bool):
            bad.append(f"{where}: ignore_exit must be a bool, got {t['ignore_exit']!r}")
    if kind == "convergence":
        if not (isinstance(t.get("outputs"), list) and len(t["outputs"]) == 1):
            bad.append(f"{where}: convergence tier needs exactly one output")
        band = t.get("band")
        if not (isinstance(band, list) and len(band) == 2 and all(_number(x) for x in band)
                and band[0] < band[1]):
            bad.append(f"{where}: band must be [lo, hi] with lo < hi, got {band!r}")
        elif not _number(t.get("order")) or not band[0] <= t["order"] <= band[1]:
            bad.append(f"{where}: order must be a number within band, got {t.get('order')!r}")
        last = t.get("last")
        if last is not None and not (isinstance(last, int) and not isinstance(last, bool) and last >= 1):
            bad.append(f"{where}: last must be a positive integer, got {last!r}")
    for k in ("format", "ignore_exit"):
        if k in t and kind != "findings":
            bad.append(f"{where}: {k} is only for findings tiers")
    for k in ("order", "band", "last"):
        if k in t and kind != "convergence":
            bad.append(f"{where}: {k} is only for convergence tiers")
    return bad


def _flags_problems(gate: dict) -> list[str]:
    bad = []
    if "flags" in gate and not isinstance(gate["flags"], bool):
        bad.append(f"[gate] flags must be a bool, got {gate['flags']!r}")
    pats = gate.get("flag_patterns", [])
    if not isinstance(pats, list):
        return bad + [f"[gate] flag_patterns must be a list of regexes, got {pats!r}"]
    for i, rx in enumerate(pats):
        bad += _regex_problem(rx, f"[gate] flag_patterns[{i}]")
    return bad
