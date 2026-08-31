"""Versioned baseline storage for suppressing only known violations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .schema import SchemaError

BASELINE_VERSION = 2


def _baseline_path(repo: Path, relative_path: str) -> Path:
    repo = repo.resolve()
    path = (repo / relative_path).resolve()
    try:
        path.relative_to(repo)
    except ValueError as exc:
        raise SchemaError(
            f"{relative_path}: baseline resolves outside the repository"
        ) from exc
    return path


def load_baseline(repo: Path, relative_path: str) -> dict[str, list[str]]:
    path = _baseline_path(repo, relative_path)
    if not path.exists():
        return {}
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"{relative_path}: could not parse baseline: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError(f"{relative_path}: baseline must be a JSON object")
    version = data.get("schema_version", 1)
    if version not in {1, BASELINE_VERSION}:
        raise SchemaError(
            f"{relative_path}: baseline schema_version {version} is unsupported"
        )
    violations = data.get("violations", {})
    if not isinstance(violations, dict):
        raise SchemaError(f"{relative_path}: baseline violations must be an object")
    output: dict[str, list[str]] = {}
    for claim_id, keys in violations.items():
        if (
            not isinstance(claim_id, str)
            or not isinstance(keys, list)
            or any(not isinstance(key, str) for key in keys)
        ):
            raise SchemaError(
                f"{relative_path}: each baseline entry must be a list of strings"
            )
        output[claim_id] = sorted(set(keys))
    return output


def build_baseline(rows: list[dict[str, Any]]) -> dict[str, Any]:
    violations = {
        row["id"]: sorted(set(row["all_violations"]))
        for row in rows
        if row["all_violations"]
    }
    return {
        "schema_version": BASELINE_VERSION,
        "violations": dict(sorted(violations.items())),
    }


def write_baseline(repo: Path, relative_path: str, rows: list[dict[str, Any]]) -> Path:
    """Write deterministically and atomically to avoid partial CI artifacts."""
    path = _baseline_path(repo, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp uses exclusive creation, so a pre-created symlink cannot be followed.
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(json.dumps(build_baseline(rows), indent=2, sort_keys=True))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return path
