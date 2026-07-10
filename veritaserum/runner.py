"""Runner: execute checkers, apply the baseline, decide gating."""
from __future__ import annotations
from pathlib import Path

from . import VERIFIED, DRIFTED, UNVERIFIABLE
from .checkers import CHECKERS
from .schema import Claim

_SEV_RANK = {"warn": 1, "error": 2}

# per-claim status
PASS, BASELINED, UNVERIF, DRIFT = "pass", "baselined", "unverifiable", "drifted"


def run(repo: Path, claims: list[Claim], *, global_exclude=None,
        baseline: dict | None = None, severity_gate: str = "error",
        naive: bool = False) -> list[dict]:
    baseline = baseline or {}
    gate_rank = _SEV_RANK[severity_gate]
    rows = []
    for c in claims:
        res = CHECKERS[c.type](repo, c.spec, global_exclude, naive)
        base_keys = set(baseline.get(c.id, []))
        new_v = [k for k in res.violations if k not in base_keys]

        if res.verdict == VERIFIED:
            status = PASS
        elif res.verdict == UNVERIFIABLE:
            status = UNVERIF
        else:  # DRIFTED
            status = DRIFT if new_v else BASELINED

        gating = status == DRIFT and _SEV_RANK[c.severity] >= gate_rank
        rows.append({
            "id": c.id, "text": c.text, "type": c.type, "severity": c.severity,
            "source": c.source, "verdict": res.verdict, "status": status,
            "confidence": res.confidence, "evidence": res.evidence,
            "new_violations": new_v, "all_violations": list(res.violations),
            "gating": gating, "gold": c.gold,
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    def n(pred):
        return sum(1 for r in rows if pred(r))
    return {
        "total": len(rows),
        "pass": n(lambda r: r["status"] == PASS),
        "drifted": n(lambda r: r["status"] == DRIFT),
        "baselined": n(lambda r: r["status"] == BASELINED),
        "unverifiable": n(lambda r: r["status"] == UNVERIF),
        "gating": n(lambda r: r["gating"]),
        "failed": any(r["gating"] for r in rows),
    }
