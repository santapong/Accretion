# Accretion P3 static graphs, checkpoints, and replay runbook

## Scope

P3 enables the persisted `GRAPH/fixed-graph-v1`, `HYBRID/hybrid-rd-v1`, and
`safe-unknown-v1` decisions. Every run instantiates an immutable `RunGraph`
from a VALIDATED, checksum-pinned template; a fail-closed scheduler walks the
graph, checkpoints at node boundaries, blocks on durable human approval gates,
and delegates bounded loop regions to the P2 engine. Dynamic workflow
synthesis and learned routing remain blocked until v0.2+ and are not emulated.

## Safety and recovery invariants

- Only VALIDATED, checksum-stable templates instantiate run graphs; unknown
  names, non-VALIDATED status, mode mismatches, and content drift all reject
  the run with a distinct 409 before any state is created.
- No API accepts executable node/edge topology; templates are code-defined and
  the only operator template input remains the audited strategy override.
- Edge selection is fail-closed: a node outcome with no eligible edge — or an
  ambiguous match — escalates to `REQUIRES_HUMAN`, never a silent default.
- Approval gates persist durable records: a pending decision pauses the wall
  clock, a decision made while the backend is down is honored exactly once on
  resume, and denial routes to the terminal explicitly.
- `safe-unknown-v1` performs at most one bounded replan inside its static
  topology before escalating.
- Checkpoints commit atomically with their `CHECKPOINT_SAVED` event and are
  immutable evidence; a checkpoint the log cannot contain fails closed.
- Reconciliation classifies crashed runs as resumable, recreate, terminal, or
  requires-human: lost workspaces holding candidate work (including any
  revision conflict) always require a human; only an empty lost substrate is
  recreated at its recorded base revision.
- Auto-resume runs only behind `ACCRETION_AUTO_RESUME_ON_RECONCILE`, only from
  a valid checkpoint, and only when the run's runtime is actually available.
- Replay is a pure fold over the immutable event log: the execution trace and
  the graph projection are identical before and after a backend restart, and
  the projection's node set never expands with iterations.

## Template registry

| Template | Mode | Topology |
|---|---|---|
| `direct-v1` | DIRECT | initialize → act → verify → complete |
| `feedback-loop-v1` | LOOP | the unchanged P2 loop region (act/observe/evaluate/verify) |
| `fixed-graph-v1` | GRAPH | plan → **plan approval** → act → observe → verify (one bounded retry) → **outcome approval** → complete |
| `hybrid-rd-v1` | HYBRID | research → theorize → experiment loop ⊂ → develop loop ⊂ → verify → complete |
| `safe-unknown-v1` | HYBRID | plan → bounded loop ⊂ → verify → {complete \| one replan \| escalate} |

⊂ marks a bounded loop subflow rendered as a nested group in React Flow.

## Release acceptance mapping

| Criterion | Evidence |
|---|---|
| `V01-P3-001` | Template guards reject unknown/DRAFT/RETIRED/drifted templates at `start_run` (409 tests); `instantiate_run_graph` enforces VALIDATED structurally; seeding is idempotent and aborts on checksum drift. |
| `V01-P3-002` | An OpenAPI introspection test proves no POST/PUT/PATCH schema exposes writable `nodes`/`edges`; `GET /templates` returns summaries only; template definitions live exclusively in code. |
| `V01-P3-003` | A two-manager restart test lands the crashed run in durable PAUSED, then auto-resumes from the last valid checkpoint to verified success at iteration N+1; invalid checkpoints (sequence ahead of log, workspace conflict) fail closed to `REQUIRES_HUMAN`. |
| `V01-P3-004` | `build_execution_trace` reconstructs traversals, loop iterations, runtime/tool calls, approvals, verifications, and checkpoints from events alone; the restart test asserts trace equality across managers. |
| `V01-P3-005` | Projection equality before/after restart (node/edge IDs, statuses, traversal counts); node ids remain `run_id:key`, matching every durable event. |
| `V01-P3-006` | Backend and UI tests assert the node count never grows with iterations (seven traversals render the same node set); hybrid children carry `parent_id` and render inside their parent group with the iteration badge on the LOOP node. |

## Recorded P3 acceptance evidence

Validated locally on 2026-08-21:

- Default non-live backend suite: 130 passed, 9 live/PostgreSQL cases excluded.
- PostgreSQL 16 migration cycle: upgrade to head, downgrade to base, and
  upgrade to head completed successfully, including the guarded loop-execution
  uniqueness relaxation and the `agent_events.node_id` widening.
- PostgreSQL-backed non-live suite: 136 passed, 3 live cases skipped.
- Frontend: TypeScript, ESLint, 12 Vitest cases, generated OpenAPI contract
  check, and the Vite production build passed.
- Strict backend checks: Ruff and mypy passed for the complete P3 tree.

The release commands are unchanged from P2:

```bash
uv run ruff check .
uv run mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin

npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts
npm run check
npm run test
npm run build
```

With a disposable PostgreSQL 16 instance available through
`ACCRETION_TEST_POSTGRES_URL`, also run the Alembic upgrade/downgrade cycle and
the same backend suite. Signed-in runtime acceptance remains deliberately
opt-in per the P2 runbook.

## Documented SDD deviations

- Gates carry `required_for_risk_gte` (default HIGH): below that risk a
  template gate auto-approves with a durable `ApprovalRecord` and
  `APPROVAL_RESOLVED` evidence, so a deliberately overridden low-risk graph
  run does not dead-end unattended. SDD §9.1 reads gates as unconditional.
- For gate-bearing templates the outcome approval gate is the independent
  human reviewer: their acceptance policies set `require_human_if_risk_gte`
  to `None`, otherwise every HIGH-risk graph run would force `INCONCLUSIVE`
  before its gate could ever run.
- `ExecutionTrace.terminal_state` includes `REQUIRES_HUMAN` (the SDD's
  three-value enum cannot represent escalation without falsifying evidence).
