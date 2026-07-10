"""Unit tests for individual checkers, incl. the precision-critical behavior."""
from pathlib import Path
import pytest

from veritaserum import VERIFIED, DRIFTED, UNVERIFIABLE
from veritaserum.checkers import CHECKERS, strip_comments

FIXTURE = Path(__file__).parent / "fixture_repo"
GE = ["node_modules", "vendor", "dist", "build", ".git"]


def chk(ctype, spec, naive=False):
    return CHECKERS[ctype](FIXTURE, spec, GE, naive)


def test_file_exists_pass_and_fail():
    assert chk("file_exists", {"path": "AGENTS.md"}).verdict == VERIFIED
    r = chk("file_exists", {"path": "NOPE.md"})
    assert r.verdict == DRIFTED and r.violations == ["NOPE.md"]


def test_dir_exists():
    assert chk("dir_exists", {"path": "pkg"}).verdict == VERIFIED
    assert chk("dir_exists", {"path": "graphql"}).verdict == DRIFTED


def test_contains():
    assert chk("contains", {"file": "main.go", "pattern": "ServerMain"}).verdict == VERIFIED
    assert chk("contains", {"file": "main.go", "pattern": "NotThere"}).verdict == DRIFTED
    assert chk("contains", {"file": "gone.go", "pattern": "x"}).verdict == UNVERIFIABLE


def test_constant_match_and_drift():
    ok = chk("constant", {"globs": ["config.go"],
                          "pattern": r'\bPort\s+=\s+"([^"]+)"', "expect": ":4984"})
    assert ok.verdict == VERIFIED
    drift = chk("constant", {"globs": ["config.go"],
                            "pattern": r'AdminPort\s+=\s+"([^"]+)"', "expect": ":4985"})
    assert drift.verdict == DRIFTED


def test_dependency():
    assert chk("dependency", {"manifest": "go.mod", "pattern": "stretchr/testify"}).verdict == VERIFIED
    assert chk("dependency", {"manifest": "go.mod", "pattern": "gin-gonic/gin"}).verdict == DRIFTED


def test_naming_violation():
    r = chk("naming", {"globs": ["ui/*.ts"], "name_regex": r"^[a-z0-9]+(-[a-z0-9]+)*$"})
    assert r.verdict == DRIFTED
    assert "ui/BadName.ts" in r.violations
    assert "ui/good-name.ts" not in r.violations


def test_forbidden_real_hit():
    r = chk("forbidden", {"globs": ["**/*.go"], "pattern": r"\bjson\.(Marshal|Unmarshal)\b",
                          "exclude_tests": True})
    assert r.verdict == DRIFTED
    assert r.violations == ["pkg/handler.go"]  # test file + comment excluded


def test_forbidden_refined_vs_naive_precision():
    """The core Spike 2 insight: naive over-flags (false positive), refined is clean."""
    spec = {"globs": ["**/*.go"], "pattern": r"\bfmt\.Printf\b",
            "exclude_tests": True, "comment_aware": True}
    refined = chk("forbidden", spec, naive=False)
    naive = chk("forbidden", spec, naive=True)
    assert refined.verdict == VERIFIED, "refined must not flag comment/test-only usage"
    assert naive.verdict == DRIFTED, "naive should over-flag (demonstrates the delta)"


def test_strip_comments_go():
    src = "a := 1 // json.Marshal here\n/* fmt.Printf */ b := 2"
    out = strip_comments(src, ".go")
    assert "json.Marshal" not in out and "fmt.Printf" not in out
    assert "a := 1" in out and "b := 2" in out


def test_strip_comments_python():
    assert "secret" not in strip_comments("x = 1  # secret", ".py")
