"""Load and validate claim files (YAML or JSON) from configured globs."""
from __future__ import annotations
from pathlib import Path
import json
import yaml

from .schema import Claim, SchemaError, validate_claim_file


def load_claims(repo: Path, globs: list[str]) -> list[Claim]:
    files = _resolve(repo, globs)
    claims: list[Claim] = []
    seen: dict[str, str] = {}
    for f in files:
        doc = _parse(f)
        for claim in validate_claim_file(doc, path=str(f.relative_to(repo))):
            if claim.id in seen:
                raise SchemaError(
                    f"duplicate claim id '{claim.id}' in {f.name} "
                    f"(already defined in {seen[claim.id]})"
                )
            seen[claim.id] = f.name
            claims.append(claim)
    return claims


def _resolve(repo: Path, globs: list[str]) -> list[Path]:
    out: list[Path] = []
    for g in globs:
        out.extend(sorted(p for p in repo.glob(g) if p.is_file()))
    # de-dup preserving order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _parse(f: Path):
    text = f.read_text(encoding="utf-8")
    if f.suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)
