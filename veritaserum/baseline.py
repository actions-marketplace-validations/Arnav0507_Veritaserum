"""Baseline / suppression — the adoption-critical feature.

Records the violations that already exist so CI gates only on NEW drift. Keys are
per-violation and line-number-free (file paths, not line numbers) so they survive
code churn. Without this, installing on a brownfield repo fails the build on day
one with legacy violations and gets uninstalled.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json

from . import SCHEMA_VERSION


def load_baseline(repo: Path, rel_path: str) -> dict:
    p = repo / rel_path
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("violations", {})


def build_baseline(rows: list[dict]) -> dict:
    violations = {}
    for r in rows:
        if r["all_violations"]:
            violations[r["id"]] = sorted(r["all_violations"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "violations": violations,
    }


def write_baseline(repo: Path, rel_path: str, rows: list[dict]) -> Path:
    p = repo / rel_path
    p.write_text(json.dumps(build_baseline(rows), indent=2) + "\n", encoding="utf-8")
    return p
