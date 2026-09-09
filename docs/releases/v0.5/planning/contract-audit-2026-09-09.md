# v0.5 contract and authority planning audit

Date: 2026-09-09 (Asia/Bangkok). Status: **proposal for the v0.5 plan; no
implementation or release acceptance is claimed.** Inspected Git baseline:
`01e2268b3b602a12eeaf81f55fb4670a1fe7636f`, the integrated presentation refresh
on `develop`. This report was prepared in an isolated documentation worktree.
Source line references below refer to that baseline.

The inherited entry gates have recorded satisfied dispositions. The main M0
work is reconciling the forward design with the existing authority and contract
boundaries. Copying its example YAML directly would create incompatible IDs,
hashes, events and approvals. v0.5 can be planned now; its simulator, adapter,
lease, safety and embodied verification behavior still needs implementation
and its own acceptance evidence.

## Scope and evidence

The full [v0.5 SDD](../../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md),
[Golden Direction](../../../sdd/future/v0.4-v1.0/01_GOVERNANCE/Accretion_Golden_Direction_v0.4.md)
and [cross-release registry](../../../sdd/future/v0.4-v1.0/01_GOVERNANCE/Accretion_Cross_Release_Contract_Registry_v0.4_to_v1.0.md)
were read, together with current v0.4 ADRs, contracts, relevant persistence and
execution code, and the v0.4 handoff/closure records. The v0.5 SDD is the release
scope; old robotics-charter proposals do not replace it. Permanent authority
invariants still apply. The frozen imported files remain reference artifacts:
after approval, create an active v0.5 SDD and a clearly scoped registry/ADR
overlay; do not repair frozen examples in place.

**Fresh inspection:** source, recorded artifacts and clean worktree state at the
baseline above. **Historical execution:** the commands and results recorded by
v0.4 closure and dependency integration. No backend tests, migration cycle,
scientific corpus read, simulator campaign, provider call or physical action
was performed for this planning audit. Code inspection establishes the seam
that exists, not that a future robotics path has executed successfully.

Milestone labels in this report map the ten numbered steps in SDD §21 to
**M0–M9**: freeze; registry/SDK; gateway/leases; safety; orchestration/recording;
verification/replay; flagship adapter; second adapter; Studio; benchmark/release.

## Entry-condition matrix

| Gate | Evidence at the inspected baseline | Planning disposition | Required execution check for a new candidate |
|---|---|---|---|
| E1: v0.1–v0.3 gates passed | [v0.1 audit](../../v0.1/audit.md), [v0.2 audit](../../v0.2/audit.md), [v0.3 baseline](../../v0.3/baseline.md); reconciled in [handoff](../../v0.4/research-handoff-2026-09-08.md), lines 60–84 | Satisfied by recorded release evidence; retain the explicit historical v0.2 browser exception | Preserve immutable tags; run current applicable regression/release gates and check manual evidence expiry |
| E2: routing claim demonstrated or documented no-go | [Adopted scoped NO-GO](../../v0.4/research-handoff-2026-09-08.md), lines 1–49, retained PARTIAL paired synthetic evidence | Satisfied. A favorable routing result or live-provider pilot is not an entry prerequisite | No new locked-corpus read; use a distinct prospective v0.5 protocol for new claims |
| E3: v0.4 migrations stable | [Closure record](../../v0.4/closure-execution-2026-09-08.md), lines 81–122; isolated upgrade → base downgrade → upgrade and full suite on `f8b47a8`; later [dependency review](../../v0.4/dependency-review-2026-09-09.md) preserves schemas/migrations | Satisfied for the recorded integrated candidates; current baseline contains the closure | Fresh serial migration/preservation tests on the new candidate, with a dedicated disposable database |
| E4: gateway denies undeclared capabilities | [Gateway](../../../../src/accretion/governance.py), lines 114–175 and 378–467, checks registry version, task allow/deny, policy and principal; handoff names executed negative witnesses | Existing digital/fake condition satisfied; robotics endpoint isolation is unimplemented | Prove unknown, undeclared, disabled, foreign-scope and physical endpoints cause zero adapter invocation |
| E5: independent PASS/FAIL/INCONCLUSIVE | [Recorder](../../../../src/accretion/feedback/verification.py) and [integrated fake summary](../../v0.4/closure-evidence-2026-09-09/fake-pilot-summary.json); closure includes verifier tests | Existing condition satisfied; this does not satisfy v0.5's stricter process/identity requirements | Run separate embodied task/safety/completeness verifiers; prove producer identity cannot submit acceptance |
| M0 program unlock | SDD still resides under `future`; this request authorizes a multi-agent plan | Plan review remains the transition into implementation | Record approved scope, active SDD/registry overlay, ADR decisions and measurable acceptance ownership before implementation waves |

The [handoff](../../v0.4/research-handoff-2026-09-08.md) explicitly separates
entry satisfaction from authorization for a new program. The [closure record](../../v0.4/closure-execution-2026-09-08.md)
records the subsequent protected integration. Neither record is positive
simulation evidence or permission to run a paid study.

## M0 decisions that block independent implementation

These are proposed decision topics, not allocated permanent ADR numbers. Assign
unique numbers in the active SDD after checking the complete ADR inventory.

| Decision | Concrete mismatch or missing definition | Recommended default | Alternative and condition | Owner / dependent milestones |
|---|---|---|---|---|
| D01: canonical ownership and identity | SDD §7 uses UUIDs/PascalCase types and omits `workspace_id`. [CanonicalContract](../../../../src/accretion/contracts/canonical.py), lines 289–460, requires the shared workspace header and lowercase canonical type; [ADR-055](../../../sdd/Accretion_SDD_v0.4.md#21-architecture-decisions) adopts prefixed base32 IDs | Inherit the existing header and canonicalization. Assign unique ID kinds in `ids.py`; distinguish immutable descriptor/adapter logical names from persisted contract IDs. Preserve `PrincipalRef`, `ObjectiveContractRef`, `NodeContract` and the existing risk/evidence enums | A new ID scheme or different hash algorithm requires a major-version migration and demonstrated need; there is none in this inspection | Contract lane / all |
| D02: contract extension and forwarding | The registry and SDD require preserving unknown compatible optional fields; [upcast](../../../../src/accretion/contracts/upcast.py), lines 1–155 and 290 onward, returns a lossy, resealed read projection while preserving the original stored payload. It is not a forwarding envelope | Keep an immutable writer envelope, verified writer digest and reader projection separately. Forward/export the original supported-major envelope with unknown fields intact. Only fully understood executable fields can authorize execution. Introduce new robotics contracts by composition, pinning existing node/objective refs, to avoid unnecessary changes to v0.4 field sets | If an existing v0.4 contract must gain fields, implement both old-writer/new-reader and new-writer/old-reader fixtures, explicit field-set hash handling and migration receipts; do not merely change the schema default | Contract/persistence lane / M1–M5 |
| D03: simulation approvals and risk | SDD OQ10 proposes objective approval for low-risk episodes, but [risk mapping](../../../../src/accretion/contracts/routing.py), lines 151–188, maps SIMULATION→HIGH. [CapabilityPolicyEngine](../../../../src/accretion/governance.py), lines 150–175, protects all side effects, and Golden Direction §8.4/§16 requires individual high-risk trial approval | Preserve SIMULATION→HIGH. Specify one human-approved episode/trial authorization bound to its exact planned episode ID, contract, successful preflight, envelope, adapter/environment, caps and lease. Every action still requires current capability authorization and deterministic safety admission. Add this narrow approval interpretation explicitly; never silently reuse generic approval | The unchanged per-action approval path remains the safe implementation fallback if an episode-scoped policy is not approved. A lower-risk classification needs explicit policy/authority review and cannot be inferred from a simulator label | Authority lane / M2–M4, M8, benchmark protocol |
| D04: policy versus safety receipts and signatures | Existing [CapabilityAuthorization](../../../../src/accretion/contracts/__init__.py), lines 569–575, carries outcome, policy ID/version and approval ID, without a content digest/signature. SDD §5.5 requires a signed safety receipt; registry §20.7 only gives a general later signature default | Keep permission and deterministic safety as distinct receipts. Pin `PolicyRef`, action hash, observation/state reference, envelope, lease fencing generation, command binding, evaluator digest and caps in a new safety decision receipt. Use detached, domain-separated signatures with an identified trusted evaluator key; a content hash is never described as a signature | If only local trusted-process receipts are desired, that is an explicit SDD/registry decision before implementation, not fulfillment of the existing signed-receipt requirement. [Plugin Ed25519 verification](../../../../src/accretion/plugins/trust.py), lines 66–173, supplies a primitive, not evaluator authority or signing-key lifecycle | Authority/contracts lane / M2–M5 |
| D05: event envelope and non-run events | SDD §16 assumes an inherited envelope with workspace/project, producer, schema version and payload digest. [AgentEvent](../../../../src/accretion/contracts/__init__.py), lines 1759–1773, instead requires run/session/provider, uses `timestamp`, `normalized_type` and `adapter_version`; [agent_events](../../../../src/accretion/persistence/models.py), lines 254–274, requires a run FK. Registration/conformance events may predate runs | Define one versioned domain-event envelope and a durable outbox for v0.5 domain events, with nullable run/node, explicit scope, actor, canonical type, causation, sequence and payload digest. Project run-scoped events into the existing trace/SSE boundary with source-event identity; retain old events unchanged | A versioned extension of existing AgentEvent is possible only with explicit nullable-run and schema migration compatibility, historical replay and all consumer tests. Do not mint a fake run or label the old event shape as the registry envelope | Event/persistence owner / M1–M5, M8 |
| D06: simulation lease and lifecycle ownership | [WorkspaceLease](../../../../src/accretion/contracts/__init__.py), lines 1908–1920, leases a Git worktree; it has no simulator endpoint, ownership fencing or episode action authority. SDD lifecycle has no recovery transition out of HumanReview and compresses execution/verification authority | Add a distinct `SimulationLease` owned by the gateway: opaque handle, resource/endpoint binding, exact scope and artifact identities, monotonic fencing generation, wall-clock expiry, heartbeat, revocation and terminal cleanup. Keep execution state and verification state separately authorized, with an explicit unresolved-review/resolution/quarantine model | Do not overload worktree leases. A single-process in-memory lease is a development fixture only; production acceptance requires persistence and races across clients/processes | Gateway/persistence owner / M2–M5 |
| D07: API retries, actions and uncertain outcomes | SDD §15 mandates `Idempotency-Key` and `If-Match`. Existing [routing API](../../../../src/accretion/api/routing.py) uses IDs/body expected versions, and the [side-effect ledger](../../../../src/accretion/persistence/side_effects.py) keys globally by idempotency key without comparing changed request payloads itself | Freeze request-key scope, canonical request digest, response receipt and ETag/resource revision. Same key+same request returns the original receipt with no dispatch; same key+different body conflicts. Action sequence is unique and monotonic per episode. Lost/uncertain acknowledgement terminates the episode; a new reset/new episode is required | Do not infer exactly-once simulator execution from a ledger. Legacy ledger reuse needs an explicit scoped wrapper, body conflict checks and atomic episode admission; otherwise add a simulation action ledger | API/gateway/persistence owner / M2–M5 |
| D08: verifier process and service identity | [IndependenceCheck](../../../../src/accretion/feedback/verification.py), lines 205–250, allows sessionless in-process deterministic verification. SDD §5.7 requires separate process and identity. [BoundedProcess](../../../../src/accretion/verifiers/process.py) bounds time/output, but does not establish a distinct identity, read-only evidence authority or CPU/memory isolation | Introduce `EmbodiedVerifierHost` with producer/verifier service-principal separation, registered implementation digests, immutable read-only evidence, and authenticated result submission. Reuse `VerifierRef`, `VerificationState` and claim coverage, with an explicit bridge to `IndependentVerificationResult` | Separate agent context alone is insufficient. A separate process with the same unrestricted authority is also insufficient; record the actual identity/sandbox boundary | Verification owner / M5, M6–M9 |
| D09: artifact and evidence ownership | SDD uses `artifact://` URIs; current [ArtifactRef](../../../../src/accretion/contracts/__init__.py), lines 1893–1898, is run/path based with optional hash. Existing [EvidenceRef](../../../../src/accretion/contracts/refs.py), lines 155–174, requires class and digest; retention remains a token without a full policy vocabulary | Keep existing ArtifactRef semantics. Define a canonical content-addressed blob/manifest reference for registry artifacts that may predate runs; bind into run artifacts when consumed. Define retention and size/decompression limits. Require SIMULATION labels and parent provenance in recorder, verifier, archive and export | Extending ArtifactRef must preserve old required run identity and existing readers; weakening it to fit registry inputs is not acceptable | Evidence/contracts owner / M1, M4–M5, M8–M9 |
| D10: action representation and safety timing | End-effector target poses and scale factors alone do not prove joint/velocity/acceleration/workspace/collision bounds. SDD evaluates before adapter invocation, while adapter translation may determine the actual trajectory | Freeze typed SI/UCUM-compatible units, frame/transform provenance, clock rules, gripper intents and a bounded controller mode. Use a prepare → validate → execute protocol: an isolated preparation stage yields a digest-bound trajectory/control proposal; evaluator admits that exact proposal against current state; executor rejects a changed proposal or stale state/receipt. Account actual motion and stop on violations | A conservative controller that proves the same bounds without exposing a prepared trajectory is possible, but its verified contract and receipt binding must be explicit. No torque/current escape or unvalidated artifact payload | Contracts/safety/adapter owners / M1–M4, M6–M7 |
| D11: simulator profile, replay and conformance identity | SDD §23 lists proposed Gazebo/ROS defaults, not installed/tested facts. Example SimulationExperimentContract omits the mandatory replay class; conformance example's 48/48 is illustrative | M0 records exact simulator/middleware profile decision and resolved deployment digests before execution. Declare replay class/tolerances, physics configuration, seeds and randomization samples in the contract. Invalidate conformance on adapter, simulator, world/model, controller, schema or tolerance-profile changes. Prefer tolerant replay unless exact is demonstrated | Gazebo Harmonic-class/ROS2 Jazzy-class is the forward-spec default pending a bounded compatibility decision; alternate middleware/simulator requires an ADR. Do not use an example test count as an acceptance result | Runtime/conformance owner / M1–M7, M9 |
| D12: experience and routing boundary | v0.4 `ExperienceRecord` is a projection over P7 Experience, and routing compatibility signatures do not yet include embodiment/environment conformance identity. v0.5 prohibits cross-embodiment experience changing live action selection | Create one episode eligibility projection keyed to the existing experience source, with pinned task/verifier/adapter/environment identity and contradiction edges. Route fixed graph-node configurations only; keep learned live routing/online exploration off for SIMULATION. Different embodiment evidence can appear as an explained weak prior, never an admitted action source | Any new routing/experience ablation must name the exact policy and fresh evidence it actually uses. Do not count displaying a weak prior as action-level transfer or claim v0.7 completion | Experience/routing owner / M4–M5, M8–M9 |

### D03 is a required authority decision, not an assumed convenience

The minimal initial model is exact per-episode approval. An automated seed matrix
can prepare frozen episode/preflight bundles, but it pauses for the actual human
approval of each planned trial. Its driver cannot mint approval records, reuse
an old objective approval, or treat a prior successful seed as approval of a
new attempt. A changed artifact, envelope, lease/preflight identity or expired
authorization requires a newly valid approval. This makes approval overhead
part of the benchmark's operational design and reported end-to-end timing.

A narrower **isolated-simulation policy** is an alternative for M0 review: a
human reviews an explicit finite matrix of exact trial identities/artifacts,
caps and expiry, and the authority service records the scope of each approved
trial. Admission can then consume those existing approvals without another
interactive prompt where the approved pins still match. This requires a real
approval UI/API, explicit matching/consumption/revocation semantics, and a
decision that it satisfies the individual-trial invariant. A wildcard approval
for future seeds, retries or newly constructed episodes would be an authority
change, not that finite-matrix option. Keep the existing HIGH mapping and
general capability rules outside this narrow profile. Select and approve one
model before claiming that the benchmark can run unattended.

### Hash and identity details that must survive D01–D02

- The serializer deliberately differs from RFC 8785: Unicode code-point key
  ordering, integer/float distinction, Decimal-as-string and UTC datetime
  handling are documented in [canonical.py](../../../../src/accretion/contracts/canonical.py),
  lines 1–80. Preserve this established algorithm and its
  [TypeScript twin](../../../../apps/ui/src/contracts/canonical.ts); a casual
  switch to a library named “canonical JSON” changes existing identities.
- `content_hash` is optional during construction and checked when provided.
  Persisted readers must require the original nonempty seal before validation;
  otherwise a missing seal can silently become a freshly sealed body. The
  current [store boundary](../../../../src/accretion/persistence/store.py),
  lines 389–431, already distinguishes those paths.
- Writer digest, derived node/configuration digest and reader-projection digest
  are different identities. Bind execution/accounting to verified original
  payloads and checked row scope, never a projected object resealed by a reader.
  Preserve the [v0.4 reference witnesses](../../../../tests/test_v04_upcast_references.py)
  and [PostgreSQL parity witnesses](../../../../tests/test_v04_upcast_references_postgres.py).
- Unknown same-version fields, unknown nested authority fields and unknown
  enum/major values remain refusals. Forwarding an opaque, verified envelope
  does not imply understanding or permission to execute its contents.
- `PrincipalRef` currently contains principal ID/display/status, while the full
  [Principal](../../../../src/accretion/contracts/__init__.py), lines 613–630,
  carries issuer/subject and memberships carry workspace role. Resolve authority
  from persisted identity/membership, not caller-supplied display/status or a
  duplicate “robotics principal” schema.

### Additional fields the schema freeze must explicitly decide

The ten named v0.5 contracts are abbreviated examples, not complete executable
schemas. Their freeze must cover the shared header, exact foreign references
and these missing or underspecified details:

| Contract / companion | Required freeze detail |
|---|---|
| `EmbodimentDescriptor`, `ObservationSpec` | Joint order and units; frame/transform versions; sensor validity/missing/skew policy; gripper semantics; numeric finite/range validation; immutable model references |
| `RobotAdapterManifest`, `AdapterConformanceReport` | Requested capability versions, signed artifact identity, approved endpoint profile, controller/observation compatibility; exact conformance dependency closure and independently produced evidence |
| `ActionIntent`, `SafetyEnvelope` | Admitted command binding, state/clock reference, sequence/expiry, authorized mode, joint and Cartesian limits including acceleration/effort where applicable, contact/grasp policy and accumulated budget accounting |
| `SimulationExperimentContract`, `SimulationEnvironmentSnapshot` | Replay class and tolerance profile; required verifier identities; resource budgets; frozen seed matrix, distributions and actual samples; all world/robot/controller/adapter/image digests; host/GPU-sensitive determinism limitations |
| `EpisodeRecord`, `EmbodiedVerificationSpec` | Producer and verifier service identities, independent task/safety/completeness/replay coverage, complete content-addressed manifest, original routing/policy/safety receipts, terminal reason and contradiction lineage |
| New supporting records | A simulation lease; preflight result; episode approval binding; signed safety decision; action admission/acknowledgement; lifecycle transition/outbox event; quarantine/resolution. Register one owner for each before storage/API code appears |

`STOP_BEFORE_CONTACT` in the example envelope is not compatible with treating
every gripper-object contact as a violation in a pick-and-place task. Freeze
allowed contact pairs/phases and deterministic forbidden-contact checks; never
quietly turn collision checking off to make the flagship task pass.

## File ownership and dependencies

Paths marked **proposed** do not yet exist. One lane owns edits to each shared
file; other agents supply requirements or patches through the coordinator.
This is an ownership recommendation for the parent's delivery plan, not a
request to run more than the coordinator plus three concurrent workers.

| Surface | Canonical source / proposed destination | Single edit owner | Depends on |
|---|---|---|---|
| Active design, overlay and acceptance mapping | **Proposed:** `docs/sdd/Accretion_SDD_v0.5.md`, `docs/contracts/v0.5/README.md`, v0.5 release/backlog/ADRs; existing frozen package unchanged | Coordinator/contracts | Approved scope, D01–D12 |
| Contract classes, IDs, serializer compatibility | Existing `src/accretion/contracts/{canonical,refs,upcast,routing}.py`, `src/accretion/ids.py`; **proposed:** `src/accretion/contracts/robotics/`, v0.5 schemas/fixtures/export wiring | Contracts lane | D01, D02, D04, D09–D11 |
| Persistence and migrations | Existing `src/accretion/persistence/{models,store}.py`, `src/accretion/persistence/side_effects.py`; **proposed:** additive robotics tables and next unallocated migration | Coordinator/persistence lane | Frozen types, lease/event/concurrency model; obtain migration number at integration time |
| Gateway and authority | Existing `src/accretion/governance.py`, identity/token broker boundaries; **proposed:** `src/accretion/robotics/{gateway,leases,preflight,host}.py` | Runtime/authority lane | D03–D07, D10; M1 SDK |
| Safety and command protocol | **Proposed:** `src/accretion/robotics/{safety,commands}.py`, signed receipt verifier, adversarial fixtures | Safety lane | D03, D04, D06, D10; pinned adapter SDK |
| Episode execution/recorder | **Proposed:** `src/accretion/robotics/{orchestrator,recorder}.py`; bounded integration seam in existing run manager | Runtime lane | M1–M3, event/outbox and atomic persistence |
| Independent verification/evidence | Existing `src/accretion/verifiers`, `feedback/verification.py`, evidence refs; **proposed:** embodied host/verifiers/replay and eligibility adapter | Verification lane | M4 evidence manifest; D08–D12 |
| Domain events/API/projection | Existing `api/main.py`, `projections.py`, generated API schema; **proposed:** `events/` domain envelope/outbox and `api/robotics.py` | Coordinator/API lane | Frozen DTOs and durable backend services; M4/M5 |
| Adapters | **Proposed:** separate flagship and independent second adapter packages behind the same SDK | Adapter lanes, separate module ownership | M1–M5; exact environment profiles |
| Studio and generated client | Existing `apps/ui/src/api/schema.d.ts`, route/graph surfaces, canonical hash twin; **proposed:** robotics inventory/episode components | Frontend lane | Frozen DTOs/events first; executable backend before acceptance |
| Conformance and release tests | Existing acceptance/release/docs harness; **proposed:** `tests/test_v05_*`, v0.5 contract fixtures and simulation conformance/protocol evidence | Coordinator with each behavior owner | Claiming implementation and immutable scenario identities |

The critical dependency chain is **approved M0 → registry/SDK M1 → gateway M2
and safety M3 → orchestration/evidence M4 → independent verification/replay M5
→ both real conformance adapters M6/M7 → benchmark/release M9**. Frontend layout
and test harness scaffolding can begin after the relevant DTOs are frozen,
but neither unlocks backend acceptance. M6 and M7 can be developed concurrently
against a stable SDK; the second adapter's conformance evaluation must remain
independent. Integrate shared store/event/schema changes before scattering
feature work across worktrees.

## M0 review and later acceptance witnesses

M0 is complete when the approved active SDD and registry overlay resolve every
decision above, every contract has one owner and exact schema version, examples
and golden hash vectors agree, all 30 SDD §22 criteria have behavior owners and
planned witnesses, and the simulator/conformance/protocol boundaries are
recorded. A successful schema freeze proves none of the later runtime criteria.

Required witnesses to allocate now, execute with the owning milestone:

1. Minimal/complete/invalid/unknown-major fixtures; old/new reader and writer
   round trips; Unicode/float/time hash vectors in Python and TypeScript;
   tampered/missing seals and unknown execution fields rejected.
2. Immutable registry admission; alias resolution pinned before a run;
   conformance invalidation across the exact dependency closure; registry
   manifests requesting capabilities cannot authorize them.
3. Opaque lease expiry/revocation/fencing and cross-scope denial; duplicate and
   changed-body idempotency across concurrent clients; transaction rollback
   before and after outbox/action-intent persistence; no physical endpoint.
4. Every intent checked before dispatch; stale observation/receipt, unit/frame
   mismatch, cap overflow, invalid contact, changed prepared command and clock
   regression produce a typed refusal with zero unsafe dispatch.
5. Drop acknowledgements and crash at each action boundary. Prove no replay into
   uncertain state; only new reset/new episode recovery. Prove hard caps persist
   through concurrent processes and restart, not only local locks.
6. Separate verifier principal/process cannot mutate producer output; producer
   cannot write verifier verdicts; missing evidence and conflicting required
   verdicts never become acceptance; review and quarantine preserve original
   provenance and identify downstream experience.
7. Durable event replay for pre-run registration and run-scoped episodes;
   idempotent consumers and source-event identities; reconnect reconstructs
   state from persistence; large payloads remain artifacts.
8. Simulation labels, digests and retention survive recorder → archive → import
   → replay → export; no API, UI or experience adapter upgrades simulation
   evidence into physical evidence.
9. Both conforming adapters execute the same frozen high-level tasks and paired
   design. Static direct-script and Accretion baselines, routing/experience
   ablations, safety/false-acceptance gates and replay classes need prospective
   evidence. Existing fake runtime or a copied adapter with a new name cannot
   establish embodiment-neutral success.

The v0.4 routing-benefit NO-GO must remain visible throughout. It permits entry
under v0.5 §2.4; it does not justify enabling learned simulation control. A v0.5
feature-complete candidate, a passed simulation claim gate, and a promoted
release are separate outcomes. Passing any of them does not authorize v0.6
physical execution.
