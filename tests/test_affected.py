"""Tests for git-diff to affected-claim mapping."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from veritaserum.affected import (
    affected_claims,
    annotate_affected_rows,
    claim_affected_by,
    git_changed_files,
    parse_source_path,
    resolve_base_head,
    summarize_affected,
)
from veritaserum.cli import main
from veritaserum.loader import load_claims
from veritaserum.reporters import human_affected
from veritaserum.schema import validate_claim

ROOT = Path(__file__).parents[1]
DEMO = ROOT / "examples" / "demo-repo"


def _claim(raw: dict):
    return validate_claim(raw)


def test_parse_source_path() -> None:
    assert parse_source_path("AGENTS.md#L6") == "AGENTS.md"
    assert parse_source_path("docs/AGENTS.md") == "docs/AGENTS.md"


def test_forbidden_claim_matches_changed_go_file() -> None:
    claim = _claim(
        {
            "id": "demo-no-printf",
            "type": "forbidden",
            "source": "AGENTS.md#L6",
            "spec": {"globs": ["**/*.go"], "pattern": r"\bfmt\.Printf\b"},
        }
    )
    reasons = claim_affected_by(claim, {"main.go"}, repo=DEMO)
    assert any("matches spec.globs" in reason for reason in reasons)


def test_contains_claim_matches_exact_file() -> None:
    claim = _claim(
        {
            "id": "main-server",
            "type": "contains",
            "spec": {"file": "main.go", "pattern": "ServerMain"},
        }
    )
    reasons = claim_affected_by(claim, {"main.go"}, repo=DEMO)
    assert reasons == ["checked file main.go was edited"]


def test_dir_exists_claim_matches_nested_file() -> None:
    claim = _claim(
        {
            "id": "pkg-dir",
            "type": "dir_exists",
            "spec": {"path": "pkg"},
        }
    )
    reasons = claim_affected_by(claim, {"pkg/handler.go"}, repo=DEMO)
    assert reasons == ["changed file pkg/handler.go is under directory pkg/"]


def test_context_edit_marks_source_touchpoint() -> None:
    claim = _claim(
        {
            "id": "demo-no-printf",
            "type": "forbidden",
            "source": "AGENTS.md#L6",
            "spec": {"globs": ["**/*.go"], "pattern": r"\bfmt\.Printf\b"},
        }
    )
    reasons = claim_affected_by(claim, {"AGENTS.md"}, repo=DEMO)
    assert reasons == ["context file AGENTS.md was edited"]


def test_unrelated_changes_do_not_affect_claim() -> None:
    claim = _claim(
        {
            "id": "demo-port",
            "type": "constant",
            "spec": {
                "globs": ["config.go"],
                "pattern": r'\bPort\s+=\s+"([^"]+)"',
                "expect": ":4984",
            },
        }
    )
    assert claim_affected_by(claim, {"main.go"}, repo=DEMO) == []


def test_affected_claims_filters_full_set() -> None:
    claims = load_claims(DEMO, ["claims/**/*.yml"])
    matched = affected_claims(claims, ["main.go"], repo=DEMO)
    assert [claim.id for claim, _ in matched] == [
        "demo-no-encoding-json",
        "demo-no-printf",
    ]


def test_git_changed_files_between_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "main.go").write_text("package main\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.go"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "main.go").write_text("package main\n\nfunc f() {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.go"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "change"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert git_changed_files(repo, base, head) == ["main.go"]


def test_resolve_base_head_falls_back_to_previous_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "one"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "two"], cwd=repo, check=True, capture_output=True)
    first = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    base, head = resolve_base_head(repo, None, None)
    assert head == "HEAD"
    resolved_base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", base],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert resolved_base == first


def test_stale_annotation_for_gating_drift() -> None:
    rows = annotate_affected_rows(
        [
            {
                "id": "demo-no-printf",
                "status": "drifted",
                "gating": True,
                "source": "AGENTS.md#L6",
                "new_locations": [],
            }
        ],
        {"demo-no-printf": ["changed file main.go matches spec.globs '**/*.go'"]},
        changed_files=["main.go"],
    )
    assert rows[0]["stale"] is True
    assert rows[0]["context_edited"] is False


def test_context_edit_marks_stale_even_when_verified() -> None:
    rows = annotate_affected_rows(
        [
            {
                "id": "demo-no-printf",
                "status": "pass",
                "gating": False,
                "source": "AGENTS.md#L6",
                "new_locations": [],
            }
        ],
        {"demo-no-printf": ["context file AGENTS.md was edited"]},
        changed_files=["AGENTS.md"],
    )
    assert rows[0]["stale"] is True
    assert rows[0]["context_edited"] is True


def test_human_affected_report_mentions_stale() -> None:
    text = human_affected(
        [
            {
                "id": "demo-no-printf",
                "type": "forbidden",
                "status": "drifted",
                "evidence": "1 forbidden occurrence(s)",
                "affected_reasons": ["changed file main.go matches spec.globs '**/*.go'"],
                "new_locations": [],
                "stale": True,
            }
        ],
        summarize_affected(
            [{"status": "drifted", "gating": True, "stale": True, "id": "x"}],
            changed_files=["main.go"],
            total_claims=4,
        ),
    )
    assert "STALE" in text
    assert "main.go" in text


def _init_demo_repo_with_printf_drift(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "compliant baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    main_go = repo / "main.go"
    main_go.write_text(
        main_go.read_text(encoding="utf-8").replace(
            "rest.ServerMain()",
            'fmt.Printf("debug\\n")\n\trest.ServerMain()',
        ).replace(
            'import "example.com/demo/rest"',
            'import "fmt"\nimport "example.com/demo/rest"',
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "main.go"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "introduce printf"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, base, head


def test_affected_cli_on_demo_git_history(tmp_path: Path) -> None:
    repo, base, head = _init_demo_repo_with_printf_drift(tmp_path)
    assert (
        main(
            [
                "affected",
                "--repo",
                str(repo),
                "--base",
                base,
                "--head",
                head,
                "--format",
                "json",
            ]
        )
        == 1
    )


def test_affected_json_includes_changed_files(tmp_path: Path) -> None:
    repo, base, head = _init_demo_repo_with_printf_drift(tmp_path)
    out = tmp_path / "report.json"
    rc = main(
        [
            "affected",
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--format",
            "json",
            "--output",
            str(out),
            "--warn-only",
        ]
    )
    assert rc == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["summary"]["changed_files"] == ["main.go"]
    assert document["summary"]["affected_claims"] == 2
    assert {claim["id"] for claim in document["claims"]} == {
        "demo-no-encoding-json",
        "demo-no-printf",
    }
    stale_ids = {claim["id"] for claim in document["claims"] if claim["stale"]}
    assert stale_ids == {"demo-no-printf"}
