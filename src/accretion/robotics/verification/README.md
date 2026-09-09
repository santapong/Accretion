# Captured-evidence verifier construction (v0.5 M5)

`verify_episode(original, trusted_binding, artifact_reader, trusted_safety_keys=…)`
returns `ConstructionFindings`. `compare_replay` checks two supplied complete
captures and returns a `RoleFinding`. These functions do not launch a host or
replay, authenticate process independence, emit `EmbodiedVerificationResult`,
accept episodes, or write quarantine/experience state. A `SATISFIED` finding is a
construction check, never an independently attested production PASS or AC5 proof.

The integration host must select and authenticate the configuration before
outcome access, pin the current episode/dependency closure, select installed
verifier implementations, supply trusted safety signing keys and the exact
ancillary artifact inventory, and authorize every read within workspace/project
scope. Copying those values from producer metadata supplies no authority. The
pure boundary snapshots nested trusted inputs before artifact callbacks.

The current actual-interval schema is a construction assumption stronger than
what the sampled adapter can provide. The SDD does not require a validated
continuous-ODE certificate. The planned numerical-stage correction is separate;
until implemented and checked, these readers must refuse substituted sampled
claims rather than convert them into interval proof. See the
[Wave 2 scope note](../../../../docs/releases/v0.5/m2-modules-construction-2026-09-09.md#evidence-and-limits).

## Recorder format

All new versioned artifacts are canonical JSON with complete fields, exact
SIMULATION content-addressed references, and bounded readers. Original canonical
contracts and protocol requests/acknowledgements retain their exact original
bytes and seals; supported execution schema is 1.0.0. A lossy projection, unknown
writer version, duplicated JSON key, missing field, or nonfinite tensor cannot
become successful construction evidence.

The frozen EpisodeRecord stays unchanged:

| EpisodeRecord reference | Versioned artifact |
| --- | --- |
| provenance_manifest_ref | EpisodeProvenanceV1 |
| trajectory_ref | ChunkManifestV1 → TrajectoryChunkV1 |
| sensor_manifest_ref | ChunkManifestV1 → SensorChunkV1 |
| action_receipts_ref | ChunkManifestV1 → ActionChunkV1 |
| safety_events_ref | ChunkManifestV1 → SafetyChunkV1 |
| metrics_ref | ChunkManifestV1 → MetricChunkV1 |

The provenance root pins the other five references and original input contracts,
actual reset request/response, termination event, trusted geometry capture,
configuration, and ancillary policy/routing artifacts. It never includes its
parent EpisodeRecord hash, so there is no hash cycle. Chunk indexes and exact
counts must cover each role contiguously. A successful task cannot have an empty
trace, absent acknowledgement, or missing physics interval. Recorded metrics are
integrity-checked but cannot substitute for metrics derived from observations.
Ancillary policy/routing artifacts receive full hash/length and trusted-inventory
checks; semantic policy/routing qualification remains a host responsibility.

Sensor chunks contain existing SDK ObservationBatch values referencing raw
RAW_LE_V1 tensors. Trace rows bind the complete batches, simulator time, wall
time and phase. Capture every physics step, including both endpoints. Required
joint channels use rad, rad/s, rad/s2 and N.m; gripper opening uses metres. Pose
translation [3] metres and quaternion [4] dimensionless values remain separate.
Task/safety field names and thresholds come from VerifierConfigurationV1, never
from defaults or producer success flags.

Action rows reference original sealed intent, prepared command, safety issuance,
ordered admission/outcome receipts and the full actual Execute request and
response bytes. SafetyIssuanceV1 preserves the M3 immutable receipt/request/
preview snapshots. Semantic correlation includes exact request payloads, scope,
lease, state, closure, prepared/safety hashes, acknowledgement reference and
captured resulting observation. An uncertain acknowledgement, failed action or
safety denial terminates the original episode; later actions are refused.

The current action-row variant starts after a PreparedCommand and safety issuance
exist. PREPARE failure cannot be filled with fabricated records. M4 must retain
complete raw protocol attempts separately and extend the versioned capture format
for failed attempts before production completeness verification. Such captures
remain INCONCLUSIVE under this bounded verifier until the required evidence is
representable and checked.

ActualSafetyRow is actual host capture, not desired-path preview. It joins two
adjacent raw observations and records continuous conservative bounds for all
joint motion/dynamics, gripper opening/dynamics, all declared bodies and contacts.
For an action, `absolute_ns = PreparedCommand.state.sim_time_ns + relative_ns`.
The first bound begins at the command's state and the last ends at the captured
acknowledgement state; time and motion must fit the signed reservation. Raw
endpoint joint/gripper values must fit the interval bounds. Exactly one unordered
body-pair bound is allowed per interval, containing aggregate force and maximum
penetration. Workspace, forbidden volumes, phase and SI force/depth limits are
checked. The future authenticated host must establish actual interior physics
bounds, body geometry and contact provenance; arbitrary supplied bounds do not
prove kinematics, physics tracking or zero execution.

TerminationPayloadV1.final_state is producer execution disposition: successful
capture awaits VERIFYING; abort/rejection is ABORTED/REJECTED. It cannot declare
ACCEPTED or verifier PASS. final_observation is a separate SDK StateBinding.

## Task, recovery and replay scope

Reach checks captured translation/orientation and the configured final hold
window. Pick/place requires an initially open, supported object; distinct finger
force observations; grasp maintained relative to the tool; lift; horizontal
transport; release; support and post-release positional stability. Capture cadence
and every distance/force/dwell tolerance are explicit configuration. These are
sampled observational predicates; no interpolated continuous goal dwell or
registered scientific thresholds are inferred.

Ordinary declared perception disturbance requires observed error in the fixed
window, recovered error after it, a changed acknowledged command, and task
completion. Planning-policy recovery and other disturbance definitions are not
silently generalized from this supported schema.

Recovery after termination additionally requires RecoveryLinkV1 whose exact
digest is supplied independently by the trusted host. It pins the original
aborted source EpisodeRecord, its original committed termination event and its
own provenance, then a distinct episode/run/lease, fresh reset, new preflight and
new exact approval. Missing or unauthenticated relation evidence is INCONCLUSIVE.
The host must authenticate both the committed source and the fresh relation.

Replay comparison requires fresh episode/run/lease and full construction checks
on both supplied captures. Commands and ordered outcome traces must match. EXACT
compares bitwise normalized observation bytes/times, actual safety intervals,
geometry and derived metrics; transient
episode/lease/wall-clock identifiers are not normalized data. TOLERANT requires
complete per-field and derived-metric tolerance coverage, separate quaternion
angular tolerance, explicit units, and exact closure/configuration. STATISTICAL
returns INCONCLUSIVE pending a registered distribution protocol. The comparison
alone does not prove a separate process executed a replay.

M4/M5 production integration must still authenticate sealed capture provenance,
independent host process/service identity, current approvals and lifecycle, exact
registry/conformance and policy qualification, actual replay execution, verifier
attestation and false-acceptance quarantine. No empirical threshold or study is
approved by the synthetic fixtures in this package's tests.
