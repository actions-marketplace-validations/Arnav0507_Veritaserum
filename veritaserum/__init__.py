"""Veritaserum — testable AI instructions.

Verifies AI context-file claims (AGENTS.md, copilot-instructions.md, CLAUDE.md,
Cursor rules) against the live code, in CI. Emits VERIFIED / DRIFTED / UNVERIFIABLE
per claim, gates PRs on *new* drift via a baseline, and reports human/JSON/SARIF.
"""
__version__ = "0.2.0"

VERIFIED = "VERIFIED"
DRIFTED = "DRIFTED"
UNVERIFIABLE = "UNVERIFIABLE"
SCHEMA_VERSION = 1
