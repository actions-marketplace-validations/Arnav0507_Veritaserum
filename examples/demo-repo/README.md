# Veritaserum demo repository

This tiny Go project shows how Veritaserum ties `AGENTS.md` conventions to
machine-checkable claims and CI drift detection.

## What the context says

`AGENTS.md` states four conventions:

- JSON marshaling uses `base.JSONMarshal`, never `encoding/json` directly
- Logging uses `base.Infof`, never `fmt.Printf`
- New UI file names use kebab-case
- The default public port is `:4984`

Each convention maps to a typed claim in `claims/demo.yml`.

## Try it locally

From the Veritaserum repository root:

```bash
python -m pip install .
veritaserum check --repo examples/demo-repo
```

Exit code `0` — the code matches the instructions.

## Introduce drift

Apply the bundled patch to break the logging convention:

```bash
cp -r examples/demo-repo /tmp/demo-drift
patch -p1 -d /tmp/demo-drift < examples/demo-repo/drift.patch
veritaserum check --repo /tmp/demo-drift
```

Exit code `1` — Veritaserum reports `demo-no-printf` with evidence in `main.go`
and anchors the SARIF result at `AGENTS.md#L5`.

Inspect the human report or emit SARIF:

```bash
veritaserum check --repo /tmp/demo-drift --format sarif --output drift.sarif
```

## Use in GitHub Actions

Copy `.github/workflows/veritaserum.yml` into your repository, or see
[`action/example-workflow.yml`](../../action/example-workflow.yml) in the main
project.

When a pull request introduces `fmt.Printf`, the action fails and uploads SARIF
annotations on the context file line that was violated.

To re-check only claims touched by the diff:

```bash
veritaserum affected --repo . --base origin/main --head HEAD
```

## Next steps

- Run `veritaserum suggest --repo .` to draft additional claims from prose
- Review generated YAML before committing — verification stays deterministic
- Commit a baseline after accepting known brownfield drift
