"""Map git diffs to claims and flag potentially stale context."""
from __future__ import annotations

import subprocess
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from .schema import Claim

_DIFF_FILTER = "--diff-filter=ACMRD"


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def parse_source_path(source: str | None) -> str | None:
    if not source:
        return None
    return _normalize_path(source.split("#", 1)[0])


def _path_matches_glob(repo: Path, relative: str, pattern: str) -> bool:
    """Match using the same ``Path.glob`` semantics as the checkers."""
    normalized = _normalize_path(relative)
    glob = _normalize_path(pattern)
    repo = repo.resolve()
    if PurePosixPath(normalized).match(glob) or fnmatchcase(normalized, glob):
        return True
    for candidate in repo.glob(glob):
        if not candidate.is_file():
            continue
        try:
            if candidate.resolve().relative_to(repo).as_posix() == normalized:
                return True
        except ValueError:
            continue
    return False


def _under_prefix(relative: str, prefix: str) -> bool:
    normalized = _normalize_path(relative)
    root = _normalize_path(prefix)
    return normalized == root or normalized.startswith(f"{root}/")


def claim_touchpoints(claim: Claim) -> dict[str, set[str]]:
    """Return structured paths and globs a claim depends on."""
    spec = claim.spec
    paths: set[str] = set()
    globs: set[str] = set()
    if claim.type in {"file_exists", "dir_exists"}:
        paths.add(spec["path"])
    elif claim.type == "contains":
        paths.add(spec["file"])
    elif claim.type == "dependency":
        paths.add(spec["manifest"])
    elif claim.type in {"constant", "naming", "forbidden"}:
        globs.update(spec["globs"])
    source_path = parse_source_path(claim.source)
    if source_path:
        paths.add(source_path)
    return {"paths": paths, "globs": globs}


def _path_change_reason(claim: Claim, normalized: str, path: str) -> str:
    path_norm = _normalize_path(path)
    source_path = parse_source_path(claim.source)
    if source_path == normalized:
        return f"context file {normalized} was edited"
    if claim.type == "dependency" and path_norm == normalized:
        return f"dependency manifest {normalized} was edited"
    if claim.type == "contains" and path_norm == normalized:
        return f"checked file {normalized} was edited"
    if claim.type == "file_exists" and path_norm == normalized:
        return f"required file {normalized} was edited"
    if claim.type == "dir_exists" and _under_prefix(normalized, path_norm):
        return f"changed file {normalized} is under directory {path_norm}/"
    return f"claim path {normalized} was edited"


def claim_affected_by(
    claim: Claim, changed_files: set[str], *, repo: Path
) -> list[str]:
    """Return human-readable reasons when *claim* may be invalidated by *changed_files*."""
    if not changed_files:
        return []
    touchpoints = claim_touchpoints(claim)
    reasons: list[str] = []
    for changed in sorted(changed_files):
        normalized = _normalize_path(changed)
        for path in touchpoints["paths"]:
            path_norm = _normalize_path(path)
            if claim.type == "dir_exists":
                if _under_prefix(normalized, path_norm):
                    reasons.append(_path_change_reason(claim, normalized, path))
            elif normalized == path_norm:
                reasons.append(_path_change_reason(claim, normalized, path))
        for pattern in touchpoints["globs"]:
            if _path_matches_glob(repo, normalized, pattern):
                reasons.append(
                    f"changed file {normalized} matches spec.globs {pattern!r}"
                )
    seen: set[str] = set()
    unique: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique


def affected_claims(
    claims: list[Claim], changed_files: list[str], *, repo: Path
) -> list[tuple[Claim, list[str]]]:
    changed = {_normalize_path(path) for path in changed_files if path.strip()}
    output: list[tuple[Claim, list[str]]] = []
    for claim in claims:
        reasons = claim_affected_by(claim, changed, repo=repo)
        if reasons:
            output.append((claim, reasons))
    return output


def git_changed_files(repo: Path, base: str, head: str) -> list[str]:
    """List repository-relative paths changed between *base* and *head*."""
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise RuntimeError(f"not a git repository: {repo}")

    if head.upper() == "WORKTREE":
        command = [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            _DIFF_FILTER,
            base,
        ]
    else:
        command = [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            _DIFF_FILTER,
            f"{base}...{head}",
        ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not run git: {exc}") from exc
    if completed.returncode not in {0, 1}:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"git diff failed ({completed.returncode}): {detail or 'unknown error'}"
        )
    return [
        _normalize_path(line)
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def _git_rev(repo: Path, ref: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ref],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not run git: {exc}") from exc
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def resolve_base_head(repo: Path, base: str | None, head: str | None) -> tuple[str, str]:
    repo = repo.resolve()
    resolved_head = head or "HEAD"
    if base:
        return base, resolved_head
    head_sha = _git_rev(repo, resolved_head)
    for candidate in ("@{upstream}", "origin/main", "origin/master", "main", "master"):
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo), "merge-base", resolved_head, candidate],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError(f"could not run git: {exc}") from exc
        if completed.returncode != 0 or not completed.stdout.strip():
            continue
        merge_base = completed.stdout.strip()
        if head_sha is None or merge_base != head_sha:
            return merge_base, resolved_head
    return "HEAD~1", resolved_head


def annotate_affected_rows(
    rows: list[dict[str, Any]],
    reasons_by_id: dict[str, list[str]],
    *,
    changed_files: list[str],
) -> list[dict[str, Any]]:
    changed_context = set(changed_files)
    annotated: list[dict[str, Any]] = []
    for row in rows:
        reasons = reasons_by_id.get(row["id"], [])
        source_path = parse_source_path(row.get("source"))
        context_edited = bool(source_path and source_path in changed_context)
        stale = bool(
            reasons
            and (
                row["gating"]
                or row["status"] == "drifted"
                or context_edited
            )
        )
        annotated.append(
            {
                **row,
                "affected": True,
                "affected_reasons": reasons,
                "context_edited": context_edited,
                "stale": stale,
            }
        )
    return annotated


def summarize_affected(
    rows: list[dict[str, Any]],
    *,
    changed_files: list[str],
    total_claims: int,
) -> dict[str, Any]:
    base = {
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "affected_claims": len(rows),
        "total_claims": total_claims,
        "stale": sum(bool(row.get("stale")) for row in rows),
        "gating_stale": sum(
            bool(row.get("stale") and row.get("gating")) for row in rows
        ),
    }
    base.update(
        {
            "pass": sum(row["status"] == "pass" for row in rows),
            "drifted": sum(row["status"] == "drifted" for row in rows),
            "baselined": sum(row["status"] == "baselined" for row in rows),
            "unverifiable": sum(row["status"] == "unverifiable" for row in rows),
            "gating": sum(bool(row["gating"]) for row in rows),
            "failed": any(bool(row.get("stale") and row.get("gating")) for row in rows),
        }
    )
    return base
