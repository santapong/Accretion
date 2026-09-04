# Accretion v0.4-v1.0 Codex Handoff

**Repository:** https://github.com/santapong/Accretion/tree/develop  
**Baseline inspected:** `develop@9b5997751011eabb0b2f06ebb4597450f4a3f037` on 2026-08-20  
**Package role:** Guarded forward architecture and research specification  
**Immediate implementation authorization:** None beyond the user's separately stated request

---

## 1. Read this before coding

This package defines the future Accretion architecture from v0.4 through v1.0. It is technically detailed so later releases remain coherent. It is **not** permission to skip the repository's unfinished v0.1 phases or implement all releases at once.

At the inspected baseline, the repository is still in v0.1:

- P0 runtime feasibility: implemented;
- P1 deterministic task profiling/strategy planning: implemented;
- P2 LOOP execution: planned;
- P3 GRAPH/HYBRID execution: planned;
- P4 harness/capability/verification/observability release gate: not yet complete.

The current run manager correctly blocks non-`DIRECT` execution until the owning phases exist. Preserve that behavior until the required implementation and tests are complete.

## 2. What Accretion is

Accretion is an **evidence-governed Adaptive R&D Meta-Harness and Experiment Studio** for developer-researchers.

It should feel approachable like Codex or Claude Code, but it is not initially another foundation-model coding runtime. It coordinates replaceable runtimes such as Codex and Claude, tools, skills, plugins, verifiers, workflows, experiments, and structured evidence.

Its permanent product promise is:

> A user gives a rough research or development goal. Accretion proposes a versioned ObjectiveContract, constructs and executes a governed workflow, selects compatible execution configurations, independently verifies results, preserves evidence and contradictions, and learns cautiously from verified experience.

## 3. Non-negotiable invariants

1. An incorrect result accepted as correct is the highest-severity product failure.
2. Backend project/run/evidence state is authoritative; chat and UI are control surfaces.
3. Claude, Codex, and other models are replaceable workers, not authorities.
4. A model, plugin, adapter, or learned policy cannot grant permissions or expand its authority.
5. Verification is defined before execution; a producer cannot be its sole verifier.
6. Deterministic evidence is first, independent model judgment second, and unresolved cases require human review.
7. Contradictory evidence is preserved and blocks unsafe promotion until resolved.
8. Every automatic loop has hard resource caps and an expected-value improvement threshold.
9. Objective changes are versioned, impact-analyzed, and human-approved before affecting new runs.
10. Physical/high-risk experiments require one exact human approval for every individual trial.
11. Online learning/exploration is prohibited on physical/high-risk execution.
12. React Flow is a backend-derived read-only execution projection.
13. Plugins request capabilities; installation is not authorization.
14. Models receive opaque capability/connection references, never raw tokens or robot-control credentials.
15. Robotics begins after Software/AI foundations and progresses simulation → governed physical → bounded cross-embodiment.

## 4. Required release sequence

| Order | Release | Unlock requirement | Primary result |
|---:|---|---|---|
| 1 | v0.1 | Current work | Reliable static observable meta-harness |
| 2 | v0.2 | v0.1 gate | Validated dynamic workflow synthesis/revision/search/experience |
| 3 | v0.3 | v0.2 gate | Plugin/MCP/identity/token-broker platform |
| 4 | v0.4 | v0.3 gate | Learned node-level execution configuration |
| 5 | v0.5 | v0.4 gate | Robotics simulation and embodiment substrate |
| 6 | v0.6 | v0.5 gate plus safety case | Individually approved physical trials |
| 7 | v0.7 | v0.6 gate | Verified bounded cross-embodiment transfer |
| 8 | v0.8 | v0.7 gate | Learned graph planning |
| 9 | v0.9 | v0.8 gate | Joint but authority-separated orchestration |
| 10 | v0.10 | v0.9 gate | Human-promoted guarded capability evolution |
| 11 | v1.0 | All gates | Stable integrated R&D operating system |

Do not create an implementation backlog for a locked release.

## 5. Package map

```text
00_READ_ME_FIRST.md
01_GOVERNANCE/
  Accretion_Golden_Direction_v0.4.md
  Accretion_Direction_and_SDD_Audit_v0.4_to_v1.0.md
  Accretion_Cross_Release_Contract_Registry_v0.4_to_v1.0.md
  Accretion_Roadmap_v0.6_to_v1.0.md
02_SDDS/
  Accretion_SDD_v0.4.md
  Accretion_SDD_v0.5.md
  Accretion_SDD_v0.6.md
  Accretion_SDD_v0.7.md
  Accretion_SDD_v0.8.md
  Accretion_SDD_v0.9.md
  Accretion_SDD_v0.10.md
  Accretion_SDD_v1.0.md
03_RESEARCH/
  Accretion_v0.4_Research_Protocol.md
04_BACKGROUND/
  Accretion_v0.5_Robotics_Charter.md
  Accretion_v0.6_Physical_Robotics_Charter.md
  Accretion_v0.7_Cross_Embodiment_Charter.md
  Accretion_v0.8_to_v1.0_Technical_Package_Index.md
MANIFEST.sha256
```

Normative order inside the package:

1. Golden Direction;
2. cross-release contract registry;
3. currently unlocked SDD;
4. earlier released SDDs/ADRs;
5. locked future SDDs;
6. research protocol;
7. background charters.

## 6. First Codex task

Before changing code, Codex should produce a short repository gap analysis for the currently authorized release:

1. verify the current commit and dirty-worktree state;
2. read repository `AGENTS.md`/instructions if present;
3. read `docs/sdd/Accretion_SDD_v0.1.md` and the current implementation;
4. identify the next unimplemented v0.1 milestone and acceptance criteria;
5. map existing code and tests to that milestone;
6. list contract migrations, API changes, and frontend changes;
7. call out conflicts with the cross-release registry;
8. propose a bounded implementation plan;
9. wait for user authorization if the requested task was only review/planning.

The v0.4-v1.0 documents may guide interface seams, naming, and future-proofing, but must not cause features from later releases to be shipped early.

## 7. Repository placement recommendation

When the user authorizes documentation integration, place these files under a clearly forward-looking path such as:

```text
docs/sdd/future/v0.4-v1.0/
```

Keep the package structure or provide an index that preserves normative/background separation. Do not overwrite the existing v0.1-v0.3 SDDs.

The integration should be a documentation-only commit unless the user separately authorizes code changes.

## 8. Engineering rules for implementation

- Reuse canonical contracts rather than creating parallel lookalikes.
- Add migrations instead of rewriting historical records.
- Keep frontend types generated from or tested against backend schemas.
- Use deterministic compatibility/policy pruning before any learned ranking.
- Preserve candidate sets, propensities, snapshots, decisions, evidence, and verifier lineage.
- Treat `FAIL`, `INCONCLUSIVE`, `ERROR`, and `QUARANTINED` as non-passing.
- Fail closed on unknown authority/safety enum values and major schema versions.
- Use idempotency keys and optimistic concurrency for mutable APIs.
- Keep raw secrets/tokens out of model contexts, logs, events, and artifacts.
- Use content digests for runtimes, models, tools, skills, adapters, environments, and verifiers.
- Block physical capability registration before v0.6 and block physical exploration permanently.
- Never allow learned capability evolution to modify protected control-plane or safety surfaces.

## 9. Testing expectations

For every milestone:

- unit tests for contracts, validation, policy, and state transitions;
- property tests for caps, idempotency, compatibility pruning, and hash stability;
- integration tests across backend, persistence, events, runtime/capability adapters, and UI projection;
- adversarial tests for authority expansion, verifier leakage, reward hacking, prompt injection, token exposure, and replay;
- migration/replay tests against historical fixtures;
- end-to-end acceptance test tied to the active SDD gate;
- backend and frontend required checks before handoff.

Robotics milestones additionally require adapter fault injection, clock/time alignment, stale/duplicate command behavior, evidence typing, approval replay tests, safe-stop/lockout drills, and independent safety review.

## 10. Change-control rules

If implementation evidence invalidates a future SDD assumption:

1. preserve the evidence;
2. open a typed design issue or ADR;
3. identify affected contracts/releases;
4. propose a versioned SDD/registry revision;
5. include authority, safety, migration, benchmark, and rollback impact;
6. obtain user approval before changing the active release scope.

Do not silently “fix” the Golden Direction to match convenient implementation.

## 11. GitHub authority

This package was prepared through read-only inspection. It does not authorize Codex to:

- push commits;
- create or update pull requests/issues;
- merge branches;
- change branch protection, CI, secrets, collaborators, or repository settings;
- publish releases or packages.

Those actions require an explicit user request.

## 12. Handoff conclusion

The package follows the project's direction. Its most important operational instruction is:

> Use v0.4-v1.0 to preserve the destination, but implement only the next unlocked evidence-gated step from the actual repository state.

---

## Unlock log

| Date | Release | Decision |
|---|---|---|
| 2026-09-05 | v0.4 | Unlocked by the owner. Preconditions evidenced on `develop@00765e5`: release gate 5/5 (`in scope: 117 proven: 111 unmet MUST: 0`); Golden Direction accepted. The v0.4 SDD moved to [`docs/sdd/Accretion_SDD_v0.4.md`](../../Accretion_SDD_v0.4.md) and is normative there; this package remains the forward reference for v0.5-v1.0 and is no longer the home of the v0.4 text. |
