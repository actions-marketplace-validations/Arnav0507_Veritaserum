"""Execute claims, apply the baseline, and calculate gating status."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import DRIFTED, UNVERIFIABLE, VERIFIED
from .checkers import CHECKERS, Evidence
from .schema import Claim

_SEVERITY_RANK = {"warn": 1, "error": 2}
PASS, BASELINED, UNVERIF, DRIFT = "pass", "baselined", "unverifiable", "drifted"


def _is_baselined(key: str, baseline_keys: set[str]) -> bool:
    if key in baseline_keys:
        return True
    # Version 1 baselines used a path as the key. Retain compatibility while
    # version 2 writes occurrence fingerprints for newly updated baselines.
    path = key.split("::", 1)[0]
    return path in baseline_keys


def run(
    repo: Path,
    claims: list[Claim],
    *,
    global_exclude: list[str] | None = None,
    baseline: dict[str, list[str]] | None = None,
    severity_gate: str = "error",
    naive: bool = False,
) -> list[dict[str, Any]]:
    baseline = baseline or {}
    gate_rank = _SEVERITY_RANK[severity_gate]
    rows: list[dict[str, Any]] = []
    for claim in claims:
        result = CHECKERS[claim.type](repo, claim.spec, global_exclude or [], naive)
        baseline_keys = set(baseline.get(claim.id, []))
        new_keys = [
            key for key in result.violations if not _is_baselined(key, baseline_keys)
        ]
        new_key_set = set(new_keys)
        new_locations = [
            asdict(location)
            for location in result.locations
            if location.key in new_key_set
        ]

        if result.verdict == VERIFIED:
            status = PASS
        elif result.verdict == UNVERIFIABLE:
            status = UNVERIF
        else:
            status = DRIFT if new_keys else BASELINED
        gating = status == DRIFT and _SEVERITY_RANK[claim.severity] >= gate_rank
        rows.append(
            {
                "id": claim.id,
                "text": claim.text,
                "type": claim.type,
                "severity": claim.severity,
                "source": claim.source,
                "verdict": result.verdict,
                "status": status,
                "confidence": result.confidence,
                "evidence": result.evidence,
                "locations": [asdict(location) for location in result.locations],
                "new_locations": new_locations,
                "new_violations": new_keys,
                "all_violations": list(result.violations),
                "gating": gating,
                "gold": claim.gold,
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def count(status: str) -> int:
        return sum(row["status"] == status for row in rows)

    return {
        "total": len(rows),
        "pass": count(PASS),
        "drifted": count(DRIFT),
        "baselined": count(BASELINED),
        "unverifiable": count(UNVERIF),
        "gating": sum(bool(row["gating"]) for row in rows),
        "failed": any(row["gating"] for row in rows),
    }
