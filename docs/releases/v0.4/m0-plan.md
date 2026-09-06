# v0.4 M0 — Contract and feature freeze

SDD v0.4 §19: *schemas, hashes, migrations, fixtures approved.* Nothing routes in M0. It makes the
fourteen v0.4 contracts and `ObjectiveContract` exist as validated, hashable, persisted,
fixture-backed schemas, and registers v0.4 in the acceptance harness. The decisions are
ADR-051..059 in [SDD v0.4 §21](../../sdd/Accretion_SDD_v0.4.md); this page is the build order. M0 claims no
acceptance criterion (ADR-052): its proof is that the later milestones prove theirs against
frozen contracts.

## Ladder

| PR | Content | Proof |
|---|---|---|
| PR0 — unlock and governance | SDD moved to `docs/sdd/`, §20 as `AC4-M<owner>-0NN` tables, ADR-051..058, registry note, forward-package unlock log and manifest, harness binding (`v0.4` file, `AC4` rows, `v0.4-M<n>` stages), fifty `not_yet_due` policy rows, pins 167/165, these pages | harness suite; `check_acceptance.py` still `in scope: 117` |
| PR1 — contracts package and canonical hashing | `contracts.py` → `contracts/__init__.py` byte-identical; `contracts/canonical.py` (ADR-056); `contracts/refs.py` (registry §4 refs incl. `ApprovalArtifactRef`; `PrincipalRef`, `PluginRef`, `ConnectionRef`, `ArtifactRef` reused); the two agent definitions and the `m6-deviations` line citation that name `contracts.py` | full suite unchanged; `openapi.json` unchanged (`git diff --exit-code`); committed hash vectors read by the tests; `content_hash` excluded from its own input; key order irrelevant; mutation: default separators break every vector |
| PR2 — the v0.4 contract family | `contracts/routing.py`, an **enumerated inventory of nineteen models**: `ObjectiveContract`, `ObjectiveContractRef`, `NodeContract`, `VerificationSpec`, `TaskFeatures`, `ProjectFeatures`, `RoutingContext`, `ExecutionConfiguration`, `ConfigurationCandidate`, `CompatibilityDecision`, `StructuredExplanation`, `RoutingDecisionReceipt`, `IndependentVerificationResult`, `ExperienceRecord` (projection over the P7 `Experience`), `FailureEvent`, `RouterModelVersion`, `RouterTrainingSnapshot`, `RouterPromotionReport`, `ShadowDecision`; enums `VerificationState`, `RiskClass` (+ `risk_level_for`), `FailureType`, `FailureOwner`, `DecisionType`, `ConstructionStage`, `CompatibilityStatus`, `SubjectType`, `Visibility`, `ContradictionStatus`, `RouterScope`, `RouterStatus`, `PromotionDecision`; `ids.py` prefixes (`ccd` for candidates); JSON Schema 2020-12 export under `docs/contracts/v0.4/` | fixture tests parametrized over the inventory (minimal, complete, invalid, unknown-version per model - a forgotten model is a red test); property tests: any field change changes the hash, unknown major rejected, `UNKNOWN` never compatible, independence flags cannot be false, receipts reject secret-shaped values; `risk_level_for` total with `PROHIBITED` raising; every id prefix unique; `ArtifactRef.run_id` still required; committed schemas equal regenerated ones per model |
| PR3 — persistence freeze | models and migration `0017_v04_m0_routing_contracts` (fifteen additive tables, §13.1 constraints incl. the two partial unique indexes mirrored in `MemoryStore`, reversible); append-only store methods on the `StateStore` protocol that refuse to overwrite an id or a hash; `m0-freeze.md` with every committed schema digest | `alembic upgrade → downgrade 0016 → upgrade` clean; memory/Postgres parity through the existing protocol test; immutability (a different payload under the same id raises, a byte-identical put is a no-op, a revision is a second row and both list); no criterion claimed; `in scope` stays 117 |

Each PR runs the repository team shape: implementer, then the gate chain and a read-only contract
review in parallel, at most one repair round, a local checkpoint, review, push, merge on green.

## Reconciliations the freeze must respect

- Registry precedence: header fields (§3), typed references (§4), enums (§5) override the SDD's
  illustrative YAML where they differ; `NodeContract` gains the registry §7.2 minimums.
- Name collisions (ADR-054): `IndependentVerificationResult`; `VerificationState` beside the v0.1
  `VerificationStatus`; `ExperienceRecord` keyed by the P7 `experience_id`; `CompatibilityDecision`
  beside the P7 `CompatibilityAssessment`; `RiskClass` beside `RiskLevel` with a total mapping;
  `EvidenceClass` reused; `ApprovalArtifactRef` new, `ArtifactRef` untouched.
- Identity: prefixed base32 ids, not UUIDs (ADR-055). Versioning: semver, unknown major rejected,
  `extra="forbid"` until a second writer exists (ADR-057). OQ-420: no reserved slot (ADR-059).
- Nothing from the research protocol's §21 pre-registration list is frozen in M0.

## Exit

```bash
uv run --no-sync python scripts/release_gate.py
uv run --no-sync python scripts/export_contract_schemas.py --check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin tests/contracts tests/test_v04_m0_*.py
```
The `--stage v0.4-M<n>` form stays a local diagnostic; CI gates the whole harness.
