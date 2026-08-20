# Accretion P0 runbook

## Runtime compatibility

P0 is validated against Codex CLI `>=0.148,<0.149` and Claude Code
`>=2.1.231,<2.2`. Other versions remain visible but report `DEGRADED` until their
protocol fixtures pass.

Accretion checks `codex login status` and `claude auth status`; it never reads or
copies provider credentials. Codex uses stable App Server JSONL over stdio. Claude
uses print mode with structured stream JSON.

## Local acceptance

1. Start PostgreSQL and apply migrations.
2. Confirm both CLIs are signed in.
3. Run the default fake-runtime suite.
4. Set `ACCRETION_LIVE_PROVIDERS=1` and run tests marked `live`.
5. Confirm two Codex threads and one Claude run produce normalized events.
6. Interrupt a disposable run and verify it becomes resumable or explicitly
   requires operator reconciliation.

Live tests use harmless prompts and disposable Git worktrees. They are never
enabled by default in CI.

## Recovery policy

- Runs that never started may be recreated.
- Runs with a durable session and intact worktree may resume.
- Missing worktrees, revision conflicts, and uncertain side effects become
  `REQUIRES_HUMAN`.
- A side effect with durable intent but no durable result is never retried
  automatically.
