# v0.4 M0 — the freeze record

SDD v0.4 §19 gates M0 on *schemas, hashes, migrations, fixtures approved*, and the
cross-release registry §19 gates a contract release on "JSON Schema or equivalent
machine-readable validation exists" with "every contract has one owner and schema version".
This page is the evidence for both: one row per committed schema, naming the exact bytes
that were frozen, the version they were frozen at, where a record of that shape is stored,
and the migration that made the storage exist.

It is checked by
[`tests/test_v04_m0_persistence_models.py`](../../../tests/test_v04_m0_persistence_models.py):
every digest below is recomputed from the file on disk, every schema version is compared
against `CONTRACT_SCHEMA_VERSION`, and every table named must be one of the fifteen the
migration creates. A schema regenerated without this page being updated is a red test, not
a stale document. That is the whole point of writing the digests down: a freeze that nobody
can verify is a promise, and a promise is not a freeze.

M0 claims **no acceptance criterion** (ADR-052). Its proof is that the later milestones
prove theirs against contracts that could not have moved underneath them.

## Provenance

- Contracts: [`src/accretion/contracts/routing.py`](../../../src/accretion/contracts/routing.py),
  enumerated by `CONTRACT_INVENTORY` — nineteen models.
- Canonical serialization and the digest: `contracts/canonical.py` (ADR-056). Sorted keys,
  no whitespace, UTF-8, integers as integers, decimals as strings, RFC 3339 UTC `Z`, and
  `content_hash` excluded from its own input.
- Schemas: `docs/contracts/v0.4/*.schema.json`, JSON Schema 2020-12, written and verified by
  [`scripts/export_contract_schemas.py`](../../../scripts/export_contract_schemas.py)
  (`--check` fails naming the first stale file).
- Storage: [`migrations/versions/0017_v04_m0_routing_contracts.py`](../../../migrations/versions/0017_v04_m0_routing_contracts.py)
  and the fifteen row models in `src/accretion/persistence/models.py`.
- Fixtures: `tests/fixtures/contracts/v0.4/`, four kinds per contract.

The digests below are of the **committed schema files**, not of the contracts. They are the
digest an auditor with the repository and no Python can reproduce with `sha256sum`.

## The frozen schemas

| Schema | sha256 of the committed file | `schema_version` | Stored in | Migration |
|---|---|---|---|---|
| `ObjectiveContract.schema.json` | `3536f6d12f5487fc9660b096c6a7eb59bf98154b60221c67482feb904a7020ef` | `1.0.0` | `objective_contracts` | `0017_v04_m0_routing_contracts` |
| `ObjectiveContractRef.schema.json` | `c7cd0319822bf50c53b8e0002e0a9360368ddc40385ec2c0ca475ab3dd44942a` | `1.0.0` | not persisted — registry §4 reference, embedded in the header | `0017_v04_m0_routing_contracts` |
| `NodeContract.schema.json` | `826abc5119c9c4f1ac1bc97de8929e0b88bfbde9ffcf1f2304f9bce056af36f9` | `1.0.0` | `node_contracts` | `0017_v04_m0_routing_contracts` |
| `VerificationSpec.schema.json` | `72e8536a36bd82199aa496512cc86ddd26cd0345ee8af398d7c9136355aba7dc` | `1.0.0` | `verification_specs` | `0017_v04_m0_routing_contracts` |
| `TaskFeatures.schema.json` | `67da2926d5f5bbf1bd4d5ff8043a14f6e642416f414e919cd35da99aa3b56d47` | `1.0.0` | not persisted — feature block, embedded in `routing_requests` | `0017_v04_m0_routing_contracts` |
| `ProjectFeatures.schema.json` | `a57169e831b917b58f77f91272e4908491cd7ccef2f11336e579c3a89276b16c` | `1.0.0` | not persisted — feature block, embedded in `routing_requests` | `0017_v04_m0_routing_contracts` |
| `RoutingContext.schema.json` | `bff224ddedd758829119434bb017eb6b80fbb1a35f1b34c21fe26eed0dfef23e` | `1.0.0` | `routing_requests` | `0017_v04_m0_routing_contracts` |
| `ExecutionConfiguration.schema.json` | `6ddc047a9cba4e2ce147d2fc788e6a0ac04bc2f79cb7a3b6c744bb943e5f0f00` | `1.0.0` | not persisted — embedded in `configuration_candidates` | `0017_v04_m0_routing_contracts` |
| `ConfigurationCandidate.schema.json` | `3ab0b33ba1af398b23bc40c5a6ce74fbe587b49a6937d1114a844f507f604a39` | `1.0.0` | `configuration_candidates` | `0017_v04_m0_routing_contracts` |
| `CompatibilityDecision.schema.json` | `2e252fb3863acff5bc23fa9b87d858b557d734b528b24130f7c2501277278907` | `1.0.0` | `compatibility_decisions` | `0017_v04_m0_routing_contracts` |
| `StructuredExplanation.schema.json` | `6146d98fd62845f7f27dbc39a95e401db34550164017c03b904c45e63b4fe1bd` | `1.0.0` | not persisted — embedded in `routing_receipts` | `0017_v04_m0_routing_contracts` |
| `RoutingDecisionReceipt.schema.json` | `9485d546d082dba8fc60e1999e1ec8e3d10d5b16a1dd275fc81d67d6d6d7b5e2` | `1.0.0` | `routing_receipts` | `0017_v04_m0_routing_contracts` |
| `IndependentVerificationResult.schema.json` | `4505afb080668259c7eb1df288393b43b856c7fd6830286e5c6c0e86cef14570` | `1.0.0` | `verification_results` | `0017_v04_m0_routing_contracts` |
| `ExperienceRecord.schema.json` | `cbde4ed347f6dda66be6199a6fc4be26ed77564f4233105b8de07afa49d27308` | `1.0.0` | `experience_records` | `0017_v04_m0_routing_contracts` |
| `FailureEvent.schema.json` | `f239b3a29e01d61c30884127233718abfd781a885ceff7b7983047c11c9b1529` | `1.0.0` | `failure_events` | `0017_v04_m0_routing_contracts` |
| `RouterModelVersion.schema.json` | `66503689b0ec29c3c6438bc20b809691ae4a59abd7116353308525c03c5b80a6` | `1.0.0` | `router_model_versions` | `0017_v04_m0_routing_contracts` |
| `RouterTrainingSnapshot.schema.json` | `fc5c672856e95a7a9cb6714ce958accfb311075296f44d3d910b4666ac63c2c3` | `1.0.0` | `router_training_snapshots` | `0017_v04_m0_routing_contracts` |
| `RouterPromotionReport.schema.json` | `7a835f782e10a1c0453539599aa6a6c31835fecc3ab6c5dd6b476db2c6ac2da5` | `1.0.0` | `router_promotion_reports` | `0017_v04_m0_routing_contracts` |
| `ShadowDecision.schema.json` | `5b1f5810d68be16b05565e6e829a1e4b61a5889d5767f059a1dcbd4b7c9fc603` | `1.0.0` | `shadow_decisions` | `0017_v04_m0_routing_contracts` |

Nineteen schemas, fourteen of them stored in a table of their own. The five that are "not
persisted" are not gaps: `ObjectiveContractRef` is a registry §4 typed reference that
appears inside other contracts' headers; `TaskFeatures` and `ProjectFeatures` are the
feature blocks a `RoutingContext` is built from; `StructuredExplanation` is the body of a
receipt; and `ExecutionConfiguration` is the configuration a `ConfigurationCandidate`
proposes. Each is reachable only through the record that embeds it, and giving any of them
a table would have given it a lifetime independent of the decision it explains — which is
exactly what registry §16's provenance chain forbids.

## The fifteenth table

`routing_overrides` — the fifteenth of the tables above, and one of the fourteen §13 itself
lists — appears in no row above, because **PR2 froze no contract for it**. SDD §13 lists the table; §11.1 describes an override only as an API
request body and §12 only as an event payload. M0 is a freeze and may not invent a
twentieth contract to fill the gap, so the table is created now with §13's four key fields
as real columns and its rows are stored as canonical JSON documents sealed with the same
ADR-056 digest and refused-on-drift by the same store guard as every other row.

Two things about those rows are decided here rather than left implicit, because both are
identity decisions and the registry classifies a wrong one as Major and fail-closed.

**The id kind is new.** `src/accretion/ids.py` has mapped `"override" -> "ovr"` since v0.1,
and `src/accretion/planning.py` mints it for the v0.1/v0.2 **strategy** override. That kind
is taken and it means something else, so reusing it would leave an `ovr_` id unable to say
which record class or which table it names, and would hand M2's
`CanonicalContract._validate_header_and_seal` prefix check a second claimant to accept. M0
therefore adds a distinct kind, `"routing_override" -> "rov"`, and every routing override id
is minted from it.

**The document type is outside the frozen namespace.** The stored document carries
`document_type`, not `contract_type`, and its value is
`accretion.internal.routing-override-record/0` — deliberately unparseable by
`CanonicalContract.contract_type`'s `^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$`. A value of the
form `accretion.routing-override` would have looked exactly like PR2's nineteen
`CONTRACT_TYPE` values and would therefore have promised that the row validates as the
contract of that name. It does not: `CanonicalContract` requires `created_by`, which this
document does not carry, and forbids every override field it does carry. The three header
fields that are merely optional there — `objective_contract_ref`, `labels`,
`retention_class` — are no consolation, because a reader would supply them from their
defaults and validate a body the digest was not computed over. ADR-056 hashes the whole
body, so a row sealed over the smaller field set can never be rescued by adding those
fields later — the recomputed digest differs and the reader reports a record edited after
sealing. On a human-authority governance record that is a false tampering alarm. These rows
are **pre-contract records**, and they say so.

M2 owns `POST /api/v1/routing-decisions/{receipt_id}/override`. The `RoutingOverride`
contract belongs in that milestone's freeze and needs no schema migration — the columns are
already here — but it is **not an additive Minor change under registry §3.2. It replaces
this pre-contract document, and replacing it is Major and fail-closed.** The M0 row is
`document_type` plus thirteen fields; `RoutingOverride` will be a `CanonicalContract`
carrying `contract_type`, the required `created_by`, and the rest of the registry §3
header. That renames the field that names the record's kind and adds a required one, which
the locked registry classifies as Major, and `CanonicalContract`'s `extra="forbid"` means
an M0 row fails `RoutingOverride.model_validate` twice over: on the unknown `document_type`
key (and on every override field the header has no place for) and on the absent
`created_by`.

The two shapes are **not** distinguishable by `schema_version`: both stamp `1.0.0`, because
`_build_routing_override_payload` uses the same `CONTRACT_SCHEMA_VERSION` every frozen
contract declares. So the discrimination rule is written down here and is the only one:

> A `routing_overrides` row carrying **`document_type`** is an M0 pre-contract record and
> must never be fed to a contract's `model_validate`. An M2 row carries **`contract_type`**.

`tests/test_v04_m0_store.py` makes that executable rather than advisory: it asserts that a
document from `_build_routing_override_payload` raises `ValidationError` when validated as
a `CanonicalContract`. The day M2 tries to read the two shapes through one model, that is a
red test rather than a discovery at read time. What M2 must do instead is migrate or fence
the pre-contract rows deliberately — the digest is over the whole body, so no M0 row can be
made to validate as a `RoutingOverride` by adding the missing fields to it.

### The frozen shape

`routing_overrides` is the one table whose row shape lives in Python
(`store._build_routing_override_payload`) rather than in a committed JSON Schema, so it is
frozen the same way the schemas are: by a committed golden document and its digest.

| Document | sha256 of the committed file | `schema_version` | Stored in | Migration |
|---|---|---|---|---|
| `tests/fixtures/records/v0.4/routing_override/minimal.json` | `ed565b1853bceef92fdc177438f1e3320bbf5d7ae56e7278fe9d1beefb3e97de` | `1.0.0` | `routing_overrides` | `0017_v04_m0_routing_contracts` |

The file lives under `tests/fixtures/records/` and **not** under
`tests/fixtures/contracts/v0.4/`, because that tree holds exactly one directory per frozen
contract and this record is not one — putting it there would re-assert the claim the
paragraph above exists to withdraw.

Its key set is the frozen shape, in full:

`candidate_id`, `content_hash`, `contract_id`, `created_at`, `document_type`,
`principal_id`, `project_id`, `reason`, `reason_code`, `receipt_id`, `schema_version`,
`supersedes_contract_id`, `superseding_receipt_id`, `workspace_id`.

`tests/test_v04_m0_persistence_models.py` recomputes that digest, compares that key list
against a document the builder produces right now, and separately asserts that every one of
the fifteen tables is named in a "Stored in" column somewhere on this page. Renaming
`reason` to `note`, dropping `superseding_receipt_id` or adding a field is therefore a red
test and not a shape that quietly forks — which is the same protection the fourteen
committed schemas get.

## What M0 froze about storage

The fourteen tables SDD v0.4 §13 lists plus `objective_contracts`, which §7.1 requires and
ADR-058 counts — fifteen in all — one reversible migration, and no behaviour change for
v0.1-v0.3. (§13's table begins at `node_contracts`, so an auditor counting it finds
fourteen; the objective contract is the root the rest of the family references and is
stored with them rather than left without a table.) No existing table gains, loses or
renames a column; no route, no UI, no service reads or writes any of them yet.

| §13.1 constraint | How it is enforced |
|---|---|
| Contract hash/version tuples are unique | `UniqueConstraint(content_hash, schema_version)` on all fifteen tables, named `uq_<table>_hash_version`, mirrored by a pre-insert check in both stores |
| One immutable receipt per routing request ID | `routing_receipts.routing_request_id UNIQUE` (§8.2's idempotency key), mirrored by a pre-insert check in both stores so that a second receipt for one request raises the same `ValueError` on either backend rather than a `ValueError` in memory and an `IntegrityError` in PostgreSQL |
| One active workspace router per workspace | partial unique index `uq_router_versions_active_workspace` over `workspace_id` where `status = 'ACTIVE' AND scope = 'TEAM_WORKSPACE'`, mirrored in `MemoryStore` |
| One active adapter per project/router family | partial unique index `uq_router_versions_active_project_adapter` over `(project_id, algorithm_id)` where `status = 'ACTIVE' AND scope = 'PROJECT_ADAPTER'`, mirrored in `MemoryStore` |
| Promotion reports are append-only | no `update_` or `delete_` method exists for any of the fifteen tables on `StateStore`, `MemoryStore` or `PostgresStore`; a test asserts the absence |
| Evidence/experience deletion must not orphan provenance silently | every foreign key is `ON DELETE RESTRICT`; `experience_records.id` references `experiences.id` and every `project_id` references `projects.id`, and `MemoryStore` mirrors the existence half of both keys with a `ValueError` so a record PostgreSQL would refuse is refused there too |

Five of those six rules are enforced twice — once by PostgreSQL, once by a pre-insert check
in each store — and that is deliberate; the sixth, "promotion reports are append-only", is
enforced by the absence of a method that could break it, which cannot be double-checked
because there is nothing to check. The two partial unique indexes are the repository's
first. They are declared as
`postgresql_where` clauses on the `Index` objects, so they reach the migration through
`Base.metadata` like every other index rather than as raw SQL, and both stores pre-check
the rule and raise `ValueError` before the insert so that a caller sees the same error on
either backend. Each pre-check runs in the same lock (`MemoryStore`) or the same
transaction (`PostgresStore`) as the insert it guards, so a second concurrent writer cannot
slip between the check and the write; the index is the backstop for the writer that does.

Listing is scoped, not scopable. Every `list_` method on all three surfaces takes
`workspace_id` as a **required** keyword with no default — `project_id` narrows it further
— because a tenancy filter with a default is a filter a caller can omit and still
type-check, and omitting this one would return every row of a table across every workspace.

Records are written once. A revision is a **new row** whose `supersedes_contract_id` names
the row it replaces (registry §17: "historical records are never rewritten in place"), and
both rows list.

## Freeze delta (5 Sep 2026, ADR-060..064)

Everything above records what M0 froze on 5 Sep 2026 and is left exactly as it was written.
This section records the one change made to that freeze surface, the same day, before any
milestone that reads it had started: three findings from the M6-M8 design pass do not
compose with what M0 froze, and freeze-surface changes travel together or not at all.

1. **`ShadowDecision` has no observed outcome** (ADR-060). It records the configuration a
   candidate router *would* have chosen and the utility it *projected*. §10.2 gates the
   shadow stage on evidence, and a projection is not evidence. `ShadowRolloutResult` is
   what a *branched* rollout measured — one row per arm of a `SHADOW`/`CONTROL` pair, both
   forks of the same live run under the same seed policy.
2. **"One active workspace router" could only ever fire once** (ADR-061). The partial
   unique index `uq_router_versions_active_workspace` plus this family's deliberate absence
   of any `update_` method means the first `ACTIVE` row can never be retired and a second
   can never be inserted. `RouterActivation` moves the rule onto an append-only ledger
   whose head is the active version. **The two partial indexes above are untouched by this
   delta**; M8.1's migration 0019 retires them, so a database between 0018 and 0019
   satisfies both rules at once and each migration is independently reversible.
3. **`ObjectiveContract` had no exploration budget** (OQ-410, ADR-062). M7's safety
   inequality needs an α and absolute caps that the objective's approver set, not that the
   router chose for itself. `exploration_policy` is additive, optional and defaulted to
   `None` — registry §3.2 **Minor**.

### The Minor bump on `ObjectiveContract`, and why three digests moved

Adding an optional field is a Minor change: every document written before it existed still
parses, and `schema_version` stays `1.0.0` under registry §3.2's rule that only a
remove/rename is Major. It is not, however, digest-neutral, and the distinction matters
enough to write down. ADR-056's canonical form **keeps nulls** — dropping them would make
`{"a": null}` and `{}` collide, and those say different things about a field — so
`exploration_policy: null` is part of the body of every `ObjectiveContract` sealed from now
on. A byte-identical objective therefore seals to a different `content_hash` than it would
have yesterday.

What follows from that, and what does not:

- The four committed `tests/fixtures/contracts/v0.4/objective_contract/` documents were
  **re-sealed** — the same bodies, the new digests — because a golden fixture that no
  longer verifies against its own body is not golden.
- `ObjectiveContract.schema.json` was regenerated and its row appears below with the new
  digest. The row in "The frozen schemas" above is left at the M0 bytes on purpose: that
  table is the record of what was frozen at M0, and rewriting history to match the present
  is the failure mode a freeze record exists to prevent. The tests read this table as an
  overlay on that one.
- **A row sealed before this delta no longer parses**, and the honest way to record that
  is to say it rather than to reason around it. The read boundary re-derives the digest
  over the body as the field-bearing model dumps it — `exploration_policy: null` included
  — so a pre-delta document presented with its own pre-delta `content_hash` is refused as
  tampered: `content_hash 'ed2c…265c' does not match the digest of this payload
  ('f27d…febb'); the record was edited after it was sealed`. That is a `ValidationError`
  out of `get_objective_contract` on either backend, and out of
  `list_objective_contracts` for the whole page rather than for the one row. This PR's own
  diff is the evidence: `objective_contract/minimal.json` gained no `exploration_policy`
  key and still needed a new digest.
- **No such row exists in the field**, which is why the delta is allowed to be taken now
  and not later. Migration `0017` is on `develop` and in no release — `v0.3.0` ends at
  `0016` — and no code outside the test suite writes an `objective_contracts` row, so the
  only databases holding one are developer databases already at `0017`, and those are
  recreated rather than migrated. The general case belongs to M8: registry §20.5's
  read-boundary upcaster is where a document sealed under an older shape is migrated
  forward *before* it is validated, and this delta deliberately does not open that door
  early. `tests/test_v04_freeze_delta.py` pins both halves — the unsealed pre-delta body
  parses, the sealed one raises — so the upcaster inherits a checked statement of the case
  it has to answer.

### The frozen schemas, as amended

| Schema | sha256 of the committed file | `schema_version` | Stored in | Migration |
|---|---|---|---|---|
| `ObjectiveContract.schema.json` | `66d8481faadb0429c7284d89ca03460e9e8fff8105abd47c93385de4f9bb0f16` | `1.0.0` | `objective_contracts` | `0017_v04_m0_routing_contracts` |
| `ShadowRolloutResult.schema.json` | `6ded393425ad8cdeffa5d7e519d368f43c857d96e6b4d6c463403e8f5f5165ef` | `1.0.0` | `shadow_rollout_results` | `0018_v04_freeze_delta` |
| `RouterActivation.schema.json` | `faee20c3c32e317731fcd1ffdfb73abf2c416797a80968544dfd1206389d9b55` | `1.0.0` | `router_activations` | `0018_v04_freeze_delta` |

Twenty-one committed schemas now, sixteen of them stored in a table of their own.
`CONTRACT_INVENTORY` grows 19 → 21 and the v0.4 table list 15 → 17. The two new prefixes
are `shr_` and `rac_` (ADR-055's registry, three characters wide).

### What the delta froze about storage

Migration `0018_v04_freeze_delta` (the file is
`migrations/versions/0018_v04_freeze_delta_shadow_rollouts_router_activations.py`; the id is shorter
because Alembic stores it in a `VARCHAR(32)`) creates
`shadow_rollout_results` and `router_activations` additively and reversibly, and touches
nothing else. `V04_M0_ROUTING_TABLES` keeps its name and grows to seventeen — every parity
proof in the suite is written against it — and `V04_FREEZE_DELTA_TABLES` names the two that
0017 subtracts from its own creation list, so that a revision already applied in the field
still creates exactly the fifteen it always created.

| Constraint | How it is enforced |
|---|---|
| Contract hash/version tuples are unique | `uq_shadow_rollouts_hash_version` and `uq_router_activations_hash_version`, on the same pre-insert path as the other fifteen |
| One activation per (workspace, scope, family, sequence) | `uq_router_activations_sequence`, an ordinary `UniqueConstraint` and not a partial index: once "active" is a position in a sequence rather than a value in a column, the rule is unconditional |
| A `ROLLBACK` states what it restored and why | `RouterActivation`'s validator, which refuses a `ROLLBACK` missing `cause` or `rollback_target_version_id` |
| A verified rollout names its verification | `ShadowRolloutResult`'s validator, which refuses `observed.verified` without a `verification_result_id` |
| Append-only | unchanged: no `update_`, `delete_`, `upsert_` or `set_` method exists for either table on any of the three surfaces, and the same test asserts the absence over all seventeen |

### The decisions this delta records

ADR-060 (`ShadowRolloutResult` and branched rollouts), ADR-061 (the activation ledger
replaces status mutation), ADR-062 (the bandit safety inequality and where α comes from),
ADR-063 (the promotion gate as calibrated safe policy improvement), and ADR-064 (the
research artefacts and the locked-test access rule) are written in
[`docs/sdd/Accretion_SDD_v0.4.md`](../../sdd/Accretion_SDD_v0.4.md) §21, and §22 now carries
a decision for sixteen of the twenty open questions. The sixteenth is OQ-409, and its cell
is a decision about how to proceed rather than an answer: minimum shadow evidence stays
**open**, blocked on the §21 protocol freeze, and the cell records the *interim rule* the
M6 gate runs under until the power analysis lands — at least nine paired runs per
configuration per node class. A cell that had been left empty would have said the question
was untouched; one that read as settled would have said a number nobody has computed is
final. It says neither.
