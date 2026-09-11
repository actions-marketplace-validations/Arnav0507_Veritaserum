"""Human, JSON, and SARIF 2.1.0 reporters."""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

from . import __version__

_MARK = {
    "pass": "PASS",
    "drifted": "DRIFT",
    "baselined": "BASE",
    "unverifiable": "UNVER",
}


def _display_location(location: dict[str, Any]) -> str:
    return f"{location['path']}:{location['line']}:{location['column']}"


def human_affected(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines: list[str] = []
    changed = summary.get("changed_files") or []
    lines.append(
        f"Changed files ({len(changed)}): "
        + (", ".join(changed) if changed else "(none)")
    )
    lines.append(
        f"Affected claims ({summary.get('affected_claims', len(rows))}/"
        f"{summary.get('total_claims', len(rows))})"
    )
    lines.append("")
    for row in rows:
        stale = " STALE" if row.get("stale") else ""
        lines.append(
            f"[{_MARK[row['status']]:>5}] {row['id']:<28} "
            f"{row['type']:<11} {row['evidence']}{stale}"
        )
        for reason in row.get("affected_reasons", [])[:3]:
            lines.append(f"        because {reason}")
        if len(row.get("affected_reasons", [])) > 3:
            extra = len(row["affected_reasons"]) - 3
            lines.append(f"        because ... and {extra} more reason(s)")
        for location in row["new_locations"][:5]:
            lines.append(
                f"        -> {_display_location(location)}: {location['message']}"
            )
    verdict = "FAIL" if summary["failed"] else "PASS"
    lines.extend(
        [
            "",
            f"== {verdict}  {summary.get('stale', 0)} stale, "
            f"{summary.get('gating_stale', 0)} gating stale, "
            f"{summary.get('drifted', 0)} drifted affected claim(s)",
        ]
    )
    return "\n".join(lines)


def human(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(
            f"[{_MARK[row['status']]:>5}] {row['id']:<28} "
            f"{row['type']:<11} {row['evidence']}"
        )
        for location in row["new_locations"][:5]:
            lines.append(
                f"        -> {_display_location(location)}: {location['message']}"
            )
        if len(row["new_locations"]) > 5:
            lines.append(f"        -> ... and {len(row['new_locations']) - 5} more")
    verdict = "FAIL" if summary["failed"] else "PASS"
    lines.extend(
        [
            "",
            f"== {verdict}  {summary['total']} claims: {summary['pass']} pass, "
            f"{summary['drifted']} drifted, {summary['baselined']} baselined, "
            f"{summary['unverifiable']} unverifiable "
            f"({summary['gating']} gating)",
        ]
    )
    return "\n".join(lines)


def as_json(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    fields = (
        "id",
        "type",
        "text",
        "severity",
        "source",
        "verdict",
        "status",
        "confidence",
        "evidence",
        "locations",
        "new_locations",
        "new_violations",
        "gating",
        "affected",
        "affected_reasons",
        "context_edited",
        "stale",
    )
    return json.dumps(
        {
            "schema_version": 1,
            "summary": summary,
            "claims": [
                {key: row[key] for key in fields if key in row} for row in rows
            ],
        },
        indent=2,
    )


def _level(severity: str) -> str:
    return "error" if severity == "error" else "warning"


def _source_location(source: str | None) -> dict[str, Any] | None:
    if not source:
        return None
    path, _, fragment = source.partition("#")
    region: dict[str, int] = {"startLine": 1}
    match = re.fullmatch(r"L(\d+)(?:-L?(\d+))?", fragment)
    if match:
        region["startLine"] = int(match.group(1))
        if match.group(2):
            region["endLine"] = int(match.group(2))
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": path},
            "region": region,
        }
    }


def _physical_location(location: dict[str, Any]) -> dict[str, Any]:
    return {
        "physicalLocation": {
            "artifactLocation": {
                "uri": location["path"],
            },
            "region": {
                "startLine": location["line"],
                "startColumn": location["column"],
            },
        },
        "message": {"text": location["message"]},
    }


def sarif(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    context_files: list[str] | None = None,
) -> str:
    del context_files  # Retained in the public call signature for compatibility.
    results: list[dict[str, Any]] = []
    rules: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["status"] != "drifted":
            continue
        rules.setdefault(
            row["id"],
            {
                "id": row["id"],
                "name": row["id"],
                "shortDescription": {"text": row["text"] or row["id"]},
                "defaultConfiguration": {"level": _level(row["severity"])},
                "properties": {"claimType": row["type"]},
            },
        )
        source = _source_location(row["source"])
        evidence = row["new_locations"]
        locations = [source] if source else [
            _physical_location(evidence[0]) if evidence else {
                "physicalLocation": {
                    "artifactLocation": {"uri": "."},
                    "region": {"startLine": 1},
                }
            }
        ]
        related = [
            {"id": index, **_physical_location(location)}
            for index, location in enumerate(evidence, 1)
        ]
        fingerprint_material = "\n".join(sorted(row["new_violations"]))
        result: dict[str, Any] = {
            "ruleId": row["id"],
            "level": _level(row["severity"]),
            "message": {
                "text": f"Context drift: {row['text'] or row['id']}. {row['evidence']}"
            },
            "locations": locations,
            "partialFingerprints": {
                "veritaserum/v1": sha256(
                    f"{row['id']}\n{fingerprint_material}".encode("utf-8")
                ).hexdigest()
            },
            "properties": {
                "newViolationCount": len(row["new_violations"]),
                "gating": row["gating"],
            },
        }
        if related:
            result["relatedLocations"] = related
        results.append(result)

    document = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Veritaserum",
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/veritaserum/veritaserum",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
                "properties": {"summary": summary},
            }
        ],
    }
    return json.dumps(document, indent=2)


def render(
    format_name: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    context_files: list[str] | None = None,
    affected: bool = False,
) -> str:
    if format_name == "json":
        return as_json(rows, summary)
    if format_name == "sarif":
        return sarif(rows, summary, context_files=context_files)
    if affected:
        return human_affected(rows, summary)
    return human(rows, summary)
