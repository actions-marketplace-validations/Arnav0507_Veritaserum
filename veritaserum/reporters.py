"""Reporters: human (default), json, sarif (GitHub inline annotations)."""
from __future__ import annotations
import json

_MARK = {"pass": "PASS", "drifted": "DRIFT", "baselined": "base", "unverifiable": "unv?"}


def human(rows: list[dict], summary: dict) -> str:
    lines = []
    for r in rows:
        m = _MARK[r["status"]]
        extra = ""
        if r["status"] == "drifted" and r["new_violations"]:
            extra = f"  NEW: {r['new_violations'][:3]}"
        lines.append(f"[{m:>5}] {r['id']:<28} {r['type']:<11} {r['evidence']}{extra}")
    s = summary
    verdict = "FAIL" if s["failed"] else "PASS"
    lines.append("")
    lines.append(
        f"== {verdict}  {s['total']} claims: {s['pass']} pass, {s['drifted']} drifted, "
        f"{s['baselined']} baselined, {s['unverifiable']} unverifiable "
        f"({s['gating']} gating)"
    )
    return "\n".join(lines)


def as_json(rows: list[dict], summary: dict) -> str:
    slim = [{k: r[k] for k in (
        "id", "type", "severity", "source", "verdict", "status",
        "confidence", "evidence", "new_violations", "gating")} for r in rows]
    return json.dumps({"summary": summary, "claims": slim}, indent=2)


def _level(sev: str) -> str:
    return "error" if sev == "error" else "warning"


def sarif(rows: list[dict], summary: dict, *, context_files=None) -> str:
    context_files = context_files or []
    results, rule_ids = [], {}
    for r in rows:
        if r["status"] != "drifted":
            continue
        rule_ids.setdefault(r["id"], r)
        # anchor the annotation on the source context file when known
        loc_file = (r["source"].split("#")[0] if r.get("source")
                    else (context_files[0] if context_files else "AGENTS.md"))
        results.append({
            "ruleId": r["id"],
            "level": _level(r["severity"]),
            "message": {"text": f"Context drift: {r['text'] or r['id']} — {r['evidence']}"},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": loc_file},
                "region": {"startLine": 1},
            }}],
        })
    rules = [{
        "id": rid,
        "name": rid,
        "shortDescription": {"text": r["text"] or rid},
        "defaultConfiguration": {"level": _level(r["severity"])},
    } for rid, r in rule_ids.items()]
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "Veritaserum",
                "informationUri": "https://veritaserum.dev",
                "rules": rules,
            }},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2)


def render(fmt: str, rows, summary, *, context_files=None) -> str:
    if fmt == "json":
        return as_json(rows, summary)
    if fmt == "sarif":
        return sarif(rows, summary, context_files=context_files)
    return human(rows, summary)
