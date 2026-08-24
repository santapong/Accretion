# Accretion P0 runbook

<img src="../assets/accretion-architecture.svg" alt="Accretion runtime architecture from the operator interface through the authoritative control plane to provider adapters, isolated worktrees, durable state, events, and independent verification" width="100%" />

For a first local run, follow the [developer guide](../guides/developer.md) and
[deterministic showcase](../guides/showcase.md) before enabling signed-in providers. The
[frontend guide](../guides/frontend.md) maps runtime health, session, live-run, and
history evidence to the current operator routes.

## Runtime compatibility

P0 is validated against Codex CLI `>=0.148,<0.149`, Claude Code
`>=2.1.231,<2.2`, and opencode `>=1.18,<1.19`. Other versions remain visible but
report `DEGRADED` until their protocol fixtures pass.

Accretion checks `codex login status`, `claude auth status`, and `opencode auth
list`; it never reads or copies provider credentials. Codex uses stable App Server JSONL over stdio. Claude
uses print mode with structured stream JSON. Claude subprocesses run in safe mode
so user hooks, plugins, and project customization cannot silently change the
provider protocol; Accretion's explicit MCP configuration remains authoritative.
`SessionConfig.model` is passed through when the caller pins a compatible model.

opencode runs against one headless `opencode serve` process shared by all sessions,
reading its normalized events from the `/global/event` bus. `GET /event` publishes
only `server.connected` and heartbeats, so the global bus is the only usable stream.
Each session is pinned to its worktree with the `directory` query parameter, and the
server is scoped through inline `OPENCODE_CONFIG_CONTENT` so the operator's own
`~/.config/opencode` is never read or modified.

Two operational notes specific to opencode:

- It provides **no capability gateway**. A task with a non-empty capability set is
  refused with a terminal `RUNTIME_CALL_FAILED` naming the capabilities, and must be
  routed to Claude or Codex instead. See SDD 15.4.
- Its model is set by `ACCRETION_OPENCODE_MODEL` in `providerID/modelID` form. Free
  preview models are withdrawn without notice; when that happens `health()` reports
  `DEGRADED` naming the model, and the only change required is that one variable.
  Rehearse it with `test_live_opencode_rejects_a_withdrawn_model`.

## Local acceptance

1. Start PostgreSQL and apply migrations.
2. Confirm all three CLIs are signed in.
3. Run the default fake-runtime suite.
4. Set `ACCRETION_LIVE_PROVIDERS=1` and run tests marked `live`. To pin the
   calibration model, also set `ACCRETION_CLAUDE_LIVE_MODEL`, for example
   `ACCRETION_CLAUDE_LIVE_MODEL=sonnet`.
5. Confirm two Codex threads, one Claude run, and two concurrent opencode
   sessions on one server all produce normalized events.
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
