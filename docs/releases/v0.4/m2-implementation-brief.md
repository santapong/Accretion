# M2 implementation brief — 2026-09-06

Baseline: `42067cd`. Scope: opt-in BASELINE_ONLY routing; no paid calls, learned
activation, schema migration, or v1.x feature work. Three Sol High workers and one
coordinator; no nested delegation. Existing worktrees are preserved.

## Ownership

- Freeze worker: `routing/freeze.py`, execution-instance identity, run manager,
  freeze and dispatch tests. Freeze first, dispatch after service integration.
- Selector worker: `routing/catalog.py`, `candidates.py`, `selector.py`, their tests.
- API worker: `routing/errors.py`, `api/routing.py`, API/adversarial tests.
- Coordinator: routing service, persistence/concurrency, request identity, config,
  startup, shared protocols, generated schemas, integration and acceptance docs.

## Shared interfaces

`NodeRoutingService` remains the calling interface for freeze/snapshot/route/replay/
configuration_for/override/cancel. Candidate construction and selection use existing
canonical contracts and immutable snapshot inputs. Service owns persistence. Workers
report exact pure-module signatures before callers are implemented.

Routing control errors carry a stable code, public message and HTTP status. API uses
an APIRouter so startup and route ownership do not overlap. Context and receipt ids
are deterministic; replay reads stored records before generating new timestamps.

## Delivery and verification

Four reviewable stages: freeze; select/service/dispatch; API/operator controls;
acceptance/docs. Pure tests may run in parallel; database tests run serially against
a dedicated disposable database. Full gates are integration checks. Never use the
development database for destructive migration tests. Eleven M2 criteria must each
have executable evidence before their deferred policy rows are removed.
