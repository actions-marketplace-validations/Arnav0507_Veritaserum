"""Claim schema and strict validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath, PurePosixPath, PureWindowsPath
import re
from typing import Any

from . import SCHEMA_VERSION

SEVERITIES = {"error", "warn"}
_COMMON_FIELDS = {"id", "text", "type", "severity", "source", "spec", "gold"}
_SPEC_FIELDS = {
    "file_exists": {"path"},
    "dir_exists": {"path"},
    "contains": {"file", "pattern", "comment_aware"},
    "constant": {"globs", "pattern", "expect", "exclude", "exclude_tests", "comment_aware"},
    "dependency": {"manifest", "pattern"},
    "naming": {"globs", "name_regex", "exclude", "exclude_tests"},
    "forbidden": {"globs", "pattern", "exclude", "exclude_tests", "comment_aware"},
}
_REQUIRED_SPEC = {
    "file_exists": {"path"},
    "dir_exists": {"path"},
    "contains": {"file", "pattern"},
    "constant": {"globs", "pattern", "expect"},
    "dependency": {"manifest", "pattern"},
    "naming": {"globs", "name_regex"},
    "forbidden": {"globs", "pattern"},
}
TYPES = set(_REQUIRED_SPEC)


class SchemaError(ValueError):
    """Raised when configuration or claims do not match the public schema."""


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    type: str
    spec: dict[str, Any]
    severity: str = "error"
    source: str | None = None
    gold: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def validate_relative_path(value: Any, *, where: str, glob: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where} must be a non-empty string")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (
        PurePath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise SchemaError(f"{where} must stay within the repository: {value!r}")
    if not glob and any(ch in value for ch in "*?[]"):
        raise SchemaError(f"{where} must be a path, not a glob: {value!r}")
    return value


def _string(value: Any, *, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise SchemaError(f"{where} must be {qualifier}")
    return value


def _string_list(
    value: Any, *, where: str, paths: bool = False, allow_empty: bool = False
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise SchemaError(f"{where} must be {qualifier} of strings")
    result = []
    for index, item in enumerate(value):
        result.append(
            validate_relative_path(item, where=f"{where}[{index}]", glob=True)
            if paths
            else _string(item, where=f"{where}[{index}]")
        )
    return result


def _regex(value: Any, *, where: str) -> str:
    pattern = _string(value, where=where)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise SchemaError(f"{where} is not a valid regular expression: {exc}") from exc
    return pattern


def _validate_spec(claim_id: str, claim_type: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{claim_id}: 'spec' must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{claim_id}: spec field names must be strings")
    missing = _REQUIRED_SPEC[claim_type] - value.keys()
    if missing:
        raise SchemaError(f"{claim_id}: {claim_type} claim missing spec.{sorted(missing)[0]}")
    unknown = value.keys() - _SPEC_FIELDS[claim_type]
    if unknown:
        raise SchemaError(f"{claim_id}: unknown spec field(s): {', '.join(sorted(unknown))}")

    spec = dict(value)
    for key in ("path", "file", "manifest"):
        if key in spec:
            spec[key] = validate_relative_path(spec[key], where=f"{claim_id}: spec.{key}")
    if "globs" in spec:
        spec["globs"] = _string_list(
            spec["globs"], where=f"{claim_id}: spec.globs", paths=True
        )
    if "exclude" in spec:
        spec["exclude"] = _string_list(
            spec["exclude"],
            where=f"{claim_id}: spec.exclude",
            paths=True,
            allow_empty=True,
        )
    for key in ("pattern", "name_regex"):
        if key in spec:
            spec[key] = _regex(spec[key], where=f"{claim_id}: spec.{key}")
    if claim_type == "constant" and re.compile(spec["pattern"]).groups < 1:
        raise SchemaError(
            f"{claim_id}: constant spec.pattern must contain a capture group"
        )
    if "expect" in spec and not isinstance(spec["expect"], (str, int, float, bool)):
        raise SchemaError(f"{claim_id}: spec.expect must be a scalar")
    for key in ("exclude_tests", "comment_aware"):
        if key in spec and not isinstance(spec[key], bool):
            raise SchemaError(f"{claim_id}: spec.{key} must be a boolean")
    return spec


def validate_claim(value: Any, *, where: str = "<claim>") -> Claim:
    if not isinstance(value, dict):
        raise SchemaError(f"{where}: claim must be a mapping, got {type(value).__name__}")
    if any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{where}: claim field names must be strings")
    unknown = value.keys() - _COMMON_FIELDS
    if unknown:
        raise SchemaError(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")
    for key in ("id", "type", "spec"):
        if key not in value:
            raise SchemaError(f"{where}: missing required field '{key}'")

    claim_id = _string(value["id"], where=f"{where}: id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", claim_id):
        raise SchemaError(f"{where}: id contains unsupported characters: {claim_id!r}")
    claim_type = _string(value["type"], where=f"{claim_id}: type")
    if claim_type not in TYPES:
        raise SchemaError(
            f"{claim_id}: unknown claim type {claim_type!r} (valid: {sorted(TYPES)})"
        )
    severity = value.get("severity", "error")
    if not isinstance(severity, str) or severity not in SEVERITIES:
        raise SchemaError(f"{claim_id}: severity must be one of {sorted(SEVERITIES)}")
    text = _string(value.get("text", ""), where=f"{claim_id}: text", allow_empty=True)
    source = value.get("source")
    if source is not None:
        source = _string(source, where=f"{claim_id}: source")
        source_path, separator, fragment = source.partition("#")
        source_path = validate_relative_path(
            source_path, where=f"{claim_id}: source path"
        )
        if separator:
            match = re.fullmatch(r"L(\d+)(?:-L?(\d+))?", fragment)
            if not match:
                raise SchemaError(
                    f"{claim_id}: source location must look like "
                    "'AGENTS.md#L42' or '#L42-L44'"
                )
            start_line = int(match.group(1))
            end_line = int(match.group(2)) if match.group(2) else start_line
            if start_line < 1 or end_line < 1:
                raise SchemaError(f"{claim_id}: source line numbers must be at least 1")
            if end_line < start_line:
                raise SchemaError(f"{claim_id}: source line range is reversed")
            source = f"{source_path}#{fragment}"
        else:
            source = source_path
    gold = value.get("gold")
    if gold is not None and (
        not isinstance(gold, str)
        or gold not in {"VERIFIED", "DRIFTED", "UNVERIFIABLE"}
    ):
        raise SchemaError(f"{claim_id}: gold has an invalid verdict")

    return Claim(
        id=claim_id,
        text=text,
        type=claim_type,
        spec=_validate_spec(claim_id, claim_type, value["spec"]),
        severity=severity,
        source=source,
        gold=gold,
        raw=dict(value),
    )


def validate_claim_file(doc: Any, *, path: str) -> list[Claim]:
    """Validate a list of claims or a versioned mapping containing ``claims``."""
    claims_list = doc
    if isinstance(doc, dict):
        if any(not isinstance(key, str) for key in doc):
            raise SchemaError(f"{path}: document field names must be strings")
        unknown = doc.keys() - {"schema_version", "claims"}
        if unknown:
            raise SchemaError(f"{path}: unknown field(s): {', '.join(sorted(unknown))}")
        version = doc.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise SchemaError(
                f"{path}: schema_version {version} unsupported (expected {SCHEMA_VERSION})"
            )
        if "claims" not in doc:
            raise SchemaError(f"{path}: missing required field 'claims'")
        claims_list = doc["claims"]
    if not isinstance(claims_list, list):
        raise SchemaError(f"{path}: expected a list of claims")

    output: list[Claim] = []
    seen: set[str] = set()
    for index, item in enumerate(claims_list):
        claim = validate_claim(item, where=f"{path}[{index}]")
        if claim.id in seen:
            raise SchemaError(f"{path}: duplicate claim id '{claim.id}'")
        seen.add(claim.id)
        output.append(claim)
    return output
