# AGENTS.md

## Conventions

- JSON marshaling uses `base.JSONMarshal`, never `encoding/json` directly.
- Logging uses `base.Infof`, never `fmt.Printf`.
- New file names must use kebab-case.
- Default public port is `:4984`.
