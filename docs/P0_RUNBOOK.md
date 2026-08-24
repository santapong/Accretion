# Accretion P0 runbook

<img src="assets/accretion-architecture.svg" alt="Accretion runtime architecture from the operator interface through the authoritative control plane to provider adapters, isolated worktrees, durable state, events, and independent verification" width="100%" />

For a first local run, follow the [developer guide](DEVELOPER_GUIDE.md) and
[deterministic showcase](SHOWCASE.md) before enabling signed-in providers. The
[frontend guide](FRONTEND_GUIDE.md) maps runtime health, session, live-run, and
history evidence to the current operator routes.

## Runtime compatibility

P0 is validated against Codex CLI `>=0.148,<0.149` and Claude Code
`>=2.1.231,<2.2`. Other versions remain visible but report `DEGRADED` until their
protocol fixtures pass.

Accretion checks `codex login status` and `claude auth status`; it never reads or
copies provider credentials. Codex uses stable App Server JSONL over stdio. Claude
uses print mode with structured stream JSON. Claude subprocesses run in safe mode
so user hooks, plugins, and project customization cannot silently change the
provider protocol; Accretion's explicit MCP configuration remains authoritative.
`SessionConfig.model` is passed through when the caller pins a compatible model.

## Local acceptance

1. Start PostgreSQL and apply migrations.
2. Confirm both CLIs are signed in.
3. Run the default fake-runtime suite.
4. Set `ACCRETION_LIVE_PROVIDERS=1` and run tests marked `live`. To pin the
   calibration model, also set `ACCRETION_CLAUDE_LIVE_MODEL`, for example
   `ACCRETION_CLAUDE_LIVE_MODEL=sonnet`.
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

## Recorded P0 acceptance evidence

Validated locally on 2026-08-20 with the supported signed-in subscription CLIs:

- Codex CLI `0.148.0`: two independent threads completed through one App Server.
- Claude Code `2.1.231`: a harmless structured run produced normalized start,
  progress, and terminal events.
- Claude and Codex completed simultaneously in separate disposable Git
  repositories without sharing a mutable path.
- Simulated startup failure and App Server EOF each produced exactly one
  terminal failure event and closed their event streams.
- Backend startup moved interrupted runs through reconciliation and marked
  unresolved side-effect intents `UNKNOWN` without retrying them.
- Concurrent PostgreSQL ledger instances recording the same idempotency key
  resolved to one durable operation.
- Health-classification fixtures cover `READY`, `DEGRADED`, `AUTH_REQUIRED`,
  `RATE_LIMITED`, and `UNAVAILABLE`.

The live cases use the exact harmless prompt documented in
`tests/test_live_runtimes.py` and create no files or tool calls. The signed-in
suite remains opt-in so CI never consumes a local operator's provider session.
