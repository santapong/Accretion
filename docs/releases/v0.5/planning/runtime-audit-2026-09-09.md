# v0.5 runtime and adapter planning audit

Status: proposal; no v0.5 implementation or simulation was executed in this audit.
Inspected on 2026-09-09 at Accretion commit
`01e2268b3b602a12eeaf81f55fb4670a1fe7636f` in an isolated planning worktree.
The [frozen v0.5 SDD](../../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
was read in full, together with relevant contract and runtime sections of the
[cross-release registry](../../../sdd/future/v0.4-v1.0/01_GOVERNANCE/Accretion_Cross_Release_Contract_Registry_v0.4_to_v1.0.md). Frozen
source documents remain unchanged. M0–M9 below number the ten SDD §21 steps from
zero; they are proposed work, not completed milestones.

## Recommendation

Build a simulation-only vertical slice on one frozen CPU physics stack, with
two independently implemented robot adapters and backend-owned admission,
recording and verification. Prefer MuJoCo with UR5e plus Robotiq 2F-85 as the
six-axis flagship and Franka Emika Panda with parallel fingers as the second
embodiment, subject to an explicit M0 simulator decision. These are two actual
simulated manipulators; the existing FAKE agent runtime and a protocol test
double do not satisfy the embodiment-neutral claim.

The existing control plane supplies valuable primitives, but there is no
`RobotAdapter`, simulation gateway, simulation lease, episode recorder or
embodied verifier implementation in the inspected `src/accretion` tree. v0.5
therefore requires a new execution domain, not a rename of the P7 trajectory
feature. P7 trajectories describe software workflows, tool sequences and
verification findings.

Keep the first release narrow: deterministic scripted task producers, one
fixed physics engine and replay tolerance profile, bounded local simulation,
no physical namespace, no external agent-provider requirement, and no learned
cross-embodiment action policy. Routing and compatible experience remain real
benchmark treatments even when deterministic producers make provider costs
zero. The research plan owns their pre-registration and statistical gates.

## Evidence and implementation gaps

| Area | Existing reusable evidence | Missing v0.5 behavior and implication |
| --- | --- | --- |
| Canonical contracts | [Canonical header and hashing](../../../../src/accretion/contracts/canonical.py), [read-boundary upcasting](../../../../src/accretion/contracts/upcast.py), exported v0.4 schemas and golden fixtures | Ten v0.5 contract types, unit/frame/dimensional validation, semantic intent schemas and immutable robotics references. Preserve newer compatible optional fields in the stored writer document; an execution projection must not silently erase safety semantics. |
| Registry and trust | Immutable capability versions in [state storage](../../../../src/accretion/persistence/store.py); plugin manifest registration and [artifact trust](../../../../src/accretion/plugins/trust.py) | Embodiment/adapter versions, alias resolution before admission, environment-bound conformance activation/revocation and controller/world compatibility. A plugin manifest request is not a grant or a conformance pass. |
| Capability admission | [Gateway and policy engine](../../../../src/accretion/governance.py) deny undeclared/disabled/versionless capabilities, validate schemas, bind approvals and ledger side effects | Six `robotics.sim.*` handlers, opaque simulation lease resolution, deterministic per-action safety admission and a hard physical-endpoint denial. Existing task permission checks are necessary but insufficient. |
| Processes and isolation | [Runtime subprocess utilities](../../../../src/accretion/runtimes/common.py) and [bounded verifier process helper](../../../../src/accretion/verifiers/process.py) provide patterns for deadlines and output capture | Actual adapter host with distinct OS/container identity, CPU/memory/PID/output limits, process-tree teardown, heartbeat, message allowlist and egress confinement. A subprocess by itself is not the specified sandbox. Do not reuse provider credential/config environment allowlists for simulation. |
| Leases and uncertainty | [Git worktree leases](../../../../src/accretion/workspace.py), [local concurrency limiter](../../../../src/accretion/concurrency.py), [side-effect ledger](../../../../src/accretion/persistence/side_effects.py) | Durable exclusive simulation leases with expiry, fencing and episode/endpoint/digest binding. Git leases and in-memory semaphores do not fence a stale simulator worker. Generic side-effect result caching does not establish exactly-once action execution. |
| Events and replay | [Append-only execution-trace fold](../../../../src/accretion/tracing.py), durable events and SSE | Simulation/wall-clock mapping, sensor sequence validation, episode projection and all twelve SDD event types. Legacy `AgentEvent` lacks the full canonical schema/workspace/project/producer/payload-hash envelope: add an explicit compatible representation and projection, preserving historical bytes. |
| Artifacts | [Content-addressed router blobs](../../../../src/accretion/routing/artifacts.py) verify loaded bytes by digest; existing `ArtifactRef` links files to runs | Chunked trajectory/sensor storage, required digests, media/schema/retention metadata, bounded streaming and decompression, atomic sealed manifests and scoped export. `ArtifactRef.sha256` is optional today; do not use that as the acceptance standard. |
| Independent verification | [Verifier interface/registry](../../../../src/accretion/verifiers/base.py), [independence and claim coverage](../../../../src/accretion/feedback/verification.py), PASS/FAIL/INCONCLUSIVE aggregation | Separate verifier process and identity, read-only finalized evidence mounts, independent task/safety/replay/completeness checks. Existing run-manager verifier calls execute application verifier methods; recorded separate session IDs alone do not prove v0.5 process isolation. |
| Experience and contradiction | [P7 retrieval compatibility](../../../../src/accretion/experience/service.py), [v0.4 contradiction/revision pipeline](../../../../src/accretion/feedback/experience.py), `EvidenceClass.SIMULATION` | Episode/environment/embodiment compatibility, verification-gated eligibility, append-only quarantine and downstream lineage. P7 is repository/commit/workflow oriented and lacks episode source kinds; map explicitly rather than treating robot movement as a software trajectory. |
| Persistence/API | Memory/PostgreSQL implementations, immutable contract insert helpers, existing API idempotency patterns | Nine SDD relational entities plus explicit lease, receipt, lifecycle/idempotency and provenance storage as needed; transactional action admission and state changes; `Idempotency-Key` plus `If-Match` on every applicable write. |

These are source inspection findings. Historical test counts for v0.4 are not
new evidence that these robotics boundaries work.

## Simulator and embodiment decision for M0

The frozen SDD describes Gazebo Harmonic and ROS 2 Jazzy as **proposed defaults**
and explicitly requires an implementation ADR before coding. Do not silently
replace them. The ADR should compare these two complete deployment profiles:

| Profile | Concrete scope | Evidence and tradeoff | Decision treatment |
| --- | --- | --- | --- |
| Preserve the proposed default | Ubuntu 24.04 image, ROS 2 Jazzy, Gazebo Harmonic; exact `ros_gz`, controller, arm/gripper and world packages pinned | Official Gazebo documentation lists Jazzy/Harmonic as a recommended pair. Middleware/bridge/message policies and reset/acknowledgement behavior add work. This audit found no Accretion ROS/Gazebo adapter to reuse. | Valid if ROS integration is a v0.5 product requirement. Freeze the exact six-axis model, gripper, second model and controller packages before implementation; a distro name is not an environment snapshot. |
| Recommended alternative | Isolated native MuJoCo worker; UR5e + 2F-85 and an independent Panda adapter; no ROS dependency in the release profile | Upstream supplies these MJCF models; local RoboLLM has a pinned UR5e/gripper composition and bounded end-effector control to inspect. One physics stack reduces the initial integration and replay surface. This does not prove cross-simulator portability. | Record an explicit ADR selecting MuJoCo and stating that ROS is unnecessary for this simulation-only profile. Freeze exact package/wheel/image and model digests after the feasibility gate. |

Current primary references, checked 2026-09-09:

- [Gazebo's ROS installation matrix](https://gazebosim.org/docs/jetty/ros_installation/)
  supports the Jazzy/Harmonic pairing; it does not validate an Accretion image.
- [MuJoCo simulation documentation](https://mujoco.readthedocs.io/en/latest/programming/simulation.html)
  and [native Python bindings](https://mujoco.readthedocs.io/en/stable/python.html)
  document explicit model/data state and stepping. Accretion must capture the
  relevant integration state and prove its own replay tolerance; storing only
  joint positions and a seed is not enough.
- [Menagerie model inventory and licensing](https://github.com/google-deepmind/mujoco_menagerie/blob/main/README.md),
  [UR5e](https://github.com/google-deepmind/mujoco_menagerie/blob/main/universal_robots_ur5e/README.md),
  [Robotiq 2F-85](https://github.com/google-deepmind/mujoco_menagerie/blob/main/robotiq_2f85/README.md)
  and [Panda](https://github.com/google-deepmind/mujoco_menagerie/blob/main/franka_emika_panda/README.md)
  provide concrete model sources. Preserve each model's license and notices;
  archive exact source identities instead of fetching mutable `main` during a run.

Local supporting code was inspected read-only at RoboLLM commit
`9a4a6a2c51e0c9bd20056a0279ea10794a85699e` on clean
`experiment/ur5e-vla-bed` (no fetch):

- `sim/vla-bed/scene/build_scene.py` composes UR5e and 2F-85 from Menagerie
  commit `e4049d0a3bfd58d2a3081614e6777d4007e3f86a`, declares named arm joints,
  actuator mappings, front/wrist cameras and a visual red target.
- `sim/vla-bed/env.py` resets and advances MuJoCo with controller targets;
  `safety.py` and the local control path are design references for bounded
  proposals, not independent Accretion safety verification.
- `requirements.txt` currently names MuJoCo `3.10.0` and Mink `1.3.0`.
  Those are a candidate compatibility starting point, not the Accretion lock.
  No package availability, clean-room installation or rendering test was run.

The local target is a non-colliding visual object. Its reach demonstrations,
training results and Pi runs do not prove rigid-object grasping, lifting,
release, post-release stability or the v0.5 integration. Copy only reviewed,
licensed building blocks with source provenance into an Accretion-owned model
overlay; do not import local datasets, training dependencies, network viewers,
remote/Pi configuration or research acceptance claims.

### What the second adapter must demonstrate

The SDD allows two implementations or materially distinct adapter versions,
while its purpose asks the same experiment to bind to more than one simulated
embodiment. Use the stronger interpretation: UR5e and Panda should both execute
the same task-level reach and pick/place contract, with distinct descriptors,
joint/actuator mappings, grippers, limits and controller translations.

Give the second adapter a separate implementation owner. It may share protocol,
artifact transport and the selected physics process service. It must not be a
renamed UR5e class, a descriptor alias or a FAKE trace. Its own independently
reviewed control translation and frame/observation mapping must pass the same
black-box conformance suite. A different simulator is unnecessary for this
bounded claim; changing physics engines in the primary evaluation would create
a confound the SDD explicitly asks to avoid.

## Decisions that must precede parallel implementation

1. **Freeze executable contracts, not only example YAML.** The examples omit
   operational fields needed elsewhere in the SDD. Specify experiment replay
   class/tolerances, complete observation frames, per-joint velocity/acceleration/
   effort limits, action translation receipts, signed safety decisions, lease
   generations and state revisions. Reconcile the SDD's UUID examples with the
   existing canonical identifier convention through the registry owner.
2. **Separate action preparation from action execution.** A Cartesian pose and
   speed scale cannot prove joint/acceleration/collision safety. A bounded
   preparation operation produces a complete candidate command/trajectory
   without advancing simulation. Trusted admission checks the candidate,
   starting-state digest, frame transforms and envelope; execution consumes
   exactly the admitted command digest under the fenced lease. Reject stale
   state between preparation and execution. No adapter may substitute a new
   trajectory after admission.
3. **Define grasp contacts explicitly.** A universal `STOP_BEFORE_CONTACT`
   example would prohibit the intended grasp. Freeze permitted object/finger
   contacts by task phase with force/penetration and payload limits, while
   prohibiting table/body/forbidden-volume contacts. Do not disable collisions
   globally to make pick/place pass. Use swept-volume/trajectory checking where
   needed; testing only waypoint endpoints can miss an intervening collision.
4. **Specify bounded approval delegation.** The present capability policy
   requires approval for side effects. If objective approval covers a simulation
   episode, bind that authority to the exact frozen experiment, envelope,
   capabilities, lease and budgets. New versions or expanded scope require a new
   decision; do not weaken generic gateway rules or approve an unbounded session.
5. **Make simulation a closed endpoint class.** A localhost URL alone is not
   proof of simulation. The lease resolves only an internally launched,
   digest-attested simulator instance through an opaque handle. Reject arbitrary
   URLs, host networking, serial/USB/fieldbus mounts and `robotics.physical.*`
   registration and resolution. Adapter requests cannot choose the endpoint.
6. **Resolve receipt signing and identity.** The v0.5 SDD asks for signed safety
   decisions; the shared registry proposes hashes outside later promotion and
   physical approval. Record a v0.5-specific authenticated receipt rule and
   signer identity/key custody. A content hash alone is not a signature. Avoid
   circular digests between adapter packages, manifests and conformance reports.
7. **Preserve two replay meanings.** Event replay reconstructs the UI/lifecycle;
   simulator replay reruns the admitted commands in a new reset episode and
   compares declared state/metrics. Event replay alone cannot satisfy the
   simulator reproducibility criterion. Tolerant replay is the proposed default;
   exact and statistical claims need their own explicit evidence.

## Bounded implementation slices and witnesses

All paths marked as proposed below are future files, not implemented features.
Keep the public service boundary under `src/accretion/robotics/`, types under
`src/accretion/contracts/robotics/`, HTTP routes in
`src/accretion/api/robotics.py`, and simulator dependencies in an optional
simulation group/image. Avoid expanding the generic run manager into a robot
controller. The backend invokes the episode service as a workflow capability;
the simulator and low-level controller remain behind the gateway.

| Milestone / slice | Proposed ownership and dependencies | Meaningful completion witness |
| --- | --- | --- |
| M0: contract and simulator freeze | Contract owner: ten contracts, capability/event registry, immutable references, action/approval/signature/endpoint ADRs, exact simulator/model/controller pinning recipe. No other team edits shared IDs or schema exports concurrently. | Golden canonical round trips; incompatible major and unknown safety fields rejected; schema/frame/unit errors rejected; approval and physical-denial rules reviewed; a documented bounded feasibility run is required after implementation is authorized. |
| M1a: registry and persistence | `robotics/registry.py`, contracts, dedicated robotics store interfaces; assigned changes to `persistence/models.py`, store integration and one ordered migration chain. Depends on M0. | Same-version mutation rejected in Memory and PostgreSQL; aliases freeze to exact hashes; conformance activation invalidated when any dependency changes; cross-project access denied; rollback preserves historical rows. |
| M1b: adapter SDK and host protocol | `robotics/adapter.py`, `protocol.py`, `host.py`, `conformance.py`; schemas from M0. A test double is permitted for fault injection only. | Unknown/oversized messages, duplicate IDs and deadline violations fail; worker and grandchildren are terminated; CPU/memory/PID bounds exercised in the actual container/process profile; no raw endpoint or secrets escape. |
| M2: gateway and fenced lease | `robotics/gateway.py`, `leases.py`, `preflight.py`, six capability handlers and scoped policy integration. Depends on M1 contracts/registry/host. | Race two workers for one simulator: one wins; expired generation cannot step; reset/snapshot require the correct lease; forged URL/physical connector denied; failed preflight never reaches RUNNING. |
| M3: deterministic action admission | `robotics/safety.py`, command preparation and receipt signing; uses M2 lease and pinned envelope. | Mutation at each joint position/velocity/acceleration/effort/workspace/forbidden-contact/cumulative-motion bound is denied before simulator dispatch. Exact boundary, units, stale observation, NaN/Inf, missing frame, mismatched digest and runtime override tests fail closed. |
| M4a: episode orchestration | `robotics/episodes.py`, bounded workflow integration and typed API. Depends on M2/M3. | Competing `If-Match` writes cannot both advance; duplicate request with different body conflicts; action count/time/motion/no-action caps terminate; denied/preflight-failed actions never reach the worker. Lost acknowledgement after actual apply aborts, with no resend; restart allocates a fresh episode. |
| M4b: recorder and artifacts | `robotics/recorder.py`, `artifacts.py`, `events.py`, schema/versioned event projection; shared event changes through one owner. Can develop in parallel with M4a after event freeze. | Raw/normalized streams plus mapping, contacts, receipts and environment manifest seal atomically; interrupted upload cannot produce complete evidence; clock regression, skew, disk failure, sensor overflow and corrupted chunk prevent acceptance. Event replay reconstructs the same state after restart. |
| M5a: independent verification | `robotics/verifiers/`, `verifier_host.py`, immutable result persistence. Uses M4 sealed format; verifier author can prepare checks earlier from frozen fixtures. | Verifier runs as a distinct identity/process with only read-only artifacts. Forged producer success, changed object pose, missing sensors, invalid signatures, self-authored verifier identity and incomplete evidence cannot pass. Material concern becomes INCONCLUSIVE and human review. |
| M5b: replay and experience eligibility | `robotics/replay.py`, `experience.py`, explicit bridge to feedback/experience services. Depends on M4/M5a. | Replay from reset in a fresh process matches frozen tolerances; drift is reported without upgrading the replay class. Tampering or contradiction appends quarantine and finds dependent experience/training snapshots. Different-embodiment material stays a labeled weak prior and cannot choose live actions. |
| M6: UR5e flagship adapter | `robotics/adapters/mujoco_ur5e/` if ADR chooses MuJoCo, with Accretion-owned scene/controller overlay and upstream notices. Develop against M1 protocol and M3 semantics; integrate through M4/M5. | Actual physics executes reach, rigid-object grasp/lift/transport/release and a declared recoverable perception/planning fault. Independent verifier measures pose, object support/contact and post-release stability; no teleporting object or synthetic success flags. |
| M7: independent Panda adapter | `robotics/adapters/mujoco_panda/`, separate owner and control/frame mapping; same protocol and physics profile, own immutable model/controller hashes. Depends on frozen task abstraction, not on copying UR5e implementation. | Both adapters pass the same public conformance suite and private fault cases. The same high-level tasks bind to different DOF counts/grippers with no core orchestrator conditionals for robot names. Report integration effort and adapter-specific limitations. |
| M8: Studio integration seam | Parent plan owns UI. Backend supplies registry/conformance inventory, frozen contract matrix/diff, episode actions/receipts, normalized streams, safety and verifier/replay panels, contradiction and export. | API/SSE state reconstructs after reconnect; no client mutation directly changes topology, authority or safety; UI faithfully shows rejected, aborted, failed, accepted and human-review states. |
| M9: benchmark and release | Research/release owner; all prior slices and the frozen protocol required. | Four paired treatments (direct script, static workflow, routing without retrieved experience, routing with compatible verified experience), real two-adapter execution, non-regression and false-acceptance gates, artifact export/import/replay, security faults and clean-room reproduction pass before a release claim. |

Use smaller PRs within these slices where shared migration or event work needs
separate review. Unit and contract tests run without importing simulator
packages. Simulator integration tests use the exact optional image with a
disposable database and artifact directory. Run negative tests through the real
process boundary, not only mocks of the method being tested. Resource-heavy
simulation and benchmark jobs should be serialized or explicitly capped
independently of the number of coding agents.

## Critical failure paths to prove across milestones

- Kill the adapter after a simulator step but before acknowledgement. The
  durable receipt becomes uncertain, the episode aborts, the lease is revoked,
  and recovery starts with reset and a new ID. Reissuing the request may return
  its durable status but must not execute the action again.
- Crash the orchestrator between admission, recording, invocation and
  finalization; repeat with two processes. Each boundary must preserve one
  authoritative receipt/state and prevent a stale lease holder from acting.
  Database idempotency is not a claim about exactly-once external execution.
- Change the model/controller/physics artifact after conformance, or restore a
  snapshot from another environment/episode. Preflight or snapshot validation
  must reject it; aliases and mutable container tags cannot hide the change.
- Make the producer report success while independent contact/object-state
  evidence shows an object still held, dropped or teleported. Task acceptance
  must fail. Supply only producer-generated metrics with no required raw
  evidence: completeness must block acceptance.
- Exercise safety at every micro-step or a conservative bounded trajectory
  envelope, including paths whose endpoints are safe but interior intersects a
  forbidden volume. A deniable proposal must never become an executed step.
- Inject malformed units/frames, clock reversal, missing/wrong-shaped RGB,
  oversized/compressed artifacts and scene-label instructions. These are typed
  evidence/validation failures, never new authority or unbounded memory work.
- Reclassify a simulation artifact as physical at ingest, replay and export.
  Every path rejects the relabeling. A dashboard caption is insufficient.
- Quarantine an already accepted episode. Preserve its original verdict and
  append the contradiction; discover and exclude dependent experience and
  training snapshots until explicitly resolved.

## Readiness and boundaries

The critical path is contract/ADR freeze → host/lease/admission → sealed episode
evidence → independent replay/verification → both real adapters → paired
benchmark and release audit. The second adapter, verifier implementation and UI
can run as parallel workstreams once their shared contracts are frozen. They
cannot supply evidence for a gateway or recorder that does not yet exist.

The earliest useful integrated demo is one actual UR5e reach through the full
gateway/receipt/record/verifier path. It is an intermediate milestone only.
v0.5 is unfinished until pick/place, declared recovery, two real conforming
targets, replay, experience controls and all release criteria are evidenced.
If the benchmark fails its claim gate, retain and publish the result as NO-GO
within the version's decision record; do not substitute FAKE traces, loosen the
test after seeing results or unlock physical v0.6 work.

This planning audit did not install dependencies, run physics or benchmarks,
read locked research outcomes, invoke providers, access devices or the Pi,
modify frozen sources, push branches, merge PRs or create a release. Runtime
paths proposed here require validation during the subsequently authorized
implementation phase.
