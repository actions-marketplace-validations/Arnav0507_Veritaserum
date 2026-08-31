"""Load and validate ``.veritaserum.yml``."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import SCHEMA_VERSION
from .schema import SEVERITIES, SchemaError, validate_relative_path

DEFAULT_CLAIM_GLOBS = ["claims/**/*.yml", "claims/**/*.yaml", "claims/**/*.json"]
DEFAULT_GLOBAL_EXCLUDE = [
    ".git",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "vendor",
]
CONFIG_NAMES = [".veritaserum.yml", ".veritaserum.yaml"]
_FIELDS = {
    "schema_version",
    "context_files",
    "claims",
    "global_exclude",
    "severity_gate",
    "baseline",
}


@dataclass(frozen=True)
class Config:
    context_files: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=lambda: list(DEFAULT_CLAIM_GLOBS))
    global_exclude: list[str] = field(
        default_factory=lambda: list(DEFAULT_GLOBAL_EXCLUDE)
    )
    severity_gate: str = "error"
    baseline: str = ".veritaserum-baseline.json"
    path: str | None = None


def find_config(repo: Path) -> Path | None:
    return next((repo / name for name in CONFIG_NAMES if (repo / name).is_file()), None)


def _list_of_paths(
    value: Any, *, where: str, allow_empty: bool = True, globs: bool = False
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise SchemaError(f"{where} must be {qualifier} of repository-relative paths")
    return [
        validate_relative_path(item, where=f"{where}[{index}]", glob=globs)
        for index, item in enumerate(value)
    ]


def _inside_repo(repo: Path, path: Path, *, where: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(repo.resolve())
    except ValueError as exc:
        raise SchemaError(f"{where} must stay within the repository: {path}") from exc
    return resolved


def load_config(repo: Path, explicit: str | None = None) -> Config:
    repo = repo.resolve()
    if explicit:
        candidate = Path(explicit)
        cfg_path = candidate if candidate.is_absolute() else repo / candidate
        cfg_path = _inside_repo(repo, cfg_path, where="config path")
        if not cfg_path.is_file():
            raise SchemaError(f"config file not found: {cfg_path}")
    else:
        cfg_path = find_config(repo)
    if cfg_path is None:
        return Config()

    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SchemaError(f"{cfg_path}: could not read YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SchemaError(f"{cfg_path}: configuration must be a mapping")
    if any(not isinstance(key, str) for key in data):
        raise SchemaError(f"{cfg_path}: configuration field names must be strings")
    unknown = data.keys() - _FIELDS
    if unknown:
        raise SchemaError(f"{cfg_path}: unknown field(s): {', '.join(sorted(unknown))}")
    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"{cfg_path}: schema_version {version} unsupported (expected {SCHEMA_VERSION})"
        )
    gate = data.get("severity_gate", "error")
    if not isinstance(gate, str) or gate not in SEVERITIES:
        raise SchemaError(f"{cfg_path}: severity_gate must be 'error' or 'warn'")

    claims = _list_of_paths(
        data.get("claims", list(DEFAULT_CLAIM_GLOBS)),
        where=f"{cfg_path}: claims",
        allow_empty=False,
        globs=True,
    )
    context_files = _list_of_paths(
        data.get("context_files", []), where=f"{cfg_path}: context_files"
    )
    excludes = _list_of_paths(
        data.get("global_exclude", list(DEFAULT_GLOBAL_EXCLUDE)),
        where=f"{cfg_path}: global_exclude",
        globs=True,
    )
    baseline = validate_relative_path(
        data.get("baseline", ".veritaserum-baseline.json"),
        where=f"{cfg_path}: baseline",
    )
    _inside_repo(repo, repo / baseline, where=f"{cfg_path}: baseline")
    return Config(
        context_files=context_files,
        claims=claims,
        global_exclude=excludes,
        severity_gate=gate,
        baseline=baseline,
        path=str(cfg_path),
    )
