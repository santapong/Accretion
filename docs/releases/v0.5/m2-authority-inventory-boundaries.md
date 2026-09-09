# M2 current authority inventory

This implementation adds transaction-bound policy eligibility and current
conformance checks. It installs no deployment trust root, real permission grant,
adapter attestation or episode approval. Synthetic tests establish storage and
authorization behavior, not adapter conformance or AC5 acceptance.

## Durable scope and attribution

Migration `0023_v05_authority_inventory` owns two internal tables:

| Internal record | Exact identity and purpose |
|---|---|
| `RuntimePolicyGrant` | Workspace, project, actual initiating human and service orchestrator; exact policy version/content, capability versions/content and operator permissions |
| `RuntimeConformanceAdmission` | Workspace, project, exact adapter contract and complete dependency closure; exact report, independent verifier/suite, original attestation bytes and digest |

These are internal runtime DTOs, separate from the frozen canonical robotics
family. Each installation has a validity interval of at most 24 hours, a current
disposition, creator/update attribution and a checked revision. Current readers
also verify the original installation stream and the exact event containing the
current inventory snapshot. Missing, corrupt, disabled, expired, revoked or
quarantined authority refuses use.

`AuthorityInventory.install_policy` and `install_conformance` require an
authenticated active human maintainer in the current project/workspace. The
policy grant's human/operator and service/orchestrator must also be active scoped
members. Updates and revocations require the current revision; all writes require
an idempotency key. Same-request replay returns a historical installation receipt;
it never reactivates a later revoked row. A maintainer can revoke an earlier
installer's grant even after that installer has been disabled.

The compatible event extension adds only
`simulation_authority.policy_changed` and
`simulation_authority.conformance_changed`. The original first installation
`SimulationDomainEvent` anchors each stream in `robotics_contracts`; subsequent
events correlate to that immutable anchor. This preserves the existing event
aggregate foreign key. Inventory rows reference their stream with
`event_stream_id`, while their deterministic scoped inventory ID remains separate.
No historical writer, schema fixture, source archive or evidence seal is rewritten.

## Policy and conformance providers

`InventoryPolicyAuthority.check(tx, check)` resolves the actual persisted Run,
Task and SimulationRunBinding, including the initiating human and exact service
orchestrator. Simulation remains an EXPERIMENT/HIGH operation on a real
DETERMINISTIC run. The provider reads exact governance rows in the same
transaction, checks their full writer definition and indexed fields, and rejects
missing/default-filled/coerced definitions. New policy versions never silently
replace the pinned version.

The provider reuses `CapabilityPolicyEngine.authorize(..., approval=None)` for
enabled status, explicit policy/task denial, allowed capability, operator
permission and declared idempotency checks. It deliberately accepts
REQUIRE_APPROVAL only as internal policy eligibility. Its synthetic metadata
CapabilityRequest is never submitted to an executor. The existing
SimulationAuthority still requires and atomically consumes the exact authenticated
SimulationEpisodeApproval and checks the lease, current state, signed issuance,
budgets and one-time dispatch. No legacy ApprovalRecord is fabricated.

The frozen capability family contains `robotics.sim.inspect`, `observe`,
`propose_action`, `reset`, `snapshot` and `terminate`. Binding/allocation,
heartbeat, preflight and approval transitions require eligibility for every
manifest-declared capability. Protocol reads/reset/propose/snapshot/termination
map explicitly to the corresponding capability. FINISH_DISPATCH selects RESET
or EXECUTE from the exact successful protocol response; unknown operations deny.

`InventoryConformanceAuthority(attestor=...).check(tx, check)` requires the
newest registry-linked report for the exact dependency closure, its original
seal and exact conformance event, a complete PASS, independent current principals,
the admitted manifest/descriptor/model/schema pins, and an active matching
inventory disposition. A newer FAIL for that closure invalidates older PASS;
another closure cannot substitute. Quarantined host resources also refuse use.

Deployment must supply `CurrentConformanceAttestor.verify(tx, report_envelope,
attestation_original_json=..., now=...) -> AttestationValidity`. This trusted
implementation must authenticate the exact report, closure, suite, verifier and
admitted independent runner against current deployment trust/revocation. It may
perform bounded local verification and retained reads through `tx`; network,
host/artifact IO, nested transactions, signing and caller-provided trust roots are
outside this boundary. The bounded original proof is at most 16 KiB. Attested
validity can shorten the inventory interval; it cannot extend it. No production
attestor or key is included in this change.

## Deadline propagation and atomic rollback

`RuntimeTransaction.require_valid_interval(valid_from, valid_until, code)` records
the intervals checked by configured providers. `authority_valid_until` exposes
their earliest expiry. Transaction exit performs one final authoritative clock
read after every awaited collaborator and write; an expired interval rolls back
the inventory/runtime rows, event anchor/revision, events and idempotency ledger.
This also protects BIND_RUN before a lease exists.

Fresh dispatch reservations retain `authority_valid_until` in their internal JSON
DTO. The optional field preserves readability of earlier rows; configured
inventory checks always register a finite deadline. The host must cap its
one-use operation permit by this value and all lease/heartbeat, preflight,
approval and safety deadlines, and enforce it at invocation. A historical
reservation or timestamp is never a permit. Revocation must be checked again on
every fresh operation; commit-to-IPC is not instantaneous.

For initialization/read integration, `checked_inventory_deadline(tx, check,
policy=..., conformance=...) -> datetime` rechecks both providers in the caller's
transaction and returns the same minimum. Callers still need the owning host,
lease and appropriate approval/preflight checks. No operational authorization is
inferred solely from this deadline helper.

## Reproducible validation

Use an isolated `uv sync --frozen --group dev` environment and a disposable
PostgreSQL database migrated through 0023. Set `ACCRETION_DATABASE_URL` for
Alembic and `ACCRETION_TEST_POSTGRES_URL` for pytest to that same database. Run
migration witnesses serially with other tests using that database:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen --group dev pytest \
  -p pytest_asyncio.plugin tests/test_v05_authority_providers.py \
  tests/test_v05_authority.py tests/test_v05_lifecycle.py \
  tests/test_v05_authority_inventory_migration.py \
  tests/test_v05_authority_migration.py tests/test_v05_registry_migration.py \
  tests/test_v05_contracts.py
```

Provider witnesses cover both MemoryStore and PostgreSQL, refusal rollback,
revocation serialization, installation idempotency/revisions, exact original
policy/capability bytes, missing permissions, disabled principals, stale report
lineage, missing event provenance, absent/revoked attestors, quarantine and late
expiry after writes or a later collaborator. Migration tests downgrade children
before parents inside rollback-only transactions and preserve pre-existing rows,
foreign keys and uniqueness constraints. Ordinary imports require no simulator.

Observed on 2026-09-09 in the isolated authority-providers worktree and its
disposable `accretion_v05_sdk` database: the command above passed **463 tests**,
with two intentionally inapplicable RESET/signed-safety-clock cases skipped.
The provider suite contributes 96 Memory/PostgreSQL cases. Ruff and mypy for the
changed authority sources passed; ordinary import passed with MuJoCo absent.
These are local candidate results, separate from protected integration/CI.
