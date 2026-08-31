# Veritaserum

Veritaserum makes machine-checkable claims from AI instruction files enforceable in
CI. A claim such as “production code never calls `print`” points back to its source
line in `AGENTS.md`, declares exactly how to check the repository, and produces
source-located evidence when it drifts.

The tool is intentionally deterministic: it does not ask an LLM to interpret prose.
Authors translate suitable statements from `AGENTS.md`,
`.github/copilot-instructions.md`, `CLAUDE.md`, or Cursor rules into a small
YAML/JSON claim schema. Veritaserum checks those claims against the live checkout.
A committed baseline lets brownfield repositories suppress known violations while
still failing on a new file, a changed match, or an additional occurrence in an
already-baselined file.

## Install

Veritaserum requires Python 3.9 or newer and has one runtime dependency, PyYAML.

```bash
python -m pip install .
veritaserum --version
```

For development:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Quick start

Generate conservative claims from facts Veritaserum can observe:

```bash
veritaserum init --repo .
```

Review `claims/generated.yml`, add claims for important context statements, and run:

```bash
veritaserum check --repo .
```

On a repository with accepted existing drift, commit a baseline:

```bash
veritaserum check --repo . --update-baseline
git add .veritaserum-baseline.json
```

Later checks return exit code `1` only when drift not represented by that baseline
meets the configured severity threshold.

## Claim format

Claim files are YAML or JSON. They may contain a claim list directly or a versioned
document:

```yaml
schema_version: 1
claims:
  - id: instructions-no-direct-print
    text: "Production Python code does not call print directly."
    type: forbidden
    severity: error
    source: "AGENTS.md#L18"
    spec:
      globs: ["src/**/*.py"]
      pattern: '\bprint\s*\('
      exclude: ["src/generated/**"]
      exclude_tests: true
      comment_aware: true
```

`source` uses `path#Lstart` or `path#Lstart-Lend`. SARIF results are anchored at
that context claim and include related locations for every new code match.

| Type | Required fields | Result |
| --- | --- | --- |
| `file_exists` | `path` | Drift when the path is not a file |
| `dir_exists` | `path` | Drift when the path is not a directory |
| `contains` | `file`, `pattern` | Drift when the regex is absent |
| `constant` | `globs`, `pattern`, `expect` | Compares every first capture group |
| `dependency` | `manifest`, `pattern` | Drift when the manifest regex is absent |
| `naming` | `globs`, `name_regex` | Full-matches each file stem |
| `forbidden` | `globs`, `pattern` | Reports every regex occurrence |

`constant`, `naming`, and `forbidden` support `exclude` and `exclude_tests`.
`contains`, `constant`, and
`forbidden` support `comment_aware`.

All paths and globs are repository-relative. Absolute paths and `..` traversal are
rejected, and resolved claim files cannot escape through a symlink. Excludes match
whole path components or glob patterns, not arbitrary substrings: excluding
`vendor` does not exclude `vendorized/`.

## Comment-aware matching

Comment masking is a line- and column-preserving lexical pass. It recognizes quoted
strings and escaped delimiters before removing:

- `#` comments in Python, Ruby, shell, YAML, and TOML;
- `//` and `/* ... */` in Go, JavaScript/TypeScript, Java, C/C++, C#, Rust,
  Kotlin, Swift, Scala, PHP, and common stylesheet formats;
- `--` and `/* ... */` in SQL; and
- `<!-- ... -->` in HTML, XML, Markdown, and MDX.

Unknown extensions are left unchanged rather than guessed. This is not an AST
parser: regular expressions are still evaluated over string literal contents, and
language-specific constructs such as JavaScript regex literals or C++ raw strings
can require a narrower pattern or explicit exclusion.

Test exclusion recognizes `test/`, `tests/`, and `__tests__/` path components plus
common names such as `*_test.go`, `test_*.py`, `*_test.py`, `*.test.*`, and
`*.spec.*`.

## Configuration

`.veritaserum.yml`:

```yaml
schema_version: 1
context_files:
  - AGENTS.md
  - .github/copilot-instructions.md
claims:
  - claims/**/*.yml
  - claims/**/*.yaml
  - claims/**/*.json
global_exclude:
  - .git
  - .venv
  - build
  - dist
  - node_modules
  - vendor
severity_gate: error
baseline: .veritaserum-baseline.json
```

Configuration and claims are strict: unknown fields, invalid regular expressions,
wrong value types, duplicate IDs, unsafe paths, and malformed YAML/JSON are usage
errors instead of silent defaults.

## CLI and reports

```text
veritaserum check [--repo PATH] [--config FILE]
                   [--format human|json|sarif] [--output FILE]
                   [--sarif-output FILE] [--no-baseline]
                   [--update-baseline] [--warn-only] [--dry-run]
veritaserum init [--repo PATH] [--force]
```

Exit codes are `0` for pass, `1` for new gating drift, and `2` for configuration,
I/O, or usage errors. `--warn-only` and `--dry-run` preserve reporting but convert a
drift exit to `0`. `--sarif-output` writes SARIF alongside the selected primary
format so CI can display a human summary and upload one analysis result.

JSON reports include all evidence and new evidence separately. SARIF 2.1.0 emits
one result per newly drifted claim, stable partial fingerprints, the claim source as
the primary location when supplied, and code evidence as related locations.

## Baselines

Baseline schema version 2 stores deterministic, sorted violation keys without a
timestamp. Forbidden matches use a repository path, a hash of the matched text, and
an occurrence number. This survives line movement, detects another identical
occurrence in the same file, and gates changed matched text. File, directory,
dependency, and naming violations use path-stable keys.

Version 1 path-only baselines remain readable. Updating a baseline rewrites it as
version 2. Because a version 1 entry suppresses every occurrence in that path,
regenerate old baselines to gain same-file occurrence detection.

## GitHub Action

The repository is a composite action. It installs the package from the checked-out
action revision, runs one check, writes a job summary, and produces SARIF.

```yaml
- uses: actions/checkout@v4
- id: veritaserum
  uses: Arnav0507/veritaserum@v0.2.0
  with:
    repo: "."
    fail-on-drift: "true"
    sarif-file: "veritaserum.sarif"
- if: always() && hashFiles('veritaserum.sarif') != ''
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: veritaserum.sarif
```

Uploading SARIF requires `security-events: write`; see
[`action/example-workflow.yml`](action/example-workflow.yml) for a complete job.
The action exposes `failed`, `exit-code`, and `sarif-file` outputs. Configuration
errors always fail; `fail-on-drift: "false"` suppresses only exit code `1`.

## Scope and limitations

- Claims are authored by people. `init` scaffolds only observable context files,
  dependencies, package-manager pins, and source directories.
- Checks are regex- and filesystem-based, not semantic program analysis.
- A missing input is `UNVERIFIABLE` rather than drift. Unverifiable claims are
  reported but do not currently gate.
- Veritaserum makes no precision or recall claim. The included fixtures are
  regression tests, not a representative benchmark.
- Baselines are repository state, not an approval workflow; review baseline changes
  like any other policy change.

See [`examples/claims.yml`](examples/claims.yml) for a complete claim set.
