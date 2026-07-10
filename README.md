# Veritaserum

**Testable AI instructions.** Your repo's AI context files — `AGENTS.md`,
`.github/copilot-instructions.md`, `CLAUDE.md`, Cursor rules — are hand-written,
unverified, and rot silently. They claim things ("never use `encoding/json`", "we pin
`pnpm@10.4.1`", "auth lives in `auth/`") that drift out of sync with the code, and every
AI assistant and new hire is fed those stale claims as fact.

Veritaserum decomposes each claim into a typed, checkable assertion and **verifies it
against the live code, in CI** — failing the build only on *new* drift.

> This is P0: the drift-detection wedge (CLI + GitHub Action). It is the seed of a larger
> vision — a verified, self-maintaining, vendor-neutral context layer. See
> `../project-summary.md`.

## Install

```bash
pip install veritaserum        # (P0: install from source — pip install -e .)
```

## Quickstart

```bash
# 1. Scaffold a starter claim set from deterministic facts (deps, dirs, context files).
veritaserum init --repo .

# 2. Verify claims against the code.
veritaserum check --repo .

# 3. On a brownfield repo, accept existing violations so CI gates only NEW drift.
veritaserum check --repo . --update-baseline
git add .veritaserum-baseline.json
```

## How it works

A **claim** is a typed assertion in a YAML/JSON file under `claims/`:

```yaml
- id: go-no-encoding-json
  text: "Never use encoding/json directly; use base.JSONMarshal."
  type: forbidden
  severity: error            # error gates CI; warn only annotates
  source: AGENTS.md#L42      # traceability back to the context file
  spec:
    globs: ["**/*.go"]
    pattern: '\bjson\.(Marshal|Unmarshal)\b'
    exclude_tests: true      # check ACTUAL runtime usage, not naive grep
    comment_aware: true      # ignore matches inside comments
```

Veritaserum runs the matching checker and emits **VERIFIED / DRIFTED / UNVERIFIABLE** with
evidence. A baseline records pre-existing violations (by path, not line number, so it
survives churn) and the build fails only when a **new** violation appears.

### Claim types

| Type | Verifies |
|------|----------|
| `file_exists` / `dir_exists` | a path exists |
| `contains` | a file contains a pattern |
| `constant` | a captured value equals the expected one (e.g. a port) |
| `dependency` | a manifest declares a dependency |
| `naming` | files matching a glob obey a name regex (e.g. kebab-case) |
| `forbidden` | a "never X" rule — DRIFTED if real runtime code matches |

### The precision principle

`forbidden`/`naming` checks are precision-critical: **one false positive mutes a CI check
forever.** Veritaserum checks *actual runtime usage* — excluding tests, infra, bootstrap,
and comments — not raw text hits. In validation, naive grep scored ~89% precision; refined
checking reached ~100%. Run `--naive` to see the difference (evaluation only).

## CLI

```
veritaserum check --repo PATH [--config F] [--format human|json|sarif]
                  [--output F] [--dry-run] [--warn-only]
                  [--no-baseline] [--update-baseline] [--naive]
veritaserum init  --repo PATH [--force]
```

Exit codes: `0` pass · `1` new drift gated the build · `2` config/usage error.

## GitHub Action

See `action/example-workflow.yml`. On every PR it runs the check, writes SARIF (inline
annotations on the drifted context-file line), posts a job summary, and fails on new drift.

## Config (`.veritaserum.yml`)

```yaml
schema_version: 1
context_files: [AGENTS.md, .github/copilot-instructions.md]
claims: ["claims/**/*.yml"]
global_exclude: [node_modules, vendor, dist, build, .git]
severity_gate: error        # fail CI only on >= this severity
baseline: .veritaserum-baseline.json
```

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## P0 limitations (by design)

- Structural / config / dependency / naming / forbidden claims only. Semantic claims
  ("service X owns Y") and auto-capture are later phases.
- Comment-aware matching does not parse string literals (rare edge case).
- Claims are authored by hand or via `init`; LLM extraction is out of scope for P0.
