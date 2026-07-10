"""Project config: .veritaserum.yml (falls back to sane defaults)."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml

from . import SCHEMA_VERSION

DEFAULT_CLAIM_GLOBS = ["claims/**/*.yml", "claims/**/*.yaml", "claims/**/*.json"]
DEFAULT_GLOBAL_EXCLUDE = ["node_modules", "vendor", "dist", "build", ".git", ".venv"]
CONFIG_NAMES = [".veritaserum.yml", ".veritaserum.yaml"]


@dataclass
class Config:
    context_files: list = field(default_factory=list)
    claims: list = field(default_factory=lambda: list(DEFAULT_CLAIM_GLOBS))
    global_exclude: list = field(default_factory=lambda: list(DEFAULT_GLOBAL_EXCLUDE))
    severity_gate: str = "error"
    baseline: str = ".veritaserum-baseline.json"
    path: Optional[str] = None


def find_config(repo: Path) -> Optional[Path]:
    for name in CONFIG_NAMES:
        p = repo / name
        if p.exists():
            return p
    return None


def load_config(repo: Path, explicit: Optional[str] = None) -> Config:
    cfg_path = Path(explicit) if explicit else find_config(repo)
    if not cfg_path or not cfg_path.exists():
        return Config()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    ver = data.get("schema_version", SCHEMA_VERSION)
    if ver != SCHEMA_VERSION:
        raise ValueError(f"{cfg_path}: schema_version {ver} unsupported (expected {SCHEMA_VERSION})")
    gate = data.get("severity_gate", "error")
    if gate not in ("error", "warn"):
        raise ValueError(f"{cfg_path}: severity_gate must be 'error' or 'warn'")
    return Config(
        context_files=data.get("context_files", []),
        claims=data.get("claims", list(DEFAULT_CLAIM_GLOBS)),
        global_exclude=data.get("global_exclude", list(DEFAULT_GLOBAL_EXCLUDE)),
        severity_gate=gate,
        baseline=data.get("baseline", ".veritaserum-baseline.json"),
        path=str(cfg_path),
    )
