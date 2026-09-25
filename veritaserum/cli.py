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
from . import suggest as suggest_module
from . import affected as affected_module

EXIT_OK, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def _cmd_check(a) -> int:
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"error: repo not found: {repo}", file=sys.stderr)
        return EXIT_ERROR
    try:
        cfg = load_config(repo, a.config)
        claims = load_claims(repo, cfg.claims)
        base = (
            {}
            if (a.no_baseline or a.update_baseline)
            else load_baseline(repo, cfg.baseline)
        )
    except (SchemaError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not claims:
        print("error: no claims found (check 'claims' globs in .veritaserum.yml)",
              file=sys.stderr)
        return EXIT_ERROR

    try:
        rows = run(repo, claims, global_exclude=cfg.global_exclude, baseline=base,
                   severity_gate=cfg.severity_gate, naive=a.naive)
    except (OSError, UnicodeError, ValueError) as e:
        print(f"error: could not check repository: {e}", file=sys.stderr)
        return EXIT_ERROR
    summary = summarize(rows)

    if a.update_baseline:
        try:
            p = write_baseline(repo, cfg.baseline, rows)
        except OSError as e:
            print(f"error: could not write baseline: {e}", file=sys.stderr)
            return EXIT_ERROR
        print(f"wrote baseline: {p} ({len(rows)} claims, "
              f"{sum(1 for r in rows if r['all_violations'])} with violations)")
        return EXIT_OK

    try:
        if a.sarif_output:
            Path(a.sarif_output).write_text(
                reporters.sarif(rows, summary, context_files=cfg.context_files) + "\n",
                encoding="utf-8",
            )
        out = reporters.render(a.format, rows, summary, context_files=cfg.context_files)
        if a.output:
            Path(a.output).write_text(out + "\n", encoding="utf-8")
            print(f"wrote {a.format} report: {a.output}")
        else:
            print(out)
    except OSError as e:
        print(f"error: could not write report: {e}", file=sys.stderr)
        return EXIT_ERROR

    if a.dry_run or a.warn_only:
        return EXIT_OK
    return EXIT_DRIFT if summary["failed"] else EXIT_OK


def _cmd_suggest(a) -> int:
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"suggest: repo not found: {repo}", file=sys.stderr)
        return EXIT_ERROR
    try:
        result = suggest_module.suggest(
            repo,
            context_files=a.context or None,
            input_path=Path(a.input).resolve() if a.input else None,
            max_claims=a.max_claims,
        )
    except (FileNotFoundError, RuntimeError, SchemaError, OSError, UnicodeError) as e:
        print(f"suggest: {e}", file=sys.stderr)
        return EXIT_ERROR

    for message in result.rejected:
        print(f"suggest: rejected {message}", file=sys.stderr)

    if not result.accepted:
        print("suggest: no valid claims produced", file=sys.stderr)
        return EXIT_ERROR

    document = suggest_module.claims_document(result.accepted)
    rendered = yaml.safe_dump(document, sort_keys=False)
    if a.output:
        try:
            output_path = Path(a.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except OSError as e:
            print(f"suggest: could not write output: {e}", file=sys.stderr)
            return EXIT_ERROR
        print(
            f"suggest: wrote {len(result.accepted)} claim(s) to {a.output} "
            f"({len(result.rejected)} rejected)"
        )
    else:
        print(rendered, end="")
        if result.rejected:
            print(
                f"suggest: {len(result.rejected)} proposal(s) rejected "
                "(see stderr)",
                file=sys.stderr,
            )
    print(
        "suggest: review the output before committing; verification stays deterministic",
        file=sys.stderr,
    )
    return EXIT_OK


def _cmd_affected(a) -> int:
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"affected: repo not found: {repo}", file=sys.stderr)
        return EXIT_ERROR
    try:
        cfg = load_config(repo, a.config)
        claims = load_claims(repo, cfg.claims)
        base = {} if a.no_baseline else load_baseline(repo, cfg.baseline)
        base_ref, head_ref = affected_module.resolve_base_head(repo, a.base, a.head)
        changed_files = affected_module.git_changed_files(repo, base_ref, head_ref)
    except (SchemaError, OSError, ValueError, RuntimeError) as e:
        print(f"affected: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not claims:
        print("affected: no claims found (check 'claims' globs in .veritaserum.yml)",
              file=sys.stderr)
        return EXIT_ERROR

    if not changed_files:
        print(f"affected: no changed files between {base_ref} and {head_ref}")
        return EXIT_OK

    matched = affected_module.affected_claims(claims, changed_files, repo=repo)
    if not matched:
        print(
            f"affected: {len(changed_files)} changed file(s), "
            "but no claims touch them"
        )
        for path in changed_files:
            print(f"  - {path}")
        return EXIT_OK

    reasons_by_id = {claim.id: reasons for claim, reasons in matched}
    selected = [claim for claim, _ in matched]
    try:
        rows = run(
            repo,
            selected,
            global_exclude=cfg.global_exclude,
            baseline=base,
            severity_gate=cfg.severity_gate,
            naive=a.naive,
        )
    except (OSError, UnicodeError, ValueError) as e:
        print(f"affected: could not check repository: {e}", file=sys.stderr)
        return EXIT_ERROR

    rows = affected_module.annotate_affected_rows(
        rows, reasons_by_id, changed_files=changed_files
    )
    summary = affected_module.summarize_affected(
        rows, changed_files=changed_files, total_claims=len(claims)
    )

    try:
        if a.sarif_output:
            Path(a.sarif_output).write_text(
                reporters.sarif(rows, summary, context_files=cfg.context_files) + "\n",
                encoding="utf-8",
            )
        out = reporters.render(
            a.format,
            rows,
            summary,
            context_files=cfg.context_files,
            affected=True,
        )
        if a.output:
            Path(a.output).write_text(out + "\n", encoding="utf-8")
            print(f"wrote {a.format} report: {a.output}")
        else:
            print(out)
    except OSError as e:
        print(f"affected: could not write report: {e}", file=sys.stderr)
        return EXIT_ERROR

    if a.dry_run or a.warn_only:
        return EXIT_OK
    return EXIT_DRIFT if summary["failed"] else EXIT_OK


def _cmd_init(a) -> int:
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"init: repo not found: {repo}", file=sys.stderr)
        return EXIT_ERROR
    try:
        claims = scaffold.generate_claims(repo)
    except (OSError, UnicodeError, ValueError) as e:
        print(f"init: could not inspect repository: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not claims:
        print("init: nothing detectable to scaffold (no manifests/context files found).",
              file=sys.stderr)
        return EXIT_ERROR
    claims_dir = repo / "claims"
    claims_file = claims_dir / "generated.yml"
    cfg_file = repo / ".veritaserum.yml"
    if (claims_file.exists() or cfg_file.exists()) and not a.force:
        print(f"init: {claims_file.name}/{cfg_file.name} already exist (use --force).",
              file=sys.stderr)
        return EXIT_ERROR

    try:
        claims_dir.mkdir(exist_ok=True)
        doc = {"schema_version": 1, "claims": claims}
        claims_file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        cfg = {
            "schema_version": 1,
            "context_files": [
                c["spec"]["path"] for c in claims if c["type"] == "file_exists"
            ],
            "claims": ["claims/**/*.yml", "claims/**/*.yaml", "claims/**/*.json"],
            "global_exclude": DEFAULT_GLOBAL_EXCLUDE,
            "severity_gate": "error",
            "baseline": ".veritaserum-baseline.json",
        }
        cfg_file.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    except OSError as e:
        print(f"init: could not write scaffold: {e}", file=sys.stderr)
        return EXIT_ERROR
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
    c.add_argument(
        "--sarif-output",
        default=None,
        help="also write a SARIF report (useful with the human CI summary)",
    )
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

    s = sub.add_parser(
        "suggest",
        help="draft typed claims from context prose (LLM proposes; schema validates)",
    )
    s.add_argument("--repo", default=".")
    s.add_argument(
        "--context",
        action="append",
        default=[],
        help="context file to analyze (repeatable; auto-detected when omitted)",
    )
    s.add_argument(
        "--input",
        default=None,
        help="read a saved model response instead of calling an LLM API",
    )
    s.add_argument(
        "--output",
        default=None,
        help="write accepted claims to this YAML file for human review",
    )
    s.add_argument(
        "--max-claims",
        type=int,
        default=10,
        help="maximum accepted claims per invocation (default: 10)",
    )
    s.set_defaults(func=_cmd_suggest)

    f = sub.add_parser(
        "affected",
        help="re-check claims touched by a git diff and flag stale context",
    )
    f.add_argument("--repo", default=".")
    f.add_argument("--config", default=None, help="path to .veritaserum.yml")
    f.add_argument(
        "--base",
        default=None,
        help="git base ref (default: merge-base with main/master, else HEAD~1)",
    )
    f.add_argument("--head", default=None, help="git head ref (default: HEAD)")
    f.add_argument("--format", choices=["human", "json", "sarif"], default="human")
    f.add_argument("--output", default=None, help="write report to file instead of stdout")
    f.add_argument(
        "--sarif-output",
        default=None,
        help="also write a SARIF report (useful with the human CI summary)",
    )
    f.add_argument("--naive", action="store_true", help="disable excludes/comment-awareness (eval only)")
    f.add_argument("--dry-run", action="store_true", help="report but never fail")
    f.add_argument("--warn-only", action="store_true", help="report stale drift but exit 0")
    f.add_argument("--no-baseline", action="store_true", help="ignore baseline; gate on all drift")
    f.set_defaults(func=_cmd_affected)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rc = args.func(args)
    if argv is None:
        sys.exit(rc)
    return rc


if __name__ == "__main__":
    main()
