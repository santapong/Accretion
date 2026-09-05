# M2 deterministic node routing

M2 is opt-in with `ACCRETION_ENABLE_NODE_ROUTING=true`. The default remains
`false`; existing direct, loop, and flag-off graph execution stays on its existing
path. Only `BASELINE_ONLY` is supported. AUTO, SHADOW, learned activation,
exploration, and v1.x features are not part of this change.

## Catalog and execution

The application ships an audited FAKE-runtime baseline with explicit model and
verifier identities. A live-provider run has no shipped audited model catalog and
requires human review when routing is enabled. Turning on routing does not enable
live providers or authorize paid calls. An injected catalog must provide exact,
versioned runtime/model/tool/environment bindings; an unavailable binding cannot
be replaced by an invented fallback.

For graph AGENT, TOOL, and VERIFIER nodes, verification semantics are persisted
before the immutable node contract. Routing persists candidates, compatibility
decisions, the receipt, and its audit event before dispatch. An operator amendment
creates a new receipt rather than editing history. The dispatcher resolves the
current head, checks the selected configuration and runtime, and durably claims
dispatch before invoking it. Changing a registry snapshot does not create an
independently executable sibling decision for the same frozen node.

The environment digest describes a local-process platform/interpreter manifest;
it is not a container-image attestation. Project outcome history is absent until
the feedback milestone and is not treated as evidence of success. The cold-start
success lower bound therefore selects an eligible audited fallback or requests
human review, never a learned exploit decision.

## Operator API

All endpoints use the application's authenticated principal and workspace
membership. Unknown, foreign-workspace, and unauthorized receipts return the same
not-found response. Viewer membership cannot mutate routing decisions.

- `POST /api/v1/projects/{project_id}/node-executions/{execution_instance_id}/route`
  accepts canonical frozen-node and routing-request identities, the expected node
  hash and registry snapshot digest, and `BASELINE_ONLY`.
- `GET /api/v1/routing-decisions/{receipt_id}` reads the immutable receipt.
- `GET /api/v1/routing-decisions/{receipt_id}/candidates` reads its candidates.
- `POST /api/v1/routing-decisions/{receipt_id}/override` requires an eligible
  candidate from that receipt, `expected_receipt_version`, an uppercase structured
  `reason_code`, and a nonempty explanation.
- `POST /api/v1/routing-decisions/{receipt_id}/cancel` appends a cancelled head.

Repeat requests return their persisted result. A stale head, cancelled decision,
or already-claimed execution cannot be amended or dispatched. Cancellation records
principal attribution in the new receipt and audit event; it does not manufacture
a selected-candidate override record. With routing disabled, these endpoints
report `NODE_ROUTING_UNAVAILABLE`.

## Recovery and rollback

PostgreSQL uses a transaction-scoped advisory lock per run. Receipt records and
their events commit together, with existing database uniqueness constraints as a
second guard. MemoryStore provides the same atomic publication behavior for the
offline lane. No schema migration is introduced.

A crash after the durable dispatch claim but before an observed runtime result is
an **uncertain execution**, not proof that nothing ran. Do not automatically
resubmit it: inspect the runtime/audit evidence and reconcile it first. M2 does
not claim exactly-once external execution. Runtime identity/version/capability
drift or an unavailable runtime fails closed.

To disable routing for subsequent runs, restore the flag to `false` and restart
the application. Preserve receipts and audit history. Do not use a flag rollback
to bypass an unresolved dispatch claim on an existing run. Human-review decisions
remain waiting until an eligible operator decision or explicit cancellation;
adding a catalog does not rewrite a stored receipt.

## Protocol decisions

The runtime-provider dispatch seam already exists, so M2 does not freeze the
obsolete `runtime.provider == run.provider` constraint from the earlier plan.
The selected receipt supplies runtime/model identity instead. `NodeRoutingService`
adds `latest_receipt` and `claim_dispatch`; the updated protocol digest is recorded
in [the M1 plan](m1-plan.md). Canonical contracts and migrations remain frozen.

The additional v1.x SDD package is design context only. It does not override the
v0.4 acceptance criteria or authorize implementation of later milestones.
