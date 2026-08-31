"""Configuration, baseline, reporting, CLI, and packaging-facing tests."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
import yaml

from veritaserum import reporters
from veritaserum import baseline as baseline_module
from veritaserum.baseline import build_baseline, load_baseline, write_baseline
from veritaserum.cli import main
from veritaserum.config import load_config
from veritaserum.loader import load_claims
from veritaserum.runner import run, summarize
from veritaserum.schema import SchemaError, validate_claim
from veritaserum.schema import validate_relative_path

ROOT = Path(__file__).parents[1]
FIXTURE = Path(__file__).parent / "fixture_repo"


def run_fixture(repo: Path, baseline: dict | None = None, naive: bool = False):
    config = load_config(repo)
    claims = load_claims(repo, config.claims)
    rows = run(
        repo,
        claims,
        global_exclude=config.global_exclude,
        baseline=baseline,
        severity_gate=config.severity_gate,
        naive=naive,
    )
    return rows, summarize(rows)


def test_fixture_verdicts_match_reviewed_labels() -> None:
    rows, _ = run_fixture(FIXTURE)
    assert {row["id"]: row["verdict"] for row in rows} == {
        row["id"]: row["gold"] for row in rows
    }


def test_warn_severity_does_not_gate_error_threshold() -> None:
    rows, summary = run_fixture(FIXTURE)
    naming = next(row for row in rows if row["id"] == "af-kebab")
    assert naming["status"] == "drifted"
    assert naming["gating"] is False
    assert summary["gating"] == 5
    assert summary["failed"] is True


def test_baseline_suppresses_existing_and_catches_new_same_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    rows, _ = run_fixture(repo)
    baseline = build_baseline(rows)["violations"]

    _, suppressed = run_fixture(repo, baseline)
    assert suppressed["failed"] is False
    assert suppressed["baselined"] == 6

    handler = repo / "pkg" / "handler.go"
    handler.write_text(
        handler.read_text(encoding="utf-8").replace(
            "return json.Marshal(v)",
            "_, _ = json.Marshal(v)\n\treturn json.Marshal(v)",
        ),
        encoding="utf-8",
    )
    changed_rows, changed = run_fixture(repo, baseline)
    forbidden = next(
        row for row in changed_rows if row["id"] == "af-no-encoding-json"
    )
    assert changed["failed"] is True
    assert len(forbidden["new_violations"]) == 1
    assert forbidden["new_locations"][0]["path"] == "pkg/handler.go"


def test_baseline_output_is_deterministic_and_versioned(tmp_path: Path) -> None:
    rows, _ = run_fixture(FIXTURE)
    path = write_baseline(tmp_path, "state/baseline.json", rows)
    first = path.read_bytes()
    write_baseline(tmp_path, "state/baseline.json", rows)
    assert path.read_bytes() == first
    assert json.loads(first)["schema_version"] == 2


def test_baseline_write_does_not_follow_predictable_temp_symlink(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "state"
    baseline_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("do not overwrite\n", encoding="utf-8")
    predictable = baseline_dir / ".baseline.json.tmp"
    try:
        predictable.symlink_to(victim)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    rows, _ = run_fixture(FIXTURE)
    result = write_baseline(tmp_path, "state/baseline.json", rows)

    assert result.is_file()
    assert victim.read_text(encoding="utf-8") == "do not overwrite\n"
    assert predictable.is_symlink()
    assert not list(baseline_dir.glob(".baseline.json.*.tmp"))


def test_baseline_write_cleans_unique_temp_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline_dir = tmp_path / "state"
    baseline_dir.mkdir()
    rows, _ = run_fixture(FIXTURE)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(baseline_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_baseline(tmp_path, "state/baseline.json", rows)

    assert not list(baseline_dir.glob(".baseline.json.*.tmp"))
    assert not (baseline_dir / "baseline.json").exists()


def test_invalid_baseline_is_configuration_error(tmp_path: Path) -> None:
    (tmp_path / "baseline.json").write_text('{"violations": []}', encoding="utf-8")
    with pytest.raises(SchemaError, match="violations must be an object"):
        load_baseline(tmp_path, "baseline.json")


@pytest.mark.parametrize(
    "claim",
    [
        {"id": "bad", "type": "file_exists", "spec": {"path": "../secret"}},
        {"id": "bad", "type": "forbidden", "spec": {"pattern": "["}},
        {
            "id": "bad",
            "type": "constant",
            "spec": {"globs": ["*.py"], "pattern": "no capture", "expect": "x"},
        },
        {
            "id": "bad",
            "type": "naming",
            "spec": {"globs": "*.py", "name_regex": ".*"},
        },
        {
            "id": "bad",
            "type": "file_exists",
            "spec": {"path": "README.md", "typo": True},
        },
        {"id": "bad", "type": [], "spec": {}},
        {
            "id": "bad",
            "type": "file_exists",
            "severity": [],
            "spec": {"path": "README.md"},
        },
    ],
)
def test_schema_rejects_unsafe_or_mistyped_claims(claim: dict) -> None:
    with pytest.raises(SchemaError):
        validate_claim(claim)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", r"C:\secret.txt"])
def test_paths_are_cross_platform_repository_relative(path: str) -> None:
    with pytest.raises(SchemaError, match="within the repository"):
        validate_relative_path(path, where="test")


@pytest.mark.parametrize(
    "source",
    ["AGENTS.md#L0", "AGENTS.md#L0-L1", "AGENTS.md#L1-L0", "AGENTS.md#L2-L1"],
)
def test_source_locations_require_positive_ordered_lines(source: str) -> None:
    with pytest.raises(SchemaError):
        validate_claim(
            {
                "id": "source-lines",
                "type": "file_exists",
                "source": source,
                "spec": {"path": "README.md"},
            }
        )


def test_source_path_is_normalized_before_sarif() -> None:
    claim = validate_claim(
        {
            "id": "normalized-source",
            "text": "A claim with a Windows-style source path.",
            "type": "file_exists",
            "source": r"docs\AGENTS.md#L2-L3",
            "spec": {"path": "README.md"},
        }
    )
    assert claim.source == "docs/AGENTS.md#L2-L3"
    row = {
        "id": claim.id,
        "text": claim.text,
        "type": claim.type,
        "severity": claim.severity,
        "source": claim.source,
        "status": "drifted",
        "evidence": "missing",
        "new_locations": [],
        "new_violations": ["README.md"],
        "gating": True,
    }
    document = json.loads(reporters.sarif([row], {"failed": True}))
    location = document["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "docs/AGENTS.md"
    assert location["region"] == {"startLine": 2, "endLine": 3}


def test_config_is_strict_and_explicit_path_is_repo_relative(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "custom.yml").write_text(
        "schema_version: 1\nclaims: [claims.yml]\nunknown: true\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="unknown field"):
        load_config(tmp_path, "config/custom.yml")
    with pytest.raises(SchemaError, match="stay within"):
        load_config(tmp_path, "../outside.yml")


def test_duplicate_claim_ids_across_files_are_rejected(tmp_path: Path) -> None:
    claims = tmp_path / "claims"
    claims.mkdir()
    document = [{"id": "same", "type": "file_exists", "spec": {"path": "x"}}]
    for name in ("a.yml", "b.yml"):
        (claims / name).write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(SchemaError, match="duplicate claim id"):
        load_claims(tmp_path, ["claims/*.yml"])


def test_sarif_anchors_claim_and_relates_code_evidence() -> None:
    rows, summary = run_fixture(FIXTURE)
    document = json.loads(reporters.sarif(rows, summary))
    assert document["version"] == "2.1.0"
    result = next(
        item
        for item in document["runs"][0]["results"]
        if item["ruleId"] == "af-no-encoding-json"
    )
    primary = result["locations"][0]["physicalLocation"]
    assert primary["artifactLocation"]["uri"] == "AGENTS.md"
    assert primary["region"]["startLine"] == 4
    related = result["relatedLocations"][0]["physicalLocation"]
    assert related["artifactLocation"]["uri"] == "pkg/handler.go"
    assert related["region"]["startLine"] == 7
    assert "veritaserum/v1" in result["partialFingerprints"]


def test_json_report_contains_locations() -> None:
    rows, summary = run_fixture(FIXTURE)
    document = json.loads(reporters.as_json(rows, summary))
    assert document["schema_version"] == 1
    assert document["summary"]["total"] == 12
    assert any(claim["new_locations"] for claim in document["claims"])


def test_cli_exit_codes_and_sarif_sidecar(tmp_path: Path) -> None:
    sarif = tmp_path / "result.sarif"
    assert (
        main(
            [
                "check",
                "--repo",
                str(FIXTURE),
                "--sarif-output",
                str(sarif),
            ]
        )
        == 1
    )
    assert json.loads(sarif.read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert main(["check", "--repo", str(FIXTURE), "--warn-only"]) == 0
    assert main(["check", "--repo", str(FIXTURE), "--dry-run"]) == 0


def test_cli_reports_invalid_config_as_usage_error(tmp_path: Path) -> None:
    (tmp_path / ".veritaserum.yml").write_text(
        "claims: not-a-list\n", encoding="utf-8"
    )
    assert main(["check", "--repo", str(tmp_path)]) == 2


def test_cli_baseline_round_trip(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    assert main(["check", "--repo", str(repo), "--update-baseline"]) == 0
    assert main(["check", "--repo", str(repo)]) == 0


def test_init_generates_claims_that_pass(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Context\n", encoding="utf-8")
    (repo / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@10.4.1",
                "dependencies": {"react": "^18", "next": "^14"},
            }
        ),
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    assert main(["init", "--repo", str(repo)]) == 0
    assert main(["check", "--repo", str(repo)]) == 0


def test_action_is_rooted_and_installs_its_own_checkout() -> None:
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    install_step = next(
        step for step in action["runs"]["steps"] if step["name"] == "Install Veritaserum"
    )
    assert "${{ github.action_path }}" in install_step["env"].values()
    assert "$ACTION_PATH" in install_step["run"]
    assert "version" not in action.get("inputs", {})
