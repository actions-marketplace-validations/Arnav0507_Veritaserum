"""The checkers — the precision-critical core.

Each checker returns a CheckResult with a verdict and a list of *stable violation
keys* (used by the baseline to gate only NEW drift, robust to line-number churn).

The precision insight (validated in Spike 2): naive grep over-flags. `forbidden`
must check ACTUAL runtime usage — excluding tests/infra/bootstrap and comments —
not raw text hits. Naive precision ~89%; refined ~100%.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

from . import VERIFIED, DRIFTED, UNVERIFIABLE


@dataclass
class CheckResult:
    verdict: str
    confidence: float
    evidence: str
    violations: list[str] = field(default_factory=list)  # stable keys


# ---- comment stripping (reduces false positives on code patterns) ----
_LINE = {".py": ["#"], ".rb": ["#"], ".sh": ["#"], ".yml": ["#"], ".yaml": ["#"]}
_CLIKE = {".go", ".ts", ".tsx", ".js", ".jsx", ".java", ".c", ".h", ".cc",
          ".cpp", ".hpp", ".rs", ".cs", ".kt", ".swift", ".scala", ".php"}


def strip_comments(text: str, suffix: str) -> str:
    """Best-effort comment removal. Does not parse strings (documented P0 limit)."""
    if suffix in _CLIKE:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)  # block
        text = re.sub(r"//[^\n]*", "", text)               # line
        return text
    for tok in _LINE.get(suffix, []):
        text = re.sub(rf"{re.escape(tok)}[^\n]*", "", text)
    return text


# ---- file iteration with excludes ----
def iter_files(repo: Path, globs, excludes, exclude_tests, global_exclude, naive):
    excludes = [] if naive else list(excludes or [])
    global_exclude = list(global_exclude or [])
    for g in globs:
        for p in repo.glob(g):
            if not p.is_file():
                continue
            rel = p.relative_to(repo).as_posix()
            if any(x in rel for x in global_exclude):
                continue
            if not naive:
                if exclude_tests and (
                    rel.endswith("_test.go") or ".test." in rel
                    or ".spec." in rel or "/test/" in rel or rel.startswith("test/")
                ):
                    continue
                if any(x in rel for x in excludes):
                    continue
            yield p, rel


# ---- individual checkers ----
def check_file_exists(repo, spec, ge, naive):
    ok = (repo / spec["path"]).exists()
    return CheckResult(VERIFIED if ok else DRIFTED, 1.0,
                       spec["path"] + (" exists" if ok else " MISSING"),
                       [] if ok else [spec["path"]])


def check_dir_exists(repo, spec, ge, naive):
    ok = (repo / spec["path"]).is_dir()
    return CheckResult(VERIFIED if ok else DRIFTED, 1.0,
                       spec["path"] + ("/ exists" if ok else "/ MISSING"),
                       [] if ok else [spec["path"]])


def check_contains(repo, spec, ge, naive):
    f = repo / spec["file"]
    if not f.exists():
        return CheckResult(UNVERIFIABLE, 0.5, f"{spec['file']} not found")
    pat = re.compile(spec["pattern"])
    for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
        if pat.search(line):
            return CheckResult(VERIFIED, 1.0, f"{spec['file']}:{i}")
    return CheckResult(DRIFTED, 0.9, f"pattern not found in {spec['file']}", [spec["file"]])


def check_constant(repo, spec, ge, naive):
    pat = re.compile(spec["pattern"])
    globs = spec.get("globs", ["**/*.go"])
    for _, rel in iter_files(repo, globs, spec.get("exclude"), True, ge, naive):
        m = pat.search((repo / rel).read_text(errors="ignore"))
        if m:
            val = m.group(1)
            if val == str(spec["expect"]):
                return CheckResult(VERIFIED, 1.0, f"{rel}: {val}")
            return CheckResult(DRIFTED, 0.95,
                               f"{rel}: found {val}, claim says {spec['expect']}", [rel])
    return CheckResult(UNVERIFIABLE, 0.5, "constant not located")


def check_dependency(repo, spec, ge, naive):
    f = repo / spec["manifest"]
    if not f.exists():
        return CheckResult(UNVERIFIABLE, 0.5, f"{spec['manifest']} missing")
    ok = re.search(spec["pattern"], f.read_text(errors="ignore")) is not None
    return CheckResult(VERIFIED if ok else DRIFTED, 0.95, spec["manifest"],
                       [] if ok else [spec["manifest"]])


def check_naming(repo, spec, ge, naive):
    name_re = re.compile(spec["name_regex"])
    violations = []
    for _, rel in iter_files(repo, spec["globs"], spec.get("exclude"),
                             spec.get("exclude_tests", False), ge, naive):
        stem = re.sub(r"\..*$", "", Path(rel).name)
        if not name_re.match(stem):
            violations.append(rel)
    if violations:
        return CheckResult(DRIFTED, 0.9,
                           f"{len(violations)} violations e.g. {violations[:3]}", violations)
    return CheckResult(VERIFIED, 0.85, "all names conform")


def check_forbidden(repo, spec, ge, naive):
    """'never X' rule. DRIFTED if any runtime (non-excluded) file really matches."""
    pat = re.compile(spec["pattern"])
    comment_aware = spec.get("comment_aware", True) and not naive
    hits = []
    for _, rel in iter_files(repo, spec.get("globs", ["**/*.go"]), spec.get("exclude"),
                             spec.get("exclude_tests", True), ge, naive):
        text = (repo / rel).read_text(errors="ignore")
        if comment_aware:
            text = strip_comments(text, Path(rel).suffix)
        if pat.search(text):
            hits.append(rel)
    if hits:
        return CheckResult(DRIFTED, 0.9, f"{len(hits)} hits e.g. {hits[:3]}", hits)
    return CheckResult(VERIFIED, 0.85, "no forbidden usage found")


CHECKERS = {
    "file_exists": check_file_exists, "dir_exists": check_dir_exists,
    "contains": check_contains, "constant": check_constant,
    "dependency": check_dependency, "naming": check_naming,
    "forbidden": check_forbidden,
}
