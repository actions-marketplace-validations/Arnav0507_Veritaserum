"""Focused tests for matching, path filtering, and evidence."""
from __future__ import annotations

from pathlib import Path

import pytest

from veritaserum import DRIFTED, UNVERIFIABLE, VERIFIED
from veritaserum.checkers import CHECKERS, iter_files, strip_comments

FIXTURE = Path(__file__).parent / "fixture_repo"
GLOBAL_EXCLUDES = ["node_modules", "vendor", "dist", "build", ".git"]


def check(claim_type: str, spec: dict, *, repo: Path = FIXTURE, naive: bool = False):
    return CHECKERS[claim_type](repo, spec, GLOBAL_EXCLUDES, naive)


def test_file_and_directory_types_are_not_interchangeable(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "file").write_text("", encoding="utf-8")
    assert check("file_exists", {"path": "file"}, repo=tmp_path).verdict == VERIFIED
    assert check("file_exists", {"path": "folder"}, repo=tmp_path).verdict == DRIFTED
    assert check("dir_exists", {"path": "folder"}, repo=tmp_path).verdict == VERIFIED
    assert check("dir_exists", {"path": "file"}, repo=tmp_path).verdict == DRIFTED


def test_contains_reports_match_location() -> None:
    result = check("contains", {"file": "main.go", "pattern": "ServerMain"})
    assert result.verdict == VERIFIED
    assert result.evidence == "matched at main.go:6:7"


def test_contains_missing_input_is_unverifiable() -> None:
    result = check("contains", {"file": "gone.go", "pattern": "x"})
    assert result.verdict == UNVERIFIABLE
    assert result.confidence == 0.0


def test_constant_reports_value_location_and_drift() -> None:
    result = check(
        "constant",
        {
            "globs": ["config.go"],
            "pattern": r'AdminPort\s+=\s+"([^"]+)"',
            "expect": ":4985",
        },
    )
    assert result.verdict == DRIFTED
    assert result.locations[0].path == "config.go"
    assert result.locations[0].line == 5
    assert "expected ':4985'" in result.locations[0].message


def test_constant_checks_every_located_value(tmp_path: Path) -> None:
    (tmp_path / "settings.py").write_text(
        'PORT = "8000"\nOTHER_PORT = "9000"\n', encoding="utf-8"
    )
    result = check(
        "constant",
        {
            "globs": ["*.py"],
            "pattern": r'PORT\s*=\s*"([^"]+)"',
            "expect": "8000",
        },
        repo=tmp_path,
    )
    assert result.verdict == DRIFTED
    assert result.locations[0].line == 2


def test_dependency_present_and_absent() -> None:
    assert (
        check(
            "dependency",
            {"manifest": "go.mod", "pattern": "stretchr/testify"},
        ).verdict
        == VERIFIED
    )
    assert (
        check(
            "dependency", {"manifest": "go.mod", "pattern": "gin-gonic/gin"}
        ).verdict
        == DRIFTED
    )


def test_naming_uses_full_match_and_reports_file() -> None:
    result = check(
        "naming",
        {"globs": ["ui/*.ts"], "name_regex": r"[a-z0-9]+(?:-[a-z0-9]+)*"},
    )
    assert result.verdict == DRIFTED
    assert result.violations == ["ui/BadName.ts"]


def test_no_matching_files_is_unverifiable() -> None:
    result = check(
        "naming",
        {"globs": ["missing/**/*.ts"], "name_regex": ".*"},
    )
    assert result.verdict == UNVERIFIABLE


def test_forbidden_excludes_tests_and_comments_with_source_location() -> None:
    result = check(
        "forbidden",
        {
            "globs": ["**/*.go"],
            "pattern": r"\bjson\.(Marshal|Unmarshal)\b",
            "exclude_tests": True,
            "comment_aware": True,
        },
    )
    assert result.verdict == DRIFTED
    assert len(result.violations) == 1
    assert result.violations[0].startswith("pkg/handler.go::")
    assert (result.locations[0].line, result.locations[0].column) == (7, 9)


@pytest.mark.parametrize(
    ("suffix", "source", "hidden", "visible"),
    [
        (".go", 'url := "https://literal.test" // hidden\nvisible()', "hidden", "visible"),
        (".py", 'value = "# literal"  # hidden\nvisible()', "hidden", "visible"),
        (".sql", "SELECT '-- literal'; -- hidden\nvisible()", "hidden", "visible"),
        (".html", '"<!-- literal" <!-- hidden -->\nvisible', "hidden", "visible"),
    ],
)
def test_comment_masking_preserves_strings_and_offsets(
    suffix: str, source: str, hidden: str, visible: str
) -> None:
    masked = strip_comments(source, suffix)
    assert hidden not in masked
    assert visible in masked
    assert len(masked) == len(source)
    assert masked.count("\n") == source.count("\n")
    assert "literal" in masked


def test_block_comments_preserve_line_count() -> None:
    source = "first()\n/* hidden\nhidden */\nlast()\n"
    masked = strip_comments(source, ".ts")
    assert "hidden" not in masked
    assert masked.count("\n") == source.count("\n")
    assert "first()" in masked and "last()" in masked


def test_rust_lifetimes_do_not_disable_comment_masking() -> None:
    source = (
        "fn borrow<'a>(value: &'a str) -> &'a str {\n"
        "    'retry: loop { break 'retry; } // forbidden_call()\n"
        "    value\n"
        "}\n"
    )
    masked = strip_comments(source, ".rs")
    assert "forbidden_call" not in masked
    assert "'a" in masked
    assert "'retry" in masked
    assert masked.count("\n") == source.count("\n")


def test_rust_char_literals_remain_string_literals() -> None:
    source = "let quote = '/'; // hidden\nvisible();\n"
    masked = strip_comments(source, ".rs")
    assert "hidden" not in masked
    assert "let quote = '/';" in masked
    assert "visible();" in masked


def test_excludes_match_path_components_not_substrings(tmp_path: Path) -> None:
    for directory in ("vendor", "vendorized", "contest", "tests", "src"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "sample.py").write_text("print('x')", encoding="utf-8")
    files = {
        relative
        for _, relative in iter_files(
            tmp_path,
            ["**/*.py"],
            ["src"],
            True,
            ["vendor"],
            False,
        )
    }
    assert files == {"contest/sample.py", "vendorized/sample.py"}


def test_python_test_file_patterns_are_excluded(tmp_path: Path) -> None:
    for name in ("test_api.py", "api_test.py", "api.py"):
        (tmp_path / name).write_text("print('x')", encoding="utf-8")
    files = {
        relative
        for _, relative in iter_files(
            tmp_path, ["*.py"], None, True, None, False
        )
    }
    assert files == {"api.py"}


def test_naive_mode_keeps_test_and_comment_matches() -> None:
    spec = {
        "globs": ["**/*.go"],
        "pattern": r"\bfmt\.Printf\b",
        "exclude_tests": True,
        "comment_aware": True,
    }
    assert check("forbidden", spec).verdict == VERIFIED
    assert check("forbidden", spec, naive=True).verdict == DRIFTED


def test_duplicate_occurrences_get_distinct_stable_keys(tmp_path: Path) -> None:
    source = "print('first')\nprint('second')\n"
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    result = check(
        "forbidden",
        {"globs": ["*.py"], "pattern": r"\bprint\b", "exclude_tests": False},
        repo=tmp_path,
    )
    assert len(result.violations) == 2
    assert result.violations[0].endswith("::1")
    assert result.violations[1].endswith("::2")
