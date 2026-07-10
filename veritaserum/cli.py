"""Veritaserum CLI: `check` and `init`."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import yaml

from . import __version__
from .config import load_config, DEFAULT_GLOBAL_EXCLUDE
from .loader import load_claims
from .schema import SchemaError
from .baseline import load_baseline, write_baseline
from .runner import run, summarize
from . import reporters
from . import scaffold

EXIT_OK, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def _cmd_check(a) -> int:
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"error: repo not found: {repo}", file=sys.stderr)
        return EXIT_ERROR
    try:
        cfg = load_config(repo, a.config)
        claims = load_claims(repo, cfg.claims)
    except (SchemaError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not claims:
        print("error: no claims found (check 'claims' globs in .veritaserum.yml)",
              file=sys.stderr)
        return EXIT_ERROR

    base = {} if (a.no_baseline or a.update_baseline) else load_baseline(repo, cfg.baseline)
    rows = run(repo, claims, global_exclude=cfg.global_exclude, baseline=base,
               severity_gate=cfg.severity_gate, naive=a.naive)
    summary = summarize(rows)

    if a.update_baseline:
        p = write_baseline(repo, cfg.baseline, rows)
        print(f"wrote baseline: {p} ({len(rows)} claims, "
              f"{sum(1 for r in rows if r['all_violations'])} with violations)")
        return EXIT_OK

    out = reporters.render(a.format, rows, summary, context_files=cfg.context_files)
    if a.output:
        Path(a.output).write_text(out + "\n", encoding="utf-8")
        print(f"wrote {a.format} report: {a.output}")
    else:
        print(out)

    if a.dry_run or a.warn_only:
        return EXIT_OK
    return EXIT_DRIFT if summary["failed"] else EXIT_OK


def _cmd_init(a) -> int:
    repo = Path(a.repo).resolve()
    claims = scaffold.generate_claims(repo)
    if not claims:
        print("init: nothing detectable to scaffold (no manifests/context files found).",
              file=sys.stderr)
        return EXIT_ERROR
    claims_dir = repo / "claims"
    claims_dir.mkdir(exist_ok=True)
    claims_file = claims_dir / "generated.yml"
    cfg_file = repo / ".veritaserum.yml"
    if (claims_file.exists() or cfg_file.exists()) and not a.force:
        print(f"init: {claims_file.name}/{cfg_file.name} already exist (use --force).",
              file=sys.stderr)
        return EXIT_ERROR

    doc = {"schema_version": 1, "claims": claims}
    claims_file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    cfg = {
        "schema_version": 1,
        "context_files": [c["spec"]["path"] for c in claims if c["type"] == "file_exists"],
        "claims": ["claims/**/*.yml", "claims/**/*.yaml", "claims/**/*.json"],
        "global_exclude": DEFAULT_GLOBAL_EXCLUDE,
        "severity_gate": "error",
        "baseline": ".veritaserum-baseline.json",
    }
    cfg_file.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"init: wrote {claims_file.relative_to(repo)} ({len(claims)} claims) "
          f"and {cfg_file.name}")
    print("next: review the generated claims, then run `veritaserum check --repo .`")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="veritaserum",
                                description="Testable AI instructions — verify context files against code.")
    p.add_argument("--version", action="version", version=f"veritaserum {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="verify claims against the repo")
    c.add_argument("--repo", default=".")
    c.add_argument("--config", default=None, help="path to .veritaserum.yml")
    c.add_argument("--format", choices=["human", "json", "sarif"], default="human")
    c.add_argument("--output", default=None, help="write report to file instead of stdout")
    c.add_argument("--naive", action="store_true", help="disable excludes/comment-awareness (eval only)")
    c.add_argument("--dry-run", action="store_true", help="report but never fail")
    c.add_argument("--warn-only", action="store_true", help="report drift but exit 0")
    c.add_argument("--no-baseline", action="store_true", help="ignore baseline; gate on all drift")
    c.add_argument("--update-baseline", action="store_true", help="record current violations as baseline")
    c.set_defaults(func=_cmd_check)

    i = sub.add_parser("init", help="scaffold a starter claim set (deterministic)")
    i.add_argument("--repo", default=".")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=_cmd_init)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rc = args.func(args)
    if argv is None:
        sys.exit(rc)
    return rc


if __name__ == "__main__":
    main()
