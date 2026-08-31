"""Resolve, parse, and validate YAML/JSON claim files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .schema import Claim, SchemaError
from .schema import validate_claim_file


def load_claims(repo: Path, globs: list[str]) -> list[Claim]:
    repo = repo.resolve()
    claims: list[Claim] = []
    seen: dict[str, str] = {}
    for path in _resolve(repo, globs):
        relative = path.relative_to(repo).as_posix()
        document = _parse(path, relative)
        for claim in validate_claim_file(document, path=relative):
            if claim.id in seen:
                raise SchemaError(
                    f"duplicate claim id '{claim.id}' in {relative} "
                    f"(already defined in {seen[claim.id]})"
                )
            seen[claim.id] = relative
            claims.append(claim)
    return claims


def _resolve(repo: Path, globs: list[str]) -> list[Path]:
    output: set[Path] = set()
    for pattern in globs:
        try:
            candidates = repo.glob(pattern)
            for path in candidates:
                resolved = path.resolve()
                try:
                    resolved.relative_to(repo)
                except ValueError as exc:
                    raise SchemaError(
                        f"claim glob resolved outside repository: {path}"
                    ) from exc
                if resolved.is_file():
                    output.add(resolved)
        except (OSError, ValueError) as exc:
            raise SchemaError(f"could not resolve claim glob {pattern!r}: {exc}") from exc
    return sorted(output, key=lambda path: path.relative_to(repo).as_posix())


def _parse(path: Path, relative: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SchemaError(f"{relative}: could not parse claim file: {exc}") from exc
