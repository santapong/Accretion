# v0.5 M0 decisions and contract ownership

Status: **adopted implementation directions; M0 empirical gates pending**.
On 9 September 2026 Santapong approved the
[completion plan](completion-plan-2026-09-09.md) and instructed implementation.
The coordinator adopts its recommended initial profile and the following
bounded resolutions. Approval of the plan covers this local implementation and
feasibility work; it does not assert conformance, a study outcome or a release.
The [original SDD](../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
and its imported package remain preserved.

These scoped ADR identifiers avoid renumbering historical ADR-051–064. This
overlay resolves the illustrative v0.5 fields against existing contract owners;
it does not weaken inherited authority or any of the thirty acceptance criteria.

## V05-ADR-001 — simulator profile and development feasibility

Select MuJoCo with one UR5e/Robotiq 2F-85 assembly and a separately implemented
Panda assembly under a shared task-level contract. Use CPU physics and verified
software rendering for the initial profile. Gazebo/ROS remains a documented
alternative rather than an installed dependency. A single physics engine does
not establish cross-simulator portability or learned cross-embodiment transfer.

M0 feasibility is limited to one process, twenty elapsed minutes, ten measured
CPU minutes and two GiB of output. It checks exact model loading, reset/step,
joint metadata, RGB/depth observations and rigid-object contacts for both
assemblies. No hardware endpoint, external-provider call or registered held-out
evaluation is part of this development check. Preserve software/model/scene
digests, licenses and commands. The feasibility report must distinguish contact
and reaching from an actual grasp, lift, transport, release and stable placement.

Dependencies belong in an explicit optional group. Normal contract/unit tests
must import without MuJoCo installed. The deployment profile will additionally
pin the immutable worker image, controller, physics/render settings and model
assets; a package version alone is not complete environment identity.

## V05-ADR-002 — canonical identity and references

Use the existing `CanonicalContract` header, prefixed opaque IDs, workspace and
project scope, canonical serializer and shared refs/enums. The original YAML's
UUID spelling and PascalCase tags are illustrative; add unique prefixes and
lowercase contract types. Do not change v0.4 schemas or the TypeScript canonical
algorithm to accommodate robotics. New core and supporting records live in
`src/accretion/contracts/robotics/` with an enumerated schema inventory.

Persisted/imported contracts require their original nonempty seal before
validation. A canonical writer envelope retains the exact verified original
payload, including compatible optional fields, separately from a read-only
projection. Unknown major, same-version extras and unknown authority fields
fail closed. An opaque compatible-minor envelope may be forwarded; it cannot
authorize execution through a reader that does not understand its full payload.

## V05-ADR-003 — exact episode approval

Keep `RiskClass.SIMULATION → HIGH` and existing generic side-effect policy.
The robotics service supports a narrow exact episode approval, bound to the
planned episode identity, successful preflight, immutable experiment, envelope,
adapter/environment, resource caps, current lease/fence, approver and expiry.
Admission verifies current persisted membership/permission, not caller-supplied
principal labels. Revocation or a changed pin prevents further invocation.
An episode approval never substitutes for per-action deterministic admission.

A matrix record may contain only an explicit finite list of already reviewed
exact approval references. It cannot manufacture human approvals, extend their
expiry, authorize future unknown leases/seeds or approve retries. The initial
runner pauses when an exact valid approval is absent. Any unattended study
must first present concrete scopes that this authority service can actually
honor; a generated test fixture is not a human approval in production.

## V05-ADR-004 — preparation and signed safety receipts

The protocol separates observation, preparation and execution. Preparation is
bounded and cannot advance physics or actuate. It returns an immutable candidate
command with ordered joint samples/control inputs, timestamps, state/transform
identity, intent hash and lease generation. Safety checks the actual candidate,
including path interiors, before execution. The executor consumes that exact
digest once and refuses stale state, altered inputs, invalid signature or fence.

Use detached Ed25519 signatures over a domain-separated unsigned receipt digest.
Keep receipt content hash, signature and policy authorization as distinct
objects; do not create a self-referential hash/signature. A trusted evaluator
key registry identifies issuer, key ID, public key, activation and revocation.
Private signing keys stay outside adapter mounts and event/artifact payloads.
Verification uses the trusted registry and refuses unknown/revoked keys. Local
test keys are generated only in test-owned temporary storage and never establish
a production approver or evaluator identity.

## V05-ADR-005 — action, units and contact semantics

Require ordered joint names, finite SI values, typed units/shapes, named frames
with transform provenance, and monotonic simulation clock/observation sequence.
Initial control uses bounded position trajectories with explicit gripper
semantics; arbitrary torque/current commands are not an escape path.

The envelope checks position, velocity, acceleration, effort where relevant,
workspace/forbidden volumes, cumulative motion, action/time/resource caps and
contacts. Permitted gripper/object contact pairs have explicit task phases and
force/penetration bounds. Every other contact remains governed. Pick/place
success requires physical lift/transport/release and post-release stability,
not just end-effector distance or a producer's success message.

## V05-ADR-006 — host, leases and uncertainty

The approved execution profile is an isolated child/container with an explicit
simulation-only launch inventory, resource bounds, read-only code/model mounts,
restricted filesystem and network, and no host device/serial/USB/fieldbus mounts.
An opaque internally resolved lease handle is the sole endpoint selector;
localhost, a manifest string or a URL does not prove a simulator boundary.
Isolation must be observed by escape tests, not asserted by a process flag.

Add a distinct durable simulator lease with exclusive resource scope, monotonic
fencing, expiry, heartbeat and revocation. The gateway owns it; Git workspace
leases and process-local locks are not substitutes. Concurrent clients and
restarts must preserve one owner. Lost/uncertain acknowledgement terminates the
episode without resending into that state; recovery needs a reset and new
episode identity. Cleanup terminates the owned process tree within its deadline.

## V05-ADR-007 — immutable registry and content-addressed evidence

Descriptors and adapter versions are immutable, scoped and digest-addressed.
Manifests request capabilities but grant none. Avoid a circular manifest/report
seal: the manifest declares its artifact and compatibility requirements; the
registry associates independently produced conformance with the exact closure.
Changing adapter, image, world/model, controller, schema or tolerance invalidates
that association until requalification.

Add a content-addressed artifact reference usable before a run exists; keep the
legacy run-bound `ArtifactRef` unchanged. Require media type, digest, byte count,
retention, SIMULATION evidence class and provenance. Bound raw sensor chunks,
manifest size and decompression before parsing; reject path traversal, symlinks,
unexpected external locations and incomplete/hash-mismatched reads. Export
retains the simulation label and may not relabel it as physical evidence.

## V05-ADR-008 — state, idempotency and domain events

Keep mutable episode execution state separate from the final sealed
`EpisodeRecord`. Execution and verification identities have separate transition
permissions. Acceptance requires independent task, safety and completeness
PASS; unresolved inconclusive state reaches human review. Contradiction and
quarantine append evidence without erasing original records.

Every episode and lease retains a real `run_id`: current capability policy
resolves persisted run → task → project/principal. The episode service creates
a genuine `Task(EXPERIMENT)`, `Run(provider=DETERMINISTIC)` and robotics-owned
`SimulationRunBinding` atomically before leasing. The immutable binding records
episode/experiment and `EMBODIED_ORCHESTRATOR` ownership. It is internal service
state with a run FK, never a client-writable manifest grant. No dummy FAKE run or
weakening of legacy `Run`/`ArtifactRef` is needed.

`Provider.DETERMINISTIC` is an attribution label, not an installed agent CLI.
Generic run creation/reconciliation/pause/resume/cancel/audit must consult the
authoritative binding before mutation and dispatch to the episode owner or
return a typed unsupported-operation response. Never infer ownership from a
provider string or caller label. The episode service owns startup reconciliation
and uncertain-action termination. A standalone episode has no invented software
workflow graph or worktree; a graph mode is recorded only for a real graph.

The current generic TOOL path sends a `query`, tolerates ordinary capability
failures and verifies a Git diff. It cannot execute simulation work unchanged.
The future workflow seam must pass an exact typed episode/experiment reference,
propagate denial/uncertainty/failure and await independent episode verification.
Gateway acknowledgement alone must never mark the episode or parent run as
successful. These dispatch and outcome boundaries require integration witnesses.

Idempotency keys are scoped by workspace, principal, operation and resource.
Persist canonical request digest and original response: identical retries return
that response; changed-body reuse conflicts. Every existing-resource write uses
an expected resource revision. Header parsing rejects missing, wildcard or
malformed `If-Match` rather than silently disabling concurrency. Admission and
ledger reservation are transactional. No ledger alone proves exactly-once
external execution.

Use a robotics domain-event envelope/outbox with workspace/project, original
writer seal, actor, event type, occurrence time, correlation/causation and
monotonic aggregate sequence. Run/node are optional for pre-run registration.
The existing `AgentEvent` remains unchanged; run-scoped projections retain the
source domain-event identity. Snapshot/SSE recovery and event reconstruction
are distinct from physics replay. Large sensor payloads remain artifact refs.

## V05-ADR-009 — independent verification and replay

Run embodied verification in a separate process and service identity, with
read-only sealed evidence and no adapter/action authority or producer credentials.
The host admits a pinned verifier implementation and authenticates its result.
Separate session labels alone do not meet this requirement. Qualify task, safety,
completeness and replay checks with positive, adversarial, missing, tampered and
near-threshold evidence before relying on them.

Declare tolerant physics replay initially, with tolerance profiles determined
from development/qualification before evaluation. Exact is available only where
demonstrated; statistical replay needs its own registered bounds. Never upgrade
the reported class, tune tolerances after locked outcomes or mistake ordered
event reconstruction for a fresh simulator run.

## V05-ADR-010 — experience and routing

Use one explicit episode-eligibility bridge to existing P7/v0.4 experience and
contradiction lineage. Require complete retained artifacts, independent PASS,
exact environment/adapter/verifier identity and no unresolved contradiction.
Quarantine invalidates downstream reuse across restart and exposes dependents.

Different-embodiment evidence is an attributed weak prior and cannot change live
action selection. Learned AUTO activation and online action exploration remain
off for SIMULATION. The study's routing and experience treatments require a
meaningful frozen compatible local configuration set; four labels over the
FAKE baseline do not isolate effects.

## V05-ADR-011 — API and Studio integration

Preserve the eleven minimum SDD operations. Add scoped, paginated collection,
detail, approval, event-stream, comparison and evidence-export operations as
specified in [the API contract](m0-api-contract.md). Route handlers derive the
authenticated identity and delegate authority/state transitions to the domain
service; the browser does not own simulation state or raw actuator commands.

Use the current operator shell, one route inventory, generated OpenAPI types and
backend-derived snapshots. New screens are `/simulations`, registry, experiment
and episode detail, with explicit empty, stale, denied, aborted, failed,
inconclusive and quarantined states. Preserve SSE gap recovery and actual browser
accessibility/style evidence. UI mocks are component evidence only.

## V05-ADR-012 — acceptance, feasibility and release claims

The active SDD preserves all thirty source criterion texts as AC5-001–AC5-030;
store milestone accountability separately. Register deferrals explicitly while
construction is incomplete. A criterion is discharged only when every required
automated, actual-simulator, browser and benchmark component passes for the
candidate. The v0.5 release gate rejects missing/stale/mismatched evidence and
all intermediate deferrals. Tooling/parser tests do not claim simulation results.

Preserve v0.4's protocol, access log and scoped NO-GO. Its explicit entry
exception does not waive v0.5 research. Keep development, qualification and
locked identities disjoint. Before the campaign, finalize the paired analysis,
precision/margins, exact trial approvals, resource/storage bounds and immutable
candidate closure in a separate prospective protocol. No such outcome is
claimed in M0. Full release remains subject to the plan's research and release
gates; no physical work follows from this decision.

## M0 exit and ownership

The contract agent owns the robotics contract package, new IDs, schema inventory
and golden fixtures. The runtime agent owns bounded feasibility and provenance.
The evidence agent owns active SDD/acceptance registration and prospective
protocol preparation. The coordinator owns this overlay, API freeze, dependency
lock integration, documentation hub and the combined gate record.

M0 closes only after schemas and fixtures, strict acceptance registration,
approved environment feasibility and integration checks pass. Later simulator,
UI and benchmark witness components remain pending even when their accountable
owner was M0. The next wave starts from the reviewed combined M0 candidate.
