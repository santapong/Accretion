# v0.5 simulation contract schemas

The canonical Python family lives in
[`accretion.contracts.robotics`](../../../src/accretion/contracts/robotics/__init__.py).
These JSON Schema 2020-12 files describe the ten v0.5 SDD records and eleven
supporting records. The models, schemas and synthetic fixtures form the M0
contract foundation. They do not demonstrate adapter conformance, runtime
authority, a benchmark result or any AC5 execution criterion.

## Identity and ownership

Every aggregate inherits the existing canonical header: typed contract name,
schema version, prefixed record ID, content seal, timestamp, creator, workspace
and project. Logical embodiment/adapter names remain separate from persisted
record IDs. Existing PrincipalRef, ObjectiveContractRef, NodeContractRef,
PolicyRef, VerifierRef, evidence and risk vocabularies retain their owners.

The serializer is the existing documented canonical algorithm, with its
Python/TypeScript hash vectors. It is not silently replaced by RFC 8785. Every
robotics record is project-scoped, schema major 1, and simulation risk remains
HIGH through the existing risk mapping. Unknown fields are rejected on strict
execution paths; numeric safety fields reject string/boolean coercion, NaN and
infinity.

`CanonicalWriterEnvelope` keeps original, seal-verified writer JSON separate
from a lossy read projection. New writers emit their full canonical field set.
Forwarding preserves the original compatible-minor JSON and digest; a projection
cannot be turned into execution by the envelope API. Stored readers must require
a seal before model construction. The helper is not a replacement for historical
v0.4 wire-spelling compatibility or for runtime reference/row-scope checks.

## Contract inventory

| Core record | Purpose |
|---|---|
| EmbodimentDescriptor | Immutable arm, gripper, frame and sensor facts |
| ObservationSpec | Sensor identity, modality, dtype, shape, unit and time alignment |
| ActionIntent | Typed bounded pose, arm trajectory or normalized gripper proposal |
| SafetyEnvelope | Angular arm bounds, metre-based gripper limits, explicit contact pairs/phases/force/penetration and episode caps |
| RobotAdapterManifest | Signed-artifact reference, simulation profile and requested capability versions |
| SimulationExperimentContract | Exact objective/node/embodiment/adapter references, seeds, randomization, budgets and replay class |
| SimulationEnvironmentSnapshot | Simulator/world/model/controller/configuration/host identities and sampled randomization |
| EpisodeRecord | Sealed final simulation capture, producer identity and complete artifact manifest references |
| EmbodiedVerificationSpec | Independent required deterministic task/safety/completeness/replay checks |
| AdapterConformanceReport | Independent outcome for the exact artifact/configuration/schema/tolerance dependency closure |

Supporting records are `SimulationLease`, `SimulationRunBinding`,
`SimulationPreflightReceipt`, `SimulationEpisodeApproval`,
`SimulationApprovalMatrix`, `PreparedCommand`, `SafetyDecisionReceipt`,
`SimulationActionReceipt`, `EmbodiedVerificationResult`,
`SimulationDomainEvent` and `EpisodeQuarantineRecord`.

- A manifest never embeds a report hash. The registry joins an immutable
  manifest to current conformance for an exact dependency closure, avoiding a
  circular manifest/report identity.
- A simulation lease is separate from a Git workspace lease. Opaque handles,
  fencing generation and expiry describe authority that the gateway must enforce.
  `SimulationRunBinding` identifies ownership of a real attributable run; it is
  an internal record and cannot be replaced by a provider label.
- An episode approval pins the exact preflight, seed/randomization, environment,
  adapter, safety, verifier and lease generation. A finite matrix references
  actual exact approvals and never authorizes future seeds or retries. The
  authority service must authenticate the human, check every pin and consume
  approval atomically; constructing a record grants nothing.
- Prepared commands contain a shared typed trajectory or gripper payload and a
  matching content-addressed blob reference. Trajectories carry ordered arm
  joints, monotonic times, angular position/velocity/acceleration and effort
  upper bounds. They never encode gripper sliders as radians. Descriptor matching,
  interpolation and actual physics/safety validation belong to later services.
- A safety signature covers the complete typed unsigned decision with a
  versioned domain separator; the outer canonical seal commits to the body and
  detached signature. `verify_safety_signature` additionally checks an explicit
  trusted key-to-principal mapping. It provisions no keys. Hash consistency is
  not signature authentication, and a valid signature does not bypass policy,
  expiry, lease or state checks.
- Domain events support pre-run registration and use an explicit bounded payload
  digest. Mutable episode state, outbox delivery and trace/SSE projection belong
  to persistence/services. The immutable EpisodeRecord is final evidence.
- Content-addressed artifact values preserve media type, byte size, retention and
  evidence class. They do not weaken the older run/path ArtifactRef. Final episode
  artifacts are SIMULATION and cannot be relabeled PHYSICAL.

## Regeneration and verification

```bash
uv run --no-sync python scripts/export_contract_schemas.py --release v0.5
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/export_v05_contract_fixtures.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v05_contracts.py
```

The shared exporter checks both families by default and preserves the existing
v0.4 schema bytes. The [fixtures](../../../tests/fixtures/contracts/v0.5) contain
minimal, complete, invalid and unknown-major examples for each record. Their
digests, principals, approvals, conformance outcomes and signatures are explicitly
**synthetic schema fixtures**, not production evidence. The deterministic test
signing key is public test material and must never be installed as a trusted
evaluator key. No private runtime credential is included.

The [contract tests](../../../tests/test_v05_contracts.py) exercise writer-seal
preservation, unknown-version refusal, coercion and unit/shape errors, contact
policy, signature tampering, exact approval structure, command/blob binding and
independence assertions. A schema can express shape, types and bounds; cross-field
hash/signature checks and dereferenced runtime authority still require the Python
validators and enforcing services.

## Change rule

The approved v0.5 M0 decisions and active release SDD govern the implementation;
the [imported v0.5 reference](../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
remains unchanged. After the M0 freeze, a semantic schema change requires a
versioned migration/compatibility review with historical writer fixtures,
authority analysis and rollback. Future readers must preserve original evidence;
no migration may make denied execution allowed, turn unresolved verification
into acceptance or promote simulation evidence into physical evidence.
