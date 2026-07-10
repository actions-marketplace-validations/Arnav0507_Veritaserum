"""Claim schema + strict validation. A claim is a typed, checkable assertion
extracted from an AI context file."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

from . import SCHEMA_VERSION

SEVERITIES = {"error", "warn"}

# required spec keys per claim type
_REQUIRED_SPEC = {
    "file_exists": ["path"],
    "dir_exists": ["path"],
    "contains": ["file", "pattern"],
    "constant": ["pattern", "expect"],
    "dependency": ["manifest", "pattern"],
    "naming": ["globs", "name_regex"],
    "forbidden": ["pattern"],
}
TYPES = set(_REQUIRED_SPEC)


class SchemaError(ValueError):
    pass


@dataclass
class Claim:
    id: str
    text: str
    type: str
    spec: dict
    severity: str = "error"
    source: Optional[str] = None
    gold: Optional[str] = None  # eval-only; ignored in production runs
    raw: dict = field(default_factory=dict)


def validate_claim(d: Any, *, where: str = "<claim>") -> Claim:
    if not isinstance(d, dict):
        raise SchemaError(f"{where}: claim must be a mapping, got {type(d).__name__}")
    for k in ("id", "type", "spec"):
        if k not in d:
            raise SchemaError(f"{where}: missing required field '{k}'")
    cid = d["id"]
    if not isinstance(cid, str) or not cid.strip():
        raise SchemaError(f"{where}: 'id' must be a non-empty string")
    ctype = d["type"]
    if ctype not in TYPES:
        raise SchemaError(f"{cid}: unknown claim type '{ctype}' (valid: {sorted(TYPES)})")
    spec = d["spec"]
    if not isinstance(spec, dict):
        raise SchemaError(f"{cid}: 'spec' must be a mapping")
    for req in _REQUIRED_SPEC[ctype]:
        if req not in spec:
            raise SchemaError(f"{cid}: {ctype} claim missing spec.{req}")
    severity = d.get("severity", "error")
    if severity not in SEVERITIES:
        raise SchemaError(f"{cid}: severity must be one of {sorted(SEVERITIES)}")
    return Claim(
        id=cid, text=d.get("text", ""), type=ctype, spec=spec,
        severity=severity, source=d.get("source"), gold=d.get("gold"), raw=d,
    )


def validate_claim_file(doc: Any, *, path: str) -> list[Claim]:
    """A claim file is either a list of claims, or a mapping with
    optional 'schema_version' and a 'claims' list."""
    claims_list = doc
    if isinstance(doc, dict):
        ver = doc.get("schema_version", SCHEMA_VERSION)
        if ver != SCHEMA_VERSION:
            raise SchemaError(f"{path}: schema_version {ver} unsupported (expected {SCHEMA_VERSION})")
        claims_list = doc.get("claims", [])
    if not isinstance(claims_list, list):
        raise SchemaError(f"{path}: expected a list of claims")
    out, seen = [], set()
    for i, c in enumerate(claims_list):
        claim = validate_claim(c, where=f"{path}[{i}]")
        if claim.id in seen:
            raise SchemaError(f"{path}: duplicate claim id '{claim.id}'")
        seen.add(claim.id)
        out.append(claim)
    return out
