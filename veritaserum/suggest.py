"""LLM-assisted claim extraction for human review.

The LLM proposes candidate claims from prose context files. Every proposal is
validated by the same strict schema used in CI. Invalid suggestions are
reported and dropped. Nothing is written unless the user supplies ``--output``.

``check`` never calls an LLM.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import Claim, SchemaError, validate_claim_file

CONTEXT_FILES = [
    "AGENTS.md",
    ".github/copilot-instructions.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules",
]

_CLAIM_TYPES_DOC = """\
Valid claim types and required spec fields:

- file_exists: spec.path
- dir_exists: spec.path
- contains: spec.file, spec.pattern
- constant: spec.globs, spec.pattern (must include one capture group), spec.expect
- dependency: spec.manifest, spec.pattern
- naming: spec.globs, spec.name_regex
- forbidden: spec.globs, spec.pattern

Optional spec fields where supported: exclude, exclude_tests, comment_aware.
All paths and globs must be repository-relative. Do not use absolute paths or "..".
Each claim must include: id, text, type, severity (error|warn), source, spec.
Use source like "AGENTS.md#L12" pointing at the prose line the claim encodes.
Prefer conservative, regex-checkable claims. Skip vague guidance that cannot be verified.
"""


@dataclass(frozen=True)
class SuggestResult:
    accepted: list[Claim]
    rejected: list[str]


def discover_context_files(repo: Path, explicit: list[str] | None = None) -> list[Path]:
    repo = repo.resolve()
    if explicit:
        paths = []
        for item in explicit:
            candidate = Path(item)
            path = candidate if candidate.is_absolute() else repo / candidate
            if not path.is_file():
                raise FileNotFoundError(f"context file not found: {item}")
            paths.append(path.resolve())
        return paths
    return [
        (repo / name).resolve()
        for name in CONTEXT_FILES
        if (repo / name).is_file()
    ]


def repo_hints(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    hints: dict[str, Any] = {"source_dirs": [], "manifests": []}
    skip = {".git", "node_modules", "vendor", ".venv", "dist", "build"}
    for path in sorted(repo.iterdir()):
        if path.is_dir() and path.name not in skip and not path.name.startswith("."):
            hints["source_dirs"].append(path.name)
    for manifest in ("go.mod", "package.json", "pyproject.toml", "Cargo.toml"):
        if (repo / manifest).is_file():
            hints["manifests"].append(manifest)
    return hints


def build_prompt(context_path: str, context_text: str, hints: dict[str, Any]) -> str:
    return (
        "You translate AI instruction prose into machine-checkable claims for Veritaserum.\n"
        "Return JSON only with this shape:\n"
        '{"claims": [{"id": "...", "text": "...", "type": "...", '
        '"severity": "error", "source": "AGENTS.md#L12", "spec": {...}}]}\n\n'
        f"{_CLAIM_TYPES_DOC}\n"
        f"Repository hints: {json.dumps(hints, sort_keys=True)}\n"
        f"Context file: {context_path}\n\n"
        "---\n"
        f"{context_text}\n"
        "---\n"
        "Emit at most 10 high-confidence claims. Omit prose that is not checkable."
    )


def extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    return json.loads(stripped)


def parse_llm_response(text: str, *, where: str = "llm-response") -> list[dict[str, Any]]:
    """Extract raw claim mappings from model JSON without validating them."""
    payload = extract_json_payload(text)
    if isinstance(payload, list):
        claims_list = payload
    elif isinstance(payload, dict):
        if "claims" not in payload:
            raise SchemaError(f"{where}: missing required field 'claims'")
        claims_list = payload["claims"]
    else:
        raise SchemaError(f"{where}: expected a JSON object or claim list")
    if not isinstance(claims_list, list):
        raise SchemaError(f"{where}: expected a list of claims")
    for index, item in enumerate(claims_list):
        if not isinstance(item, dict):
            raise SchemaError(
                f"{where}[{index}]: claim must be a mapping, got {type(item).__name__}"
            )
    return claims_list


def validate_suggestions(
    raw_claims: list[dict[str, Any]], *, where: str = "suggestions"
) -> SuggestResult:
    accepted: list[Claim] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_claims):
        label = f"{where}[{index}]"
        try:
            document = validate_claim_file({"schema_version": 1, "claims": [item]}, path=label)
        except SchemaError as exc:
            rejected.append(f"{label}: {exc}")
            continue
        claim = document[0]
        if claim.id in seen:
            rejected.append(f"{label}: duplicate id '{claim.id}'")
            continue
        seen.add(claim.id)
        accepted.append(claim)
    return SuggestResult(accepted=accepted, rejected=rejected)


def call_llm(prompt: str, *, api_key: str, base_url: str, model: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You emit conservative, schema-valid claim JSON for Veritaserum. "
                        "Never include commentary outside JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc.reason}") from exc

    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM response did not include message content") from exc


def resolve_llm_settings() -> tuple[str, str, str]:
    api_key = os.environ.get("VERITASERUM_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "missing LLM API key: set VERITASERUM_LLM_API_KEY or OPENAI_API_KEY, "
            "or pass --input with a saved model response"
        )
    base_url = os.environ.get("VERITASERUM_LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("VERITASERUM_LLM_MODEL", "gpt-4o-mini")
    return api_key, base_url, model


def suggest_for_file(
    repo: Path,
    context_path: Path,
    *,
    llm_text: str | None = None,
    max_claims: int = 10,
) -> SuggestResult:
    relative = context_path.resolve().relative_to(repo.resolve()).as_posix()
    context_text = context_path.read_text(encoding="utf-8")
    if llm_text is None:
        prompt = build_prompt(relative, context_text, repo_hints(repo))
        api_key, base_url, model = resolve_llm_settings()
        llm_text = call_llm(prompt, api_key=api_key, base_url=base_url, model=model)
    raw = parse_llm_response(llm_text, where=relative)
    if len(raw) > max_claims:
        raw = raw[:max_claims]
    return validate_suggestions(raw, where=relative)


def suggest(
    repo: Path,
    *,
    context_files: list[str] | None = None,
    input_path: Path | None = None,
    max_claims: int = 10,
) -> SuggestResult:
    repo = repo.resolve()
    paths = discover_context_files(repo, context_files)
    if not paths:
        raise FileNotFoundError(
            "no context files found (pass --context or add AGENTS.md / CLAUDE.md / similar)"
        )

    if input_path is not None:
        llm_text = input_path.read_text(encoding="utf-8")
        where = paths[0].relative_to(repo).as_posix()
        raw = parse_llm_response(llm_text, where=where)
        if len(raw) > max_claims:
            raw = raw[:max_claims]
        return validate_suggestions(raw, where=where)

    accepted = []
    rejected = []
    seen: set[str] = set()
    for path in paths:
        relative = path.relative_to(repo).as_posix()
        try:
            result = suggest_for_file(repo, path, max_claims=max_claims)
        except (RuntimeError, SchemaError, UnicodeError) as exc:
            rejected.append(f"{relative}: {exc}")
            continue
        for claim in result.accepted:
            if claim.id in seen:
                rejected.append(f"{relative}: duplicate id '{claim.id}'")
                continue
            seen.add(claim.id)
            accepted.append(claim)
        rejected.extend(result.rejected)
    return SuggestResult(accepted=accepted, rejected=rejected)


def claims_document(claims: list[Claim]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "claims": [claim.raw for claim in claims],
    }
