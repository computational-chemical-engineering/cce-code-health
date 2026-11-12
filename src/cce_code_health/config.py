"""Per-repo configuration: .codehealth/config.toml.

Scope patterns use fnmatch semantics on repo-relative paths, where '*' also matches '/':
"src/*" is everything under src/. Only files with a supported extension are analysed.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from . import gitio, profiles

CONFIG_PATH = ".codehealth/config.toml"
SCHEMA = 1
EXTENSIONS = {".py": "python"}


@dataclass
class Config:
    profile: profiles.Profile
    include: list = field(default_factory=lambda: ["*"])
    exclude: list = field(default_factory=list)
    source: str = "default"
    gate: dict = field(default_factory=dict)   # raw [gate] table, interpreted by gate.py

    def in_scope(self, path: str) -> bool:
        return (Path(path).suffix in EXTENSIONS
                and any(fnmatchcase(path, pat) for pat in self.include)
                and not any(fnmatchcase(path, pat) for pat in self.exclude))


def parse(text: str, source: str) -> Config:
    data = tomllib.loads(text)
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{source}: schema must be {SCHEMA}, got {data.get('schema')!r}")
    scope = data.get("scope", {})
    return Config(profile=profiles.get(data.get("profile", "python@0")),
                  include=list(scope.get("include", ["*"])),
                  exclude=list(scope.get("exclude", [])), source=source,
                  gate=dict(data.get("gate", {})))


def load(repo: Path, ref: str | None) -> Config:
    texts = gitio.read_files(repo, ref, [CONFIG_PATH]) if (
        ref is not None or (repo / CONFIG_PATH).is_file()) else {}
    if CONFIG_PATH not in texts:
        return Config(profile=profiles.get("python@0"))
    return parse(texts[CONFIG_PATH], f"{ref or 'worktree'}:{CONFIG_PATH}")
