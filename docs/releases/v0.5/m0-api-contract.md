# v0.5 M0 API and state contract

Status: adopted direction under [M0 decisions](m0-decisions-2026-09-09.md);
implementation and generated DTO verification pending.

All operations authenticate the current principal and resolve persisted
workspace/project membership. Opaque IDs never substitute for access checks.
Collections require project scope, a bounded limit (default 25, maximum 100)
and opaque cursor; return `items` and `next_cursor`. Reads never allocate a
simulator. Cross-scope resources return no body or identifying metadata.

Writes require `Idempotency-Key` (nonempty, bounded to 128 characters). Existing
resource writes require a quoted decimal ETag in `If-Match`; no wildcard. New
immutable registrations omit an ETag because no revision exists. Responses carry
the authoritative resource revision/ETag. Conflicting bodies under a reused key
return 409; a stale revision returns 412; missing precondition returns 428;
malformed contracts return 422. Capability denial returns 403, unknown scoped
resource 404, unavailable configured subsystem 409 and exceeded request limits
413. Domain errors include stable `code`, bounded `message`, resource scope and
safe recovery action; no stack trace, host socket or secret is returned.

## Operations

| Method and path | Request / response / authority |
|---|---|
| POST `/api/v1/embodiments` | Canonical descriptor; immutable registration for an authorized project maintainer; original writer envelope returned |
| GET `/api/v1/embodiments` | Scoped descriptor inventory |
| GET `/api/v1/embodiments/{id}/versions/{version}` | Exact immutable descriptor, never a mutable alias |
| POST `/api/v1/robot-adapters` | Canonical manifest; trusted artifact registration does not grant requested capabilities |
| GET `/api/v1/robot-adapters` | Scoped manifest inventory with current conformance disposition |
| GET `/api/v1/robot-adapters/{id}` | Exact manifest/dependency closure and conformance refs |
| POST `/api/v1/robot-adapters/{id}/conformance-runs` | Admitted bounded suite execution; independent digest-bound report, not a caller-provided PASS |
| POST `/api/v1/simulation-experiments` | Frozen experiment contract and resolved references; returns experiment ID/revision |
| GET `/api/v1/simulation-experiments` | Scoped experiment inventory and counts from durable state |
| GET `/api/v1/simulation-experiments/{id}` | Frozen contract, episodes, preflight/approval dispositions |
| POST `/api/v1/simulation-experiments/{id}/preflight` | Exact planned episode ID/seed; allocates bounded lease and verifies all ten SDD preconditions; emits attributable result |
| POST `/api/v1/simulation-experiments/{id}/approvals` | Human approval of one exact preflight/episode binding; authority service checks persisted role and all pins; cannot be submitted by producer/verifier service identity |
| POST `/api/v1/simulation-experiments/{id}/approvals/{approval_id}/revoke` | Attributed revocation; further actions fail current authorization |
| POST `/api/v1/simulation-experiments/{id}/episodes` | Exact admitted preflight/approval, seed and planned episode identity; orchestrator starts once or returns the original result |
| GET `/api/v1/episodes` | Scoped episodes with bounded filtering/pagination |
| GET `/api/v1/episodes/{id}` | Durable execution and verification state, counts/caps, error/review disposition and current sequence |
| POST `/api/v1/episodes/{id}/action-intents` | Canonical bounded intent; domain service observes/prepares/evaluates/reserves/executes and returns receipt, never raw unchecked actuation |
| POST `/api/v1/episodes/{id}/terminate` | Attributed bounded termination; repeated identical request returns original terminal receipt |
| GET `/api/v1/episodes/{id}/evidence` | Sealed manifest and bounded content-addressed refs, mandatory SIMULATION label |
| POST `/api/v1/episodes/{id}/replay` | Fresh bounded replay under the original class/tolerance/environment; separate attempt and verdict, never resend to uncertain original state |
| GET `/api/v1/episodes/{id}/events` | Ordered paginated domain-event snapshot with next sequence |
| GET `/api/v1/episodes/{id}/events/stream` | Authenticated SSE with source event IDs/sequence and bounded reconnect; on gap refetch snapshot |
| GET `/api/v1/episodes/{id}/export` | Bounded evidence archive/manifest with digests/provenance/retention; never a physical-evidence export |
| GET `/api/v1/simulation-experiments/{id}/comparison` | Backend-derived outcomes over explicit scoped episode IDs; never compute a research GO from an ad hoc UI filter |

## State and result boundaries

Execution progresses DRAFT → VALIDATED → LEASED → PREFLIGHT → RUNNING →
VERIFYING. Failed preflight becomes REJECTED. Safety/resource/uncertainty or
adapter failure becomes ABORTED. Only the orchestrator identity advances these
states and performs cleanup. Missing approval leaves a prepared trial waiting;
it never silently starts.

Verifier task/safety/completeness substates are independently authenticated.
All required PASS permits ACCEPTED; any required FAIL yields FAILED; unresolved
INCONCLUSIVE yields HUMAN_REVIEW. A human review record may request a new bounded
attempt or record a resolution but cannot replace missing independent PASS or
rewrite the original verdict. Contradiction appends quarantine and dependent
invalidation. Terminal states remain attributable after reconnect/restart.

The twelve mandatory SDD event types are all emitted through the durable outbox.
Additional approval/revocation/review/replay events may extend that inventory
under the same strict envelope; new fields are versioned. The mutable state
projection is rebuilt from ordered committed events and compared with its
authoritative revision. Generated OpenAPI and frontend types must agree with
the actual implemented DTOs before M4/M8 completion.

## Existing run API compatibility

The episode service creates a real attributable run with an internal
`SimulationRunBinding` before acquiring the simulation lease. The existing run
list may show that run, but generic software reconciliation and controls cannot
take ownership of it. Resolve the binding before pause/resume/cancel/audit:
dispatch supported operations to the episode service, or return a typed state
conflict with the episode detail path. Do not index an absent DETERMINISTIC CLI,
invent runtime health, resume an uncertain simulator or require a fake Git diff.
Independent episode acceptance is the only successful-completion source for
this bound run. Existing unbound software runs retain their established path.
