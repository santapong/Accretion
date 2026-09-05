# v0.4 M1 — Compatibility engine, gates and identity

SDD v0.4 §19: *deterministic admissibility before anything learned.* M1 answers one question
and refuses to answer the other: **may this tuple be built at all**, never *which tuple is
best*. It owns four acceptance criteria — AC4-M1-005 through 008 — and shipped in two PRs
against contracts M0 froze.

M1 selects nothing, scores nothing and learns nothing. Everything it produces is a
`CompatibilityDecision`: a sealed, replayable record naming the rule that spoke, the version
of the rules it spoke under, the four snapshot ids it saw and the code from a closed
vocabulary that explains it.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M1.1 — the engine | `routing/reasons.py` (`ReasonCode`, `ALL_REASON_CODES`, `RULE_VERSION = compat-rules/1`), `routing/snapshot.py` (`RoutingSnapshot` and `RegistrySnapshotBuilder`; four snapshot ids over narrow `(id, status, version)` projections that never carry a token), `routing/compatibility.py` (`evaluate`, `evaluate_joint`, `map_resolution`), `ids.derived_id`, the twelve v0.4 `EventType` members | `tests/test_v04_m1_{compatibility,events,ids}.py`; the same snapshot replays byte-identical decisions; `UNKNOWN` is never compatible; the golden reason list is transcribed by hand (#128) |
| M1.2 — gates, protocols, identity | `routing/gates.py`, `routing/protocols.py`, `routing/identity.py`; the two `RunManager` attributes; the MCP-state refinement on `RoutingSnapshot`/`map_resolution`; the four criteria rows flipped | `tests/test_v04_m1_gates.py`, `tests/test_v04_m1_postgres_store.py`; the AST import-graph assertion; the §18.2 monotonicity property; the derived-identity tests |

## What M1.2 decided, and why

**Gates are outside the learned router by construction, not by convention.** `routing/gates.py`
imports nothing from `routing.selector`, `routing.candidates`, `routing.ranker`,
`routing.gbdt`, `routing.adapter` or `experience.*` — and it does not import
`CompatibilityEngine` either. The joint evaluator arrives as the `JointEvaluator` structural
protocol, so there is no import edge to the rules at all, and the ordering helper
`gate_then_evaluate` is the only place a gate result and a compatibility result meet. Gates
run first, all of them run (an operator fixing a refusal needs every reason, not the
earliest), and a refusal means `evaluate_joint` is never consulted. AC4-M1-005 is proved
twice over: an AST walk that fails on the *name* of a forbidden module, and a call-order spy.

The AST assertion is made on names rather than on importability on purpose. Four of the six
modules do not exist yet; a test that imported them to check the edge would pass vacuously
today and keep passing on the day the ranker landed.

**Routing never pre-approves.** `CapabilityPolicyEngine.authorize` is called with
`approval=None` and its `REQUIRE_APPROVAL` becomes `INCOMPATIBLE` with the new
`APPROVAL_REQUIRED` reason code. A v0.1 approval is content-bound to a request, and no request
exists while a configuration is still being chosen. `RULE_VERSION` stays `compat-rules/1`;
ADR4-M1-003 in the [backlog](backlog.md) records why, and records that the next addition after
v0.4 ships does bump it.

**The gate asks the policy engine; it never re-implements it.** The verdict is always
`authorize`'s. Only the *word* an operator reads is chosen here, by re-reading the same inputs
in the same order — the pattern `map_resolution` already uses on the resolver's outcomes, and
for the same reason: parsing the engine's English `reason` string would make a sentence
load-bearing. Two authorities that could disagree would let a capability the gateway refuses
at execution time be routed to anyway.

**Identity is derived, never minted.** `routing_request_id` covers the node contract hash, all
four snapshot ids, the router and adapter versions and the mode. All four ids, not the
registry digest alone: the same node routed under a different policy is a different request,
and `policy_snapshot_id` is the one that is a label rather than a hex digest and therefore the
easiest to drop by accident. `VerificationSpecBuilder` derives its `contract_id` from a digest
of the spec's own body and takes `created_at` from the task, so re-freezing an unchanged node
is a no-op on the append-only store rather than a second row or a rejection.

**The deferred M1.1 minor is fixed structurally.** `CapabilityResolver` runs its plugin gate
*before* its MCP readiness gate, so a capability contributed by a disabled plugin resolves
`DISABLED` even when its MCP-backed binding points at a perfectly healthy server. M1.1
labelled every such outcome `MCP_SERVER_NOT_READY`, which sent an operator to restart a server
that was never down. `RoutingSnapshot` now carries the observed `(server id, state)` pairs —
the same projection the capability-registry digest already hashes, and never an endpoint, a
command line or an auth profile — and `map_resolution` reports `MCP_SERVER_NOT_READY` only
when the bound server's observed state is not `READY`.

## Known gaps M2 inherits

- **`execution_instance_id` derives under the `run` prefix.** `ids.py`'s registry has no kind
  for a node execution instance, and the field on `NodeContract` is a free `str`. The value is
  therefore derived under `run` with a literal domain separator (`EXECUTION_INSTANCE_DOMAIN`)
  as its first digest part, so it cannot collide with anything else derived under that kind —
  but it is *shaped* like a run id, and `has_prefix(value, "run")` is true for both. M2 is the
  milestone that first persists one and should mint the kind properly; the change is confined
  to `routing/identity.py` and one `_PREFIXES` entry.
- **`CapabilityRequirement.required_scope` is still not refused.** M1.1 deferred it to "M1.2's
  permission gate" and M1.2's permission gate answers a different question — may this principal
  route in this workspace — rather than whether a connection carries a scope. The deferral test
  `test_a_required_scope_is_not_yet_refused_by_the_joint_rule` is unchanged and still pins it.
  It belongs with M2's candidate builder, which is the first thing that binds a capability to a
  connection.
- **`gates._decide` duplicates `CompatibilityEngine._decide`'s digest input.** Deliberate: the
  alternative is the import edge AC4-M1-005 forbids, or reaching into another class's private
  method, which is the same edge with worse manners. The two are held together by a test that
  derives a gate decision's id by hand from the documented parts.

## The frozen protocol

`src/accretion/routing/protocols.py` freezes the two seams M2 and M3 implement —
`NodeRoutingService` and `FeedbackPipeline` — plus `RoutingMode`, `FrozenNode` and the
`RecoveryDecision` placeholder. Nothing in M1 calls any of them, which is exactly why the file
needs a hash: a signature that drifted between M1 and M2 would not fail here, it would fail in
the integration PR as a type error in a file nobody touched.

```
sha256(src/accretion/routing/protocols.py) = dc47c8022563df646cfbeac84d6a28cf0d21c5fae6b1933e7d4a0e5cfdd35d47
```

A later PR that changes this file must update the digest in the same diff. `RecoveryDecision`
is the one member expected to move: M3 owns `accretion.feedback.recovery`, which does not exist
on develop, so it is declared here as a `Protocol` that M3's real class satisfies structurally
— which lets M3 land without editing this file at all.

M2 explicitly extends `NodeRoutingService` with `latest_receipt` and `claim_dispatch`:
the run manager must resolve an operator-amended head and durably claim it before
any side effect. The M1 digest was
`4d7b8d670f370e08aa4fb4cb3a2ea45eea2f2fabc6d4e302be6e39f4f20d5a34`.
The updated digest above records this reviewed calling-interface change; canonical
contracts and the feedback protocol are unchanged.

## Close-out

The four rows `AC4-M1-005`, `AC4-M1-006`, `AC4-M1-007` and `AC4-M1-008` are **deleted** from
[`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml) rather than set to `test`:
`test` is the default for a criterion with no policy row, and a row restating the default is
one more place to keep in step with the SDD.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py --stage v0.4-M1
```

On the gate lane, with PostgreSQL up:

```
in scope: 121   proven: 115   unmet MUST: 0
in scope: 4     proven: 4     unmet MUST: 0
```

The whole-harness line is the CI gate; the `--stage` form stays a local diagnostic. Both need
the lane PostgreSQL running and `ACCRETION_DATABASE_URL` pointed at it: five API-surface tests
build a real application and one of them, `test_p5_api_surface_is_additive_and_project_gated`,
is the sole claiming test for `V02-P5-001`. Without a reachable database that criterion reads
`FAILING` and the first line is `in scope: 121   proven: 114   unmet MUST: 1` — a database
problem wearing a criterion's clothes.

Placement note: `policy_snapshot_id` lives in `routing/snapshot.py` beside the three registry digests and is re-exported from `routing/gates.py`, so the policy identity cannot be spelled two ways.
