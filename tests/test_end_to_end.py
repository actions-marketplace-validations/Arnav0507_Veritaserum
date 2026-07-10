"""End-to-end: runner scoring, baseline suppression, CLI exit codes, init, reporters."""
import json
import shutil
from pathlib import Path
import pytest

from veritaserum.config import load_config
from veritaserum.loader import load_claims
from veritaserum.runner import run, summarize
from veritaserum.baseline import build_baseline
from veritaserum import reporters, scaffold
from veritaserum.cli import main

FIXTURE = Path(__file__).parent / "fixture_repo"


def _run(repo, baseline=None, naive=False):
    cfg = load_config(repo)
    claims = load_claims(repo, cfg.claims)
    rows = run(repo, claims, global_exclude=cfg.global_exclude,
               baseline=baseline or {}, severity_gate=cfg.severity_gate, naive=naive)
    return rows, summarize(rows)


def test_scoring_matches_gold():
    """Refined verdicts must match the gold labels on the fixture."""
    rows, _ = _run(FIXTURE)
    for r in rows:
        assert r["verdict"] == r["gold"], f"{r['id']}: {r['verdict']} != gold {r['gold']}"


def test_refined_precision_100():
    """Zero false positives vs gold (positive class = DRIFTED)."""
    rows, _ = _run(FIXTURE)
    fp = [r["id"] for r in rows if r["gold"] != "DRIFTED" and r["verdict"] == "DRIFTED"]
    assert fp == [], f"false positives: {fp}"


def test_naive_introduces_false_positive():
    rows, _ = _run(FIXTURE, naive=True)
    fp = [r["id"] for r in rows if r["gold"] != "DRIFTED" and r["verdict"] == "DRIFTED"]
    assert "af-no-printf" in fp  # naive breaks precision


def test_warn_severity_is_non_gating():
    rows, summary = _run(FIXTURE)
    kebab = next(r for r in rows if r["id"] == "af-kebab")
    assert kebab["severity"] == "warn"
    assert kebab["status"] == "drifted"
    assert kebab["gating"] is False  # warn drift must not gate a default(error) build


def test_summary_fails_and_counts():
    _, s = _run(FIXTURE)
    assert s["failed"] is True
    assert s["gating"] == 5  # 6 drifted, but af-kebab is warn


def test_baseline_suppresses_existing_then_catches_new(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    rows, _ = _run(repo)
    base = build_baseline(rows)["violations"]

    # With baseline, all existing drift is suppressed -> pass.
    rows2, s2 = _run(repo, baseline=base)
    assert s2["failed"] is False
    assert s2["drifted"] == 0 and s2["baselined"] == 6

    # Introduce a NEW forbidden violation in a new runtime file.
    (repo / "pkg" / "new.go").write_text(
        "package pkg\nimport \"encoding/json\"\nfunc X(v any) []byte "
        "{ b, _ := json.Marshal(v); return b }\n", encoding="utf-8")
    rows3, s3 = _run(repo, baseline=base)
    ej = next(r for r in rows3 if r["id"] == "af-no-encoding-json")
    assert ej["status"] == "drifted"
    assert ej["new_violations"] == ["pkg/new.go"]  # only the NEW file, not baselined ones
    assert s3["failed"] is True


def test_cli_check_exit_codes():
    assert main(["check", "--repo", str(FIXTURE)]) == 1          # drift -> fail
    assert main(["check", "--repo", str(FIXTURE), "--warn-only"]) == 0
    assert main(["check", "--repo", str(FIXTURE), "--dry-run"]) == 0


def test_cli_check_clean_passes(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    # Update baseline, then a re-check should pass (exit 0).
    assert main(["check", "--repo", str(repo), "--update-baseline"]) == 0
    assert (repo / ".veritaserum-baseline.json").exists()
    assert main(["check", "--repo", str(repo)]) == 0


def test_sarif_is_valid_json_with_results():
    rows, summary = _run(FIXTURE)
    doc = json.loads(reporters.sarif(rows, summary, context_files=["AGENTS.md"]))
    assert doc["version"] == "2.1.0"
    results = doc["runs"][0]["results"]
    assert len(results) == 6  # one per drifted claim
    assert all("ruleId" in r for r in results)


def test_json_report_shape():
    rows, summary = _run(FIXTURE)
    doc = json.loads(reporters.as_json(rows, summary))
    assert doc["summary"]["total"] == 12
    assert len(doc["claims"]) == 12


def test_init_generates_green_claims(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# ctx", encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({
        "packageManager": "pnpm@10.4.1",
        "dependencies": {"react": "^18", "next": "^14"},
    }), encoding="utf-8")
    (repo / "src").mkdir()

    assert main(["init", "--repo", str(repo)]) == 0
    assert (repo / "claims" / "generated.yml").exists()
    assert (repo / ".veritaserum.yml").exists()
    # Generated claims must be green on first run (deterministic facts only).
    assert main(["check", "--repo", str(repo)]) == 0
