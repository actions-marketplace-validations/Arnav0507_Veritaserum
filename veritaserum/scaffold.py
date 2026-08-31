"""`veritaserum init` — deterministically scaffold a starter claim set.

No LLM. Only emits claims for facts we can detect with certainty (existing context
files, dependencies from manifests, top-level source dirs) so the generated set is
green on first run. Semantic/prose claims are intentionally NOT generated here.
"""
from __future__ import annotations
from pathlib import Path
import json
import re

CONTEXT_FILES = ["AGENTS.md", ".github/copilot-instructions.md", "CLAUDE.md",
                 ".cursorrules", ".cursor/rules"]


def generate_claims(repo: Path) -> list[dict]:
    repo = repo.resolve()
    claims: list[dict] = []
    # 1) existing context files
    for cf in CONTEXT_FILES:
        if (repo / cf).exists():
            claims.append({
                "id": f"ctx-{_slug(cf)}-exists",
                "text": f"Context file {cf} exists.",
                "type": "file_exists", "severity": "warn",
                "spec": {"path": cf},
            })
    # 2) node ecosystem
    pj = repo / "package.json"
    if _inside_repo(repo, pj) and pj.is_file():
        data = _safe_json(pj)
        pm = data.get("packageManager")
        if isinstance(pm, str) and "@" in pm:
            claims.append({
                "id": "pkg-manager-pin",
                "text": f"Package manager pinned to {pm}.",
                "type": "constant", "severity": "warn",
                "spec": {"globs": ["package.json"],
                         "pattern": r'"packageManager"\s*:\s*"([^"]+)"',
                         "expect": pm},
            })
        for dep in _top_deps(data, 3):
            claims.append({
                "id": f"dep-{_slug(dep)}",
                "text": f"Depends on {dep}.",
                "type": "dependency", "severity": "warn",
                "spec": {"manifest": "package.json",
                         "pattern": rf'"{re.escape(dep)}"\s*:'},
            })
    # 3) go ecosystem
    gomod = repo / "go.mod"
    if _inside_repo(repo, gomod) and gomod.is_file():
        for dep in _go_deps(gomod, 3):
            claims.append({
                "id": f"gomod-{_slug(dep)}",
                "text": f"Go module requires {dep}.",
                "type": "dependency", "severity": "warn",
                "spec": {"manifest": "go.mod", "pattern": re.escape(dep)},
            })
    # 4) notable top-level source dirs
    for d in _source_dirs(repo, 4):
        claims.append({
            "id": f"dir-{_slug(d)}",
            "text": f"Source directory {d}/ exists.",
            "type": "dir_exists", "severity": "warn",
            "spec": {"path": d},
        })
    return claims


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _safe_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _top_deps(pkg: dict, n: int) -> list[str]:
    deps = list((pkg.get("dependencies") or {}).keys())
    return deps[:n]


def _go_deps(gomod: Path, n: int) -> list[str]:
    out = []
    for line in gomod.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*([\w.\-/]+)\s+v\d", line)
        if m and "// indirect" not in line:
            out.append(m.group(1))
        if len(out) >= n:
            break
    return out


def _source_dirs(repo: Path, n: int) -> list[str]:
    skip = {"node_modules", ".git", "dist", "build", "vendor", ".venv",
            "__pycache__", ".github", "docs", "test", "tests"}
    dirs = [p.name for p in sorted(repo.iterdir())
            if p.is_dir() and _inside_repo(repo, p)
            and p.name not in skip and not p.name.startswith(".")]
    return dirs[:n]


def _inside_repo(repo: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(repo)
    except ValueError:
        return False
    return True
