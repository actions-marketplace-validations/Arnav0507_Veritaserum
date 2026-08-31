"""Deterministic checkers with stable, source-located evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterator

from . import DRIFTED, UNVERIFIABLE, VERIFIED


@dataclass(frozen=True)
class Evidence:
    path: str
    line: int = 1
    column: int = 1
    message: str = ""
    snippet: str = ""
    key: str = ""


@dataclass
class CheckResult:
    verdict: str
    confidence: float
    evidence: str
    violations: list[str] = field(default_factory=list)
    locations: list[Evidence] = field(default_factory=list)


_HASH_LINE = {".py", ".pyi", ".rb", ".sh", ".bash", ".zsh", ".yml", ".yaml", ".toml"}
_C_LIKE = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".kts", ".less", ".php", ".rs", ".sass", ".scala", ".scss",
    ".swift", ".ts", ".tsx",
}
_SQL = {".sql"}
_HTML = {".htm", ".html", ".md", ".mdx", ".xml"}


def _comment_syntax(suffix: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    suffix = suffix.lower()
    if suffix in _HASH_LINE:
        return ("#",), ()
    if suffix in _C_LIKE:
        lines = ("//", "#") if suffix == ".php" else ("//",)
        return lines, (("/*", "*/"),)
    if suffix in _SQL:
        return ("--",), (("/*", "*/"),)
    if suffix in _HTML:
        return (), (("<!--", "-->"),)
    return (), ()


def _is_rust_lifetime(text: str, index: int) -> bool:
    """Return whether an apostrophe starts a Rust lifetime or loop label."""
    next_index = index + 1
    if next_index >= len(text):
        return False
    first = text[next_index]
    if first != "_" and not first.isalpha():
        return False
    end = next_index + 1
    while end < len(text) and (text[end] == "_" or text[end].isalnum()):
        end += 1
    return end >= len(text) or text[end] != "'"


def strip_comments(text: str, suffix: str) -> str:
    """Mask comments while preserving line/column offsets and string literals."""
    line_tokens, block_tokens = _comment_syntax(suffix)
    if not line_tokens and not block_tokens:
        return text

    output = list(text)
    index = 0
    string_end: str | None = None
    escaped = False
    length = len(text)
    while index < length:
        if string_end is not None:
            if escaped:
                escaped = False
                index += 1
                continue
            if text[index] == "\\" and string_end != "`":
                escaped = True
                index += 1
                continue
            if text.startswith(string_end, index):
                index += len(string_end)
                string_end = None
                continue
            index += 1
            continue

        triple = next(
            (token for token in ('"""', "'''") if text.startswith(token, index)),
            None,
        )
        if triple is not None:
            string_end = triple
            index += len(triple)
            continue
        if text[index] == "'" and suffix.lower() == ".rs" and _is_rust_lifetime(
            text, index
        ):
            index += 1
            continue
        if text[index] in {'"', "'", "`"}:
            string_end = text[index]
            index += 1
            continue

        line_token = next(
            (token for token in line_tokens if text.startswith(token, index)), None
        )
        if line_token is not None:
            end = text.find("\n", index)
            end = length if end < 0 else end
            for position in range(index, end):
                output[position] = " "
            index = end
            continue

        block = next(
            (
                (start, end)
                for start, end in block_tokens
                if text.startswith(start, index)
            ),
            None,
        )
        if block is not None:
            end_index = text.find(block[1], index + len(block[0]))
            end_index = length if end_index < 0 else end_index + len(block[1])
            for position in range(index, end_index):
                if output[position] != "\n":
                    output[position] = " "
            index = end_index
            continue
        index += 1
    return "".join(output)


def _matches_exclude(relative: str, patterns: list[str]) -> bool:
    parts = PurePosixPath(relative).parts
    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/").strip("/")
        if not pattern:
            continue
        if not any(char in pattern for char in "*?[]") and "/" not in pattern:
            if pattern in parts:
                return True
        if fnmatchcase(relative, pattern) or fnmatchcase(relative, f"{pattern}/**"):
            return True
        if fnmatchcase(relative, f"**/{pattern}") or fnmatchcase(
            relative, f"**/{pattern}/**"
        ):
            return True
    return False


def _is_test_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    lowered_parts = [part.lower() for part in path.parts[:-1]]
    if any(part in {"test", "tests", "__tests__"} for part in lowered_parts):
        return True
    name = path.name.lower()
    stem = path.stem.lower()
    return (
        name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
        or stem.startswith("test_")
        or stem.endswith("_test")
    )


def iter_files(
    repo: Path,
    globs: list[str],
    excludes: list[str] | None,
    exclude_tests: bool,
    global_exclude: list[str] | None,
    naive: bool,
) -> Iterator[tuple[Path, str]]:
    """Yield unique, sorted files confined to ``repo`` and honoring path patterns."""
    repo = repo.resolve()
    found: dict[str, Path] = {}
    for pattern in globs:
        for candidate in repo.glob(pattern):
            resolved = candidate.resolve()
            try:
                relative = resolved.relative_to(repo).as_posix()
            except ValueError:
                continue
            if not resolved.is_file() or _matches_exclude(
                relative, list(global_exclude or [])
            ):
                continue
            if not naive and (
                (exclude_tests and _is_test_path(relative))
                or _matches_exclude(relative, list(excludes or []))
            ):
                continue
            found[relative] = resolved
    yield from ((found[relative], relative) for relative in sorted(found))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _repo_target(repo: Path, relative: str) -> Path | None:
    target = (repo / relative).resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        return None
    return target


def _position(text: str, offset: int) -> tuple[int, int, str]:
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    return line, offset - line_start + 1, text[line_start:line_end].strip()


def _fingerprint(relative: str, matched: str, occurrence: int) -> str:
    digest = sha256(matched.strip().encode("utf-8")).hexdigest()[:16]
    return f"{relative}::{digest}::{occurrence}"


def _location(
    relative: str,
    text: str,
    match: re.Match[str],
    *,
    message: str,
    key: str,
) -> Evidence:
    line, column, snippet = _position(text, match.start())
    return Evidence(relative, line, column, message, snippet, key)


def check_file_exists(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    relative = spec["path"]
    target = _repo_target(repo, relative)
    if target is None:
        return CheckResult(
            UNVERIFIABLE, 0.0, f"{relative} resolves outside the repository"
        )
    ok = target.is_file()
    location = Evidence(relative, message="required file is missing", key=relative)
    return CheckResult(
        VERIFIED if ok else DRIFTED,
        1.0,
        f"{relative} {'exists' if ok else 'is missing'}",
        [] if ok else [relative],
        [] if ok else [location],
    )


def check_dir_exists(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    relative = spec["path"]
    target = _repo_target(repo, relative)
    if target is None:
        return CheckResult(
            UNVERIFIABLE, 0.0, f"{relative} resolves outside the repository"
        )
    ok = target.is_dir()
    location = Evidence(relative, message="required directory is missing", key=relative)
    return CheckResult(
        VERIFIED if ok else DRIFTED,
        1.0,
        f"{relative}/ {'exists' if ok else 'is missing'}",
        [] if ok else [relative],
        [] if ok else [location],
    )


def check_contains(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    relative = spec["file"]
    path = _repo_target(repo, relative)
    if path is None:
        return CheckResult(
            UNVERIFIABLE, 0.0, f"{relative} resolves outside the repository"
        )
    if not path.is_file():
        return CheckResult(UNVERIFIABLE, 0.0, f"{relative} is not a file")
    text = _read(path)
    searchable = (
        strip_comments(text, path.suffix)
        if spec.get("comment_aware", False) and not naive
        else text
    )
    match = re.search(spec["pattern"], searchable, re.MULTILINE)
    if match:
        line, column, _ = _position(text, match.start())
        return CheckResult(VERIFIED, 1.0, f"matched at {relative}:{line}:{column}")
    location = Evidence(relative, message="required pattern was not found", key=relative)
    return CheckResult(
        DRIFTED,
        1.0,
        f"pattern not found in {relative}",
        [relative],
        [location],
    )


def check_constant(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    pattern = re.compile(spec["pattern"], re.MULTILINE)
    matches: list[tuple[str, str, re.Match[str]]] = []
    for path, relative in iter_files(
        repo,
        spec["globs"],
        spec.get("exclude"),
        spec.get("exclude_tests", True),
        ge,
        naive,
    ):
        text = _read(path)
        searchable = (
            strip_comments(text, path.suffix)
            if spec.get("comment_aware", True) and not naive
            else text
        )
        matches.extend((relative, text, match) for match in pattern.finditer(searchable))
    if not matches:
        return CheckResult(UNVERIFIABLE, 0.0, "constant pattern was not found")

    locations: list[Evidence] = []
    counters: dict[tuple[str, str], int] = {}
    expected = str(spec["expect"])
    for relative, text, match in matches:
        actual = match.group(1)
        if actual == expected:
            continue
        digest = sha256(match.group(0).strip().encode("utf-8")).hexdigest()[:16]
        counter_key = (relative, digest)
        counters[counter_key] = counters.get(counter_key, 0) + 1
        key = _fingerprint(relative, match.group(0), counters[counter_key])
        line, column, snippet = _position(text, match.start(1))
        locations.append(
            Evidence(
                relative,
                line,
                column,
                f"found {actual!r}; expected {expected!r}",
                snippet,
                key,
            )
        )
    if locations:
        return CheckResult(
            DRIFTED,
            1.0,
            f"{len(locations)} value(s) differ from {expected!r}",
            [location.key for location in locations],
            locations,
        )
    return CheckResult(
        VERIFIED, 1.0, f"all {len(matches)} located value(s) equal {expected!r}"
    )


def check_dependency(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    relative = spec["manifest"]
    path = _repo_target(repo, relative)
    if path is None:
        return CheckResult(
            UNVERIFIABLE, 0.0, f"{relative} resolves outside the repository"
        )
    if not path.is_file():
        return CheckResult(UNVERIFIABLE, 0.0, f"{relative} is not a file")
    text = _read(path)
    match = re.search(spec["pattern"], text, re.MULTILINE)
    if match:
        line, column, _ = _position(text, match.start())
        return CheckResult(VERIFIED, 1.0, f"declared at {relative}:{line}:{column}")
    location = Evidence(relative, message="dependency pattern was not found", key=relative)
    return CheckResult(
        DRIFTED,
        1.0,
        f"dependency pattern not found in {relative}",
        [relative],
        [location],
    )


def check_naming(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    name_pattern = re.compile(spec["name_regex"])
    files = list(
        iter_files(
            repo,
            spec["globs"],
            spec.get("exclude"),
            spec.get("exclude_tests", False),
            ge,
            naive,
        )
    )
    if not files:
        return CheckResult(UNVERIFIABLE, 0.0, "no files matched the configured globs")
    locations = []
    for _, relative in files:
        stem = Path(relative).stem
        if name_pattern.fullmatch(stem) is None:
            locations.append(
                Evidence(
                    relative,
                    message=f"file stem {stem!r} does not match naming rule",
                    key=relative,
                )
            )
    if locations:
        return CheckResult(
            DRIFTED,
            1.0,
            f"{len(locations)} file name violation(s)",
            [item.key for item in locations],
            locations,
        )
    return CheckResult(VERIFIED, 1.0, f"all {len(files)} file names conform")


def check_forbidden(repo: Path, spec: dict[str, Any], ge: list[str], naive: bool) -> CheckResult:
    """Find every non-comment match and assign a stable per-file occurrence key."""
    pattern = re.compile(spec["pattern"], re.MULTILINE)
    files = list(
        iter_files(
            repo,
            spec["globs"],
            spec.get("exclude"),
            spec.get("exclude_tests", True),
            ge,
            naive,
        )
    )
    if not files:
        return CheckResult(UNVERIFIABLE, 0.0, "no files matched the configured globs")

    locations: list[Evidence] = []
    counters: dict[tuple[str, str], int] = {}
    for path, relative in files:
        text = _read(path)
        searchable = (
            strip_comments(text, path.suffix)
            if spec.get("comment_aware", True) and not naive
            else text
        )
        for match in pattern.finditer(searchable):
            digest = sha256(match.group(0).strip().encode("utf-8")).hexdigest()[:16]
            counter_key = (relative, digest)
            counters[counter_key] = counters.get(counter_key, 0) + 1
            key = _fingerprint(relative, match.group(0), counters[counter_key])
            locations.append(
                _location(
                    relative,
                    text,
                    match,
                    message=f"forbidden pattern matched {match.group(0)!r}",
                    key=key,
                )
            )
    if locations:
        return CheckResult(
            DRIFTED,
            1.0,
            f"{len(locations)} forbidden occurrence(s) in "
            f"{len({item.path for item in locations})} file(s)",
            [item.key for item in locations],
            locations,
        )
    return CheckResult(VERIFIED, 1.0, f"no forbidden usage in {len(files)} file(s)")


CHECKERS = {
    "file_exists": check_file_exists,
    "dir_exists": check_dir_exists,
    "contains": check_contains,
    "constant": check_constant,
    "dependency": check_dependency,
    "naming": check_naming,
    "forbidden": check_forbidden,
}
