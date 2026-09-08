# Runtime gap audit for the v0.4 completion plan

Checked 2026-09-08 against `8ded8bab151267ec8e06182f52543dabed6eaca7` in an isolated
`docs/v04-runtime-audit` worktree. This is a source and test inspection, not a new acceptance
run, provider experiment, deployment audit or finding of an observed production incident.
The release-status audit owns the shipped v0.4.1 baseline. This report identifies the narrow
engineering work a completion plan should consider without reopening every delivered milestone.

The recommended engineering lane is **reproduce and resolve M7 budget-admission gaps**.
The other required activity is correcting contradictory descriptions of existing behavior.
Pin-aware AGENT tool sessions are a separately scoped capability expansion; they must not be
silently called a v0.4.2 patch. Read-boundary upcasting already exists, and the four surviving
legacy digest formats must remain compatible until a separately designed migration exists.

## Evidence and classification

| Finding | Classification and implication | Source inspected |
|---|---|---|
| AGENT configurations carrying selected tools are rejected before the durable dispatch claim or session creation. | **Implemented release limitation.** Do not remove this guard as a completion shortcut. The later executing-provider seam did not implement tool pinning. | [RunManager guard](../../../../src/accretion/services/run_manager.py#L1615), [negative test](../../../../tests/test_v04_m2_dispatch.py#L447), [release limitation](../notes.md#L164) |
| Selected TOOL nodes already execute through an exact binding check and the existing policy gateway. | **Implemented foundation.** Reuse it for any new AGENT boundary rather than introducing a second policy authority. | [invoke_selected](../../../../src/accretion/governance.py#L904), [binding drift tests](../../../../tests/test_v04_m2_selected_tools.py#L23) |
| M7 replays stored EXPLORE receipt charges into a fresh registry. | **Implemented recovery.** The release notes/audit's statement that restart forgets prior spend is incorrect. Losing the in-process object is not losing every charge. | [LedgerRegistry](../../../../src/accretion/routing/bandit.py#L322), [backend replay parity test](../../../../tests/test_v04_m7_postgres_store.py#L247), [inaccurate notes](../notes.md#L159) |
| PostgreSQL routing transactions lock per run, while exploration caps apply to workspace and node class. | **Source-supported concurrency defect candidate; not runtime-reproduced here.** Different runs can admit against the same remaining capacity before either receipt commits. Reproduction is the first work package gate. | [route transaction](../../../../src/accretion/routing/service.py#L460), [admission and labels](../../../../src/accretion/routing/bandit.py#L442), [PostgreSQL lock key](../../../../src/accretion/persistence/store.py#L3907) |
| Settlements are volatile and may increase a charge above the original UCB. Restart reconstructs the UCB alone. | **Source-supported restart defect candidate; not runtime-reproduced here.** The claim that restart always produces a tighter budget requires an assumption the implementation does not enforce. | [settle accepts any normalized observation](../../../../src/accretion/routing/ledger.py#L252), [replay charge parser](../../../../src/accretion/routing/bandit.py#L846), [settlement normalization](../../../../src/accretion/routing/settlement.py#L50) |
| Missing or malformed EXPLORE charge labels are skipped during replay. | **Deliberate behavior requiring a negative admission case.** Not inventing a cost is correct; treating unreadable spend as a clean budget needs a fail-closed policy. | [parser](../../../../src/accretion/routing/bandit.py#L846), [parity test expects skipping](../../../../tests/test_v04_m7_postgres_store.py#L257) |
| The implemented cap is cumulative over workspace and node kind, without a run or calendar-period key. | **Contract/description mismatch, not proof of an overspend.** With the same bounds, an all-history cap may be stricter than the documented run/day cap. It does not establish separate run/day or currency-budget guarantees. | [key](../../../../src/accretion/routing/bandit.py#L268), [cap definition](../../../../src/accretion/routing/ledger.py#L58), [ADR-062](../../../sdd/Accretion_SDD_v0.4.md#L1280) |
| Both stores load v0.4 records through the upcaster and preserve stored payloads. | **Implemented M8 behavior.** Do not schedule creation of an upcaster. | [store loader](../../../../src/accretion/persistence/store.py#L400), [upcaster](../../../../src/accretion/contracts/upcast.py#L287), [no-writeback test](../../../../tests/test_v04_m8_upcast.py#L530) |
| Several routing reference checks compare projected hashes after an upcast. | **Source-supported forward-compatibility defect candidate.** A valid newer-minor record may be refused; no current-schema production failure is established. Validate a complete reference chain before expanding the fix. | [writer-versus-reader identity rule](../../../../src/accretion/contracts/upcast.py#L80), [spec comparison](../../../../src/accretion/routing/service.py#L432), [node reference comparison](../../../../src/accretion/routing/service.py#L950) |
| Three digest sites converged, four deliberately retain old bytes. | **Completed bounded migration plus explicit deferral.** Remaining rehashing is not routine cleanup. | [digest authority and four domains](../../../../src/accretion/digests.py#L34), [M8 outcome](../backlog.md#L62) |

The backlog sentence claiming the AGENT limitation was superseded must be narrowed to the
executing-provider seam ([backlog lines 450 onward](../backlog.md#L450)). Conversely, its M7
receipt-replay description is closer to current behavior than the release notes, but its
unconditional “tighter after restart” conclusion also needs qualification.

## Budget authority that exists today

1. The approved `ObjectiveContract.exploration_policy` supplies `alpha`, `max_explore_count`
   and `max_cost`. For old sealed objectives, all three `exploration.*` labels are required
   together on the approved revision; missing policy fails closed. See
   [policy resolution](../../../../src/accretion/routing/bandit.py#L237).
2. `CostLedger.can_explore` checks count, cumulative charged cost and the conservative
   inequality. All three checks read the **same ledger state**. There is no separate durable
   absolute-cap counter rescuing a stale admission. See
   [can_explore](../../../../src/accretion/routing/ledger.py#L157).
3. A registry key is `(workspace_id, node.node_kind.value)`. Its replay reads the workspace's
   receipts without a run/day filter. Costs are normalized fractions of node caps, not money;
   `max_cost` may exceed one because it sums these fractions. See
   [unit definition](../../../../src/accretion/routing/ledger.py#L32).
4. Task execution separately tracks wall time, turns, tool calls, loop iterations and parallel
   runs. These are useful bounds, but not a monetary exploration account. See
   [TaskBudgets](../../../../src/accretion/contracts/__init__.py#L1257) and
   [durable run counters](../../../../src/accretion/services/run_manager.py#L1091).
5. The bandit and settlement hook share a registry only in `AUTO`. The existing baseline
   posture and shadow behavior must remain unchanged by hardening. See
   [bootstrap](../../../../src/accretion/routing/bootstrap.py#L122).

## Work package E0 — reconcile engineering claims

**Owner:** documentation/release integration lane. **Scope:** documentation corrections,
not a reopened v0.4 milestone or a new empirical claim.

- Correct current navigation and release-facing descriptions of M7 restart recovery, cap
  units/scope, and AGENT pinning. Preserve tagged baseline records as historical evidence;
  use an explicitly dated correction/addendum where an immutable record is affected.
- Record whether the completion target means the already shipped v0.4.1 scope or a new
  maintenance release. A plan can call the existing milestone program delivered while
  still recording defects found after release.
- Keep per-run/day semantics as an explicit decision. Do not silently add reset periods to
  the existing sealed policy: resetting a cumulative cap changes allowed behavior.

**Files:** `docs/releases/v0.4/{backlog,notes,audit,README}.md`, the documentation hub and the
relevant runbook; any normative clarification follows the SDD amendment process.
**Exit:** each live limitation has one accurate description and links to a work package or
an explicit deferral. `make docs-check` passes. This audit does not edit those shared files;
the parent plan owns their integration.

## Work package E1 — reproduce and harden M7 admission

**Owner:** one budget lane; do not assign `routing/service.py`, `routing/bandit.py` and
`persistence/store.py` to competing agents. **Priority:** first implementation candidate.
**Version:** a narrowly scoped safety/consistency repair may be a maintenance release after
reproduction and compatibility review. New budget scopes or resetting policy belong to a
separate contract decision.

### E1a: reproduce before choosing storage changes

Use synthetic fixtures and local test dependencies. Do not read locked research corpora,
fit a router, invoke providers or start production services to establish these cases.

| Required test | Meaningful failure it must expose |
|---|---|
| Two distinct runs, same workspace/node class, final count slot, two PostgreSQL store instances; barrier after both have read admission state | At most one EXPLORE receipt commits. The other gets a deterministic refusal; different run IDs must not buy separate remaining capacity. Repeat against the cost boundary and inequality, not only the count. |
| Retry the same routing request concurrently and after a simulated commit-response loss | One receipt and one charge; no second draw/charge or duplicate dispatch. |
| Failure between admission and receipt/event commit | No orphan charge, event or success response; a rollback releases the reservation/lock. |
| Settle above the booked UCB, then reconstruct the registry | A known cost increase cannot disappear and permit an otherwise refused next exploration. For example, 0.25 booked then 0.8 observed, a 0.3 next charge and a 1.0 cap: the measured state refuses 1.1; UCB-only restart would permit 0.55. This is arithmetic implied by the code, not a test run reported here. |
| Settle below the booked UCB, restart before/after settlement acknowledgement, then redeliver the same outcome | No double credit; replay remains deterministic, count never decreases, and uncertainty retains a charge. |
| Matching-class EXPLORE receipt with absent, nonnumeric, nonfinite or out-of-range charge labels; missing class label separately | The receipt remains available as evidence, but uncertain accounting prevents new exploration in the affected scope. A parser skip must not turn unknown spend into budget. |
| Same class in different workspaces; different classes in one workspace | No accidental cross-scope charging; concurrency fixes must not serialize unrelated work unnecessarily. |
| Database read failure or malformed settlement provenance | Baseline/refusal with a diagnostic, never successful exploration from an empty replacement account. |

The existing PostgreSQL parity test proves replay ordering and snapshot agreement; it does
not exercise simultaneous admission. `MemoryStore` has a global transaction lock, which can
hide the cross-run PostgreSQL race ([MemoryStore transaction](../../../../src/accretion/persistence/store.py#L1363)).
Use an explicitly configured disposable test database; a skipped PostgreSQL test does not
close this gate. This planning audit did not create or start a database.

### E1b: smallest safe implementation after reproduction

- Make budget reconstruction, admission and receipt publication one serialized operation
  under the actual budget key. Prefer a workspace/node-class transaction-scoped lock and
  existing immutable receipts over a second totals table merely because the pure ledger is
  in memory. Define a consistent lock order with the existing run lock. Pass the transaction
  store/read context through the behavior stage; its current registry reads the root store.
- Keep a reservation distinct from execution. A committed receipt does not prove a tool or
  provider ran. Uncertain dispatch keeps the reservation until existing reconciliation
  rules allow a documented release; do not add automatic retry.
- Decide settlement recovery from evidence. First assess whether validated durable
  `ExperienceRecord` outcomes can reconstruct a stable settlement with receipt/execution
  identity and defined revision semantics. If not, add an append-only settlement fact with
  a unique receipt/event identity. A cache or totals row alone is not the authority.
- Persist or reconstruct observed cost increases, and make a bound exceedance visible.
  Do not cap an actual observation at the prediction just to retain the inequality. The
  current `[0,1]` normalization and actual currency spend are different measures; the
  completion claim must name the measure the guard enforces.
- Treat unreadable relevant accounting as unavailable. Preserve malformed historical
  records and a diagnostic; do not rewrite their sealed labels or assume zero.
- Preserve current all-history scope for a narrow repair unless the contract decision
  explicitly approves another policy. A future run/day scope needs an immutable scope key,
  UTC boundary rule, approval/version ownership and mixed-version behavior.

**File ownership:** `src/accretion/routing/{ledger,bandit,settlement,service,stages,bootstrap}.py`,
`src/accretion/persistence/store.py`; `persistence/models.py`, Alembic migrations and contract
export tooling only if a new durable fact is justified. Tests belong with
`tests/test_v04_m7_{ledger,bandit,postgres_store}.py` plus narrowly named admission tests.
Inspect and update protocol/checksum expectations if the store/stage contract changes.

**Compatibility and rollback:** no rewriting historical receipts, no changing their digests,
no new exploration authorization from an absent policy. Additive storage, if needed, needs a
fresh-store and upgrade test, idempotent backfill/replay, interrupted migration handling and
an explicit downgrade rule. Do not permit an older AUTO writer to ignore new reservations or
settlements: gate it off or require a compatible writer before rollback. Disabling AUTO
protects new decisions without erasing old charges. Preserve routing-flag-off golden traces.

**Exit:** each reproduced failure has a failing-before/passing-after regression; two real
PostgreSQL connections prove cross-run admission, restart and rollback behavior; both stores
agree on accounting outcomes; targeted checks and repository acceptance/release gates pass
on the final integrated candidate. No assertion of completed repair is made by this plan.

## Work package E2 — validate stored identity across upcast references

**Owner:** contract/store lane after E1, or a non-overlapping test-only preparation in
parallel. **Priority:** bounded compatibility validation, not a blanket migration.

The upcaster verifies the writer's seal, drops an explained newer-minor field, records the
drop and seals the projection. Thus writer hash H1 and reader hash H2 may legitimately
differ. Direct read tests are already present; the missing proof to seek is a complete
stored reference chain through routing/dispatch. The service currently compares the
referenced spec and node's projected hashes. MemoryStore's receipt-by-graph lookup also uses
projected node hashes ([lookup](../../../../src/accretion/persistence/store.py#L1274)), whereas
PostgreSQL joins stored indexed hashes ([join](../../../../src/accretion/persistence/store.py#L3940)).

1. Construct a valid linked newer-minor objective/spec/node/receipt example independently
   of the upcaster, add only an understood optional-field evolution, and read it through
   both stores and reference validation. No production payload edits or corpus regeneration.
2. If it fails, expose verified stored identity alongside the reader projection, or add a
   narrow stored-reference verification method. Compare pins with the writer's verified
   seal; use projections for understood semantics. Do not remove integrity checks or
   treat arbitrary labels as authoritative stored digests.
3. Classify evolutions that are safe to explain/read versus safe to execute. Newer fields
   with unknown execution significance must remain refused. Upcast success alone is not
   permission to dispatch a configuration the adapter cannot implement.

**Files:** `contracts/upcast.py`, `persistence/store.py`, `routing/service.py`; fixtures under
`tests/fixtures/contracts/v0.4-upcast/` and tests in `test_v04_m8_upcast.py` plus routing tests.
Do not compete with E1 for the shared store/service files. No schema/data migration should
be assumed necessary before the failing linked case exists.

**Negative gates:** unknown major, unknown current-version key, unsealed/tampered body,
wrong workspace/reference and a substituted writer hash all fail closed; repeated reads and
API responses never write the projection back. Preserve current schema behavior, golden
hashes and immutable rows. A safe refusal of a not-yet-supported evolution is a documented
compatibility limitation, not evidence that the shipped current-schema path is broken.

## Work package E3 — separately approve pin-aware AGENT sessions

**Owner:** runtime/MCP feature lane. **Version:** new behavior; use a separate feature scope
and semantic-version decision, normally the next appropriate minor, rather than presuming
it is required for closing v0.4.1. This package is a reviewable design, not execution authority.

`SessionConfig` currently carries only allowed/denied capability names
([contract](../../../../src/accretion/contracts/__init__.py#L1735)). The stdio gateway lists
task-wide capabilities and resolves them again at each call
([list](../../../../src/accretion/mcp_gateway.py#L85), [call](../../../../src/accretion/mcp_gateway.py#L117)).
It receives a run identity, not a durable exact session configuration. Provider attribution
and correct runtime selection do not bridge that missing boundary.

1. Define a typed, versioned routed-session binding manifest: receipt and configuration
   identity/digest, run, execution instance, workspace, principal, executing provider,
   allowed selected `ToolBinding` entries and expiry/revocation semantics. Bind it to the
   claimed dispatch and persist the association before exposing tools. Pass an opaque
   reference through adapter startup; do not trust provider-supplied pin fields or put
   credentials in the manifest.
2. Make gateway `tools/list` expose only the selected set intersected with current policy;
   `tools/call` resolves the persisted session identity, checks current authorization and
   the exact selected implementation, then enters the existing `CapabilityGateway`.
   Reuse/extract `invoke_selected` checks without duplicating authority or bypassing
   approvals, credential brokerage, idempotency and side-effect reconciliation.
3. Implement adapter propagation and restart/resume validation for each explicitly
   supported runtime. Codex and Claude currently construct per-run gateway environments
   ([Codex](../../../../src/accretion/runtimes/codex.py#L208),
   [Claude](../../../../src/accretion/runtimes/claude.py#L228)). Opencode explicitly refuses
   governed capabilities ([refusal](../../../../src/accretion/runtimes/opencode.py#L261));
   leave it unsupported until a genuinely isolated pin-aware gateway exists.
4. Replace the blanket AGENT rejection only for adapters advertising and proving the new
   boundary. Unsupported adapters, absent manifests and drift continue to refuse before
   tool execution. Preserve the existing no-tool/flag-off path.

**File ownership:** `contracts/__init__.py` and relevant versioned refs, `services/run_manager.py`,
`mcp_gateway.py`, `governance.py`, `runtimes/{codex,claude,fake,opencode}.py`, persistence only
for the manifest/session association, generated contracts and adapter protocol tests.
Coordinate shared contract/store changes with E1/E2; do not run three conflicting merges.

**Required tests:** positive exact binding with a deterministic local fake gateway;
binding/version/implementation digest drift; same capability with a competing binding;
principal revocation; foreign workspace/run/session; a task-allowed but unselected tool;
provider-forged receipt or arguments; stale/expired manifest; resume on changed configuration;
parallel sessions without tool-set leakage; crash after claim but before session publication;
crash after side effect before response; approval-required and idempotent retry paths; no
double invocation from repeated MCP request IDs. Use adapter request recordings and local
fakes first. A real-provider test requires its own configured, bounded opt-in run.

**Rollback/exit:** disable the feature for new sessions, revoke affected manifest sessions
without rewriting receipts, retain uncertain-operation evidence and require reconciliation.
Old binaries must refuse unsupported manifests rather than falling back to capability IDs.
Generated contracts, meaningful negative tests, flag-off compatibility, backend parity and
the scoped acceptance criteria must pass before a limited provider pilot can be considered.

## Explicitly deferred work and integration order

The four legacy digest formats cover approval IDs, template checksums, remote MCP discovery
snapshots and normalized workflow hashes. They are already persisted and compared. Keep
their non-ASCII fixtures and exact old outputs. A future convergence needs an algorithm/version
identifier, old and new verification rules, referential inventory, immutable rehash mapping,
migration/rollback and outstanding-approval treatment. The current upcaster does not supply
that migration merely by existing. This is an optional separate package.

Recommended order: E0 and E1a first; E1b only for reproduced gaps; E2's linked-reference
validation after the shared-store lane stabilizes; E3 only after a separate feature/version
decision. A docs-only agent can prepare E0 concurrently with E1, and a test-only agent can
prepare E2 fixtures, with one integrator owning final acceptance. Do not widen the lane into
a paid pilot, new locked-corpus read, new routing policy, v1.x implementation or physical work.

Validation of this planning artifact is limited to source-reference inspection, local-link
resolution and `git diff --check`. No broad application tests, external effects or research
results are claimed. Implementation exit gates above are requirements for future work.
