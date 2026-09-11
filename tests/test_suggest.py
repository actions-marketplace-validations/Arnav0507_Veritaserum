"""Tests for LLM-assisted claim extraction."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from veritaserum.cli import main
from veritaserum.schema import SchemaError
from veritaserum.suggest import (
    build_prompt,
    claims_document,
    discover_context_files,
    extract_json_payload,
    parse_llm_response,
    suggest,
    validate_suggestions,
)

ROOT = Path(__file__).parents[1]
FIXTURE_RESPONSE = Path(__file__).parent / "fixtures" / "suggest" / "agents-response.json"
DEMO = ROOT / "examples" / "demo-repo"


def test_extract_json_payload_from_fenced_response() -> None:
    payload = extract_json_payload(
        'Here you go:\n```json\n{"claims": []}\n```\n'
    )
    assert payload == {"claims": []}


def test_parse_llm_response_extracts_raw_claims() -> None:
    text = FIXTURE_RESPONSE.read_text(encoding="utf-8")
    claims = parse_llm_response(text, where="AGENTS.md")
    assert len(claims) == 4
    assert claims[0]["id"] == "no-direct-print"


def test_validate_suggestions_keeps_only_schema_valid_claims() -> None:
    raw = json.loads(FIXTURE_RESPONSE.read_text(encoding="utf-8"))["claims"]
    result = validate_suggestions(raw, where="AGENTS.md")
    assert [claim.id for claim in result.accepted] == ["no-direct-print", "default-port"]
    assert len(result.rejected) == 2
    assert any("not a valid regular expression" in item for item in result.rejected)
    assert any("missing spec.path" in item for item in result.rejected)


def test_build_prompt_includes_context_and_hints() -> None:
    prompt = build_prompt(
        "AGENTS.md",
        "- Never use print\n",
        {"source_dirs": ["src"], "manifests": ["pyproject.toml"]},
    )
    assert "AGENTS.md" in prompt
    assert "Never use print" in prompt
    assert "pyproject.toml" in prompt
    assert "forbidden" in prompt


def test_discover_context_files_prefers_explicit_paths(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Context\n", encoding="utf-8")
    paths = discover_context_files(tmp_path, ["AGENTS.md"])
    assert paths == [agents.resolve()]


def test_discover_context_files_errors_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="context file not found"):
        discover_context_files(tmp_path, ["missing.md"])


def test_suggest_with_input_file(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Context\n", encoding="utf-8")
    result = suggest(tmp_path, input_path=FIXTURE_RESPONSE)
    assert [claim.id for claim in result.accepted] == ["no-direct-print", "default-port"]
    assert len(result.rejected) == 2


def test_suggest_cli_writes_reviewable_yaml(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Context\n", encoding="utf-8")
    output = tmp_path / "claims" / "suggested.yml"
    rc = main(
        [
            "suggest",
            "--repo",
            str(tmp_path),
            "--context",
            "AGENTS.md",
            "--input",
            str(FIXTURE_RESPONSE),
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    document = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert [claim["id"] for claim in document["claims"]] == [
        "no-direct-print",
        "default-port",
    ]


def test_suggest_cli_requires_api_key_or_input(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Context\n", encoding="utf-8")
    assert main(["suggest", "--repo", str(tmp_path), "--context", "AGENTS.md"]) == 2


def test_suggest_cli_reports_invalid_input(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Context\n", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "claims"}', encoding="utf-8")
    assert (
        main(
            [
                "suggest",
                "--repo",
                str(tmp_path),
                "--context",
                "AGENTS.md",
                "--input",
                str(bad),
            ]
        )
        == 2
    )


def test_claims_document_is_strict_yaml_ready() -> None:
    result = suggest(DEMO, input_path=FIXTURE_RESPONSE)
    document = claims_document(result.accepted)
    dumped = yaml.safe_dump(document, sort_keys=False)
    reparsed = yaml.safe_load(dumped)
    assert reparsed["claims"][0]["type"] == "forbidden"


def test_parse_llm_response_rejects_non_mapping_payload() -> None:
    with pytest.raises(SchemaError, match="expected a JSON object or claim list"):
        parse_llm_response("42", where="AGENTS.md")
