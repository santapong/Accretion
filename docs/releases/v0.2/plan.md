# Accretion v0.2 delivery plan

Status: implementation complete for the v0.2.0 candidate. P5, P6, P7, their
four frozen research suites, and the eleven-route operator frontend pass the
automated candidate checks. Promotion remains blocked by the browser/accessibility
gate and one-time branch-ancestry reconciliation recorded in the
[release audit](audit.md).
This document translates the normative
[v0.2 SDD](../../sdd/Accretion_SDD_v0.2.md) into an implementation and review sequence;
it does not change the SDD contracts.

<img src="../../assets/v02-roadmap.svg" alt="v0.2 roadmap from the completed v0.1 gate through P5 dynamic workflows, P6 bounded routing and search, P7 verified experience, and the final research release gate" width="100%" />

## Release thesis

v0.2 will let Accretion propose and revise run-specific workflows, choose among
runtimes using observable evidence, spend bounded compute on multiple candidates,
and reuse verified prior experience. Dynamic behavior may change computation
structure, but it never changes authority ceilings, credential handling,
acceptance policy, or durable-state ownership.

## Entry gate

The v0.1 prerequisites are now present. Their canonical identifiers and frozen
surfaces are recorded in the [v0.1 baseline](../v0.1/baseline.md):

- immutable `v0.1.0` release tag and published release evidence;
- deterministic static strategy baseline and validated templates;
- normalized runtime protocol with fake, Codex, and Claude adapters;
- verifier-gated execution, checkpoints, replay, governance, and operator views;
- frozen ACR-ARCH inputs and reproducible baseline reports.

P5 began from the recorded `develop@bb249f5` integration snapshot. Its required
SDD choices are captured in the [P5 decision record](../../research/p5/decisions.md). The
immutable `v0.1.0` release remains the experimental control and compatibility
floor; the moving `develop` branch is not the v0.1 baseline.

## Scope and sequence

### P5 — Validated dynamic workflow proposals

Implementation status: merged into `develop` by
[PR #35](https://github.com/santapong/Accretion/pull/35) behind opt-in gates.
Operational details and evidence are in the [P5 runbook](../../runbooks/p5-dynamic-workflows.md) and
[P5 acceptance report](../../research/p5/acceptance.md). The missing release-level
static-versus-dynamic research condition is now closed by the
[frozen P5 benchmark](../../research/p5/benchmark.md).

Outcome: a model may propose graph structure, but deterministic code decides
whether that structure is admissible and executable.

Deliverables:

- versioned `WorkflowProposal`, node, edge, validation, and graph-revision contracts;
- deterministic `GraphValidator` with bounded topology and authority checks;
- proposal persistence, checksums, provenance, and immutable revision history;
- replan triggers that preserve completed work and never replay side effects;
- operator UI for proposal, validation findings, revisions, and replan reason;
- static-template fallback for every proposal or revision failure.
- frozen dynamic-versus-static cohort benchmark with preregistered utility and
  non-inferiority thresholds.

Exit evidence:

- adversarial invalid graphs fail before execution;
- valid proposals instantiate repeatably from the same frozen inputs;
- crash/reconcile tests preserve revision identity and graph cursor state;
- every P3 checkpoint/replay test remains green.

### P6 — Evidence-based routing and bounded search

Implementation status: completed across the
[contracts/persistence PR #37](https://github.com/santapong/Accretion/pull/37),
[executor/recovery PR #38](https://github.com/santapong/Accretion/pull/38), and
[operator/research PR #39](https://github.com/santapong/Accretion/pull/39).
Operational details and evidence are in the [P6 runbook](../../runbooks/p6-candidate-search.md),
[decision record](../../research/p6/decisions.md), and
[P6 acceptance report](../../research/p6/acceptance.md).

Outcome: Accretion can compare a small number of candidates without hiding the
cost, selection evidence, or losing trajectories.

Deliverables:

- interpretable runtime-decision records based on health, compatibility, prior
  observed results, and declared constraints;
- versioned `SearchPlan` and `CandidateTrajectory` contracts;
- bounded best-of-N, hypothesis, cross-provider, and generator-reviewer modes;
- a `REPLAY_BRANCH` contract that remains fail closed in P6-only deployments and
  activates only through P7 compatibility/applicability evidence;
- hard shared budgets across candidates, including wall time and tool calls;
- independent verifier ranking with explicit ties and inconclusive outcomes;
- UI views for candidate lineage, spend, evidence, and final selection.

Exit evidence:

- search never exceeds the persisted shared budget;
- the static v0.1 strategy is retained as the control treatment;
- all candidate traces, including rejected and failed candidates, remain queryable;
- routing and search reports preserve negative and null results.

### P7 — Verified experience retrieval and replay

Implementation status: experience contracts/persistence merged in
[PR #41](https://github.com/santapong/Accretion/pull/41), deterministic retrieval
and context selection merged in [PR #42](https://github.com/santapong/Accretion/pull/42),
and replay/operator/benchmark evidence completed in
[PR #43](https://github.com/santapong/Accretion/pull/43) behind the independent P7
gate. Operational details are in the [P7 runbook](../../runbooks/p7-verified-experience.md),
[decision record](../../research/p7/decisions.md), and [acceptance report](../../research/p7/acceptance.md).

Outcome: prior evidence can inform a new run without becoming unreviewed policy.

Deliverables:

- versioned experience, query, match, and applicability records;
- retrieval over task structure, environment, strategy, and verifier evidence;
- explicit negative knowledge and negative-transfer measurement;
- trajectory replay that creates a new evidenced candidate rather than copying a
  prior acceptance decision;
- operator explanations for why an experience matched and how it affected a run;
- retention, invalidation, and provenance rules.

Exit evidence:

- unverifiable or stale experiences cannot influence execution;
- retrieval improves a preregistered treatment without increasing false accepts;
- negative transfer is measured and blocks the release gate when material;
- no experience is automatically promoted into policy, skills, or permissions.

## Cross-cutting workstreams

| Workstream | Required throughout v0.2 |
|---|---|
| Contracts | Additive versioning; preserve v0.1 identifiers and audit references |
| Persistence | Forward and reverse migrations; immutable proposal/revision/candidate evidence |
| Security | Deterministic capability ceilings and credential isolation remain authoritative |
| Verification | Calibrate ranking reliability and false-accept risk before expanding search |
| UI | Complete for P5–P7 on `develop`; retain snapshot-first projections and provenance for every dynamic decision |
| Benchmark | Static control, preregistered treatments, negative/null results, fixture hashes |
| Operations | Recovery classification for proposals, revisions, candidates, and retrieval |

## Frontend delivery status

<img src="../../assets/operator-ui-map.svg" alt="Completed v0.2 frontend with eleven routes for planning, live operation, governance, and ACR-ARCH and P5 through P7 research, backed by typed snapshots and resumable events" width="100%" />

The frontend work planned for P5–P7 is complete in the release candidate. Planning Review
supports experience selection, dynamic proposal validation, and bounded search
attachment; Live Run explains graph state, controls, verifier evidence, P6
candidate lineage, and P7 materialization/replay provenance; dedicated pages
reproduce all four frozen research suites. The generated OpenAPI contract,
ESLint, TypeScript, 22 component tests, and production build pass for the v0.2
release candidate.

No additional feature surface is required to close P7. The remaining frontend
release work is reproducibility: rebuild and retest from the final clean checkout
used by the v0.2 audit. The current bundle-size advisory is non-blocking and must
remain visible; signed-in live-provider calibration stays opt-in.

## Proposed pull-request slices

1. P5 contracts and database migrations — complete.
2. Pure GraphValidator plus adversarial fixtures — complete.
3. Proposal persistence and static fallback — complete.
4. Dynamic graph instantiation and revision-safe checkpointing — complete.
5. P5 operator surfaces and acceptance report — complete.
6. Runtime evidence and `SearchPlan` contracts — complete.
7. Bounded candidate executor and shared-budget accounting — complete.
8. Candidate comparison UI and P6 research report — complete.
9. Experience records, retrieval, invalidation, and negative knowledge — complete.
10. Replay integration and P7 UI/benchmark evidence — complete.
11. P5 research benchmark and v0.2 release audit — candidate complete; external
    promotion gates remain open.

Each slice must be independently reviewable, retain a disabled or static fallback
until its release gate passes, and include schema, migration, API, UI, recovery,
and benchmark evidence where applicable.

## Frozen and remaining decisions

- P5 graph grammar, topology bounds, reusable state, and fallback behavior are
  frozen in the [P5 decision record](../../research/p5/decisions.md).
- P6 ranking, shared-budget, cancellation, isolation, promotion, and recovery
  behavior are frozen in the [P6 decision record](../../research/p6/decisions.md).
- P7 representation, invalidation, replay/recovery, and benchmark behavior are
  frozen in the [P7 decision record](../../research/p7/decisions.md). Expansion requires a new
  ADR and cannot rewrite the preregistered v0.2 evidence after the fact.

## Explicit non-goals

v0.2 does not include reinforcement-learned routing, self-modifying architecture,
automatic promotion of experience into policy, unrestricted production
deployment, or the full plugin/identity ecosystem assigned to v0.3.

## Release gate

Release v0.2 only when the P5–P7 acceptance evidence is reproducible from a clean
checkout, the dynamic treatment demonstrates preregistered benefit over the v0.1
static control, verifier safety does not regress, negative-transfer results are
reported, all inherited v0.1 gates pass, and the operator can explain every
proposal, revision, candidate, retrieval, and terminal decision from durable
records. The [frontend guide](../../guides/frontend.md) is the route-level checklist for
that operator explanation gate.
