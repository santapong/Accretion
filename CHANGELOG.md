# Changelog

All notable changes to Accretion are documented in this file.

## [Unreleased]

## [0.2.0] - 2026-08-24

### Added

- Added a newcomer-focused project overview, a complete operator frontend guide,
  and accessible repository-native SVGs for release orientation and the eleven-route
  UI/data-flow map.
- Added a developer documentation hub, accessible architecture and lifecycle
  diagrams, and a deterministic public-API showcase using the fake runtime.
- Added an actionable v0.2 P5–P7 delivery plan tied to the normative v0.2 SDD.
- Added opt-in P5 validated dynamic workflows with typed proposals, deterministic
  graph validation, one bounded repair, static fallback, immutable revisions,
  safe replanning, runtime-decision evidence, and operator inspection
  ([PR #35](https://github.com/santapong/Accretion/pull/35)).
- Added opt-in P6 candidate-search contracts and PostgreSQL persistence for
  versioned plans, shared budgets, candidate trajectories and scores, runtime
  provenance, and crash-reconcilable promotion records
  ([PR #37](https://github.com/santapong/Accretion/pull/37)).
- Added the bounded P6 executor with best-of-N, hypothesis, cross-provider, and
  generator-reviewer modes, isolated worktrees and sessions, independent
  verifier ranking, hard parent-owned budgets, cancellation, and conservative
  restart recovery ([PR #38](https://github.com/santapong/Accretion/pull/38)).
- Added P6 planning and candidate-lineage operator views, including status,
  provider/runtime/model/version, reviewer, score, quality, cost/latency proxies,
  actual spend, terminal reason, and final selection
  ([PR #39](https://github.com/santapong/Accretion/pull/39)).
- Added a deterministic 12-task P6 replay benchmark with frozen fixture hashes,
  N=1/2/4 quality-vs-compute curves, provider comparison, preserved null results,
  API endpoints, and an operator research page.
- Added the P6 runbook, developer showcase, acceptance report, decision record,
  and accessible repository-native lifecycle and quality/compute diagrams.
- Added opt-in P7 immutable experience contracts, controlled procedural
  segments, deterministic 384-dimensional embeddings, exact pgvector retrieval,
  compatibility/transfer-risk evidence, retraction, negative knowledge,
  operator-frozen `ContextBundle` v2 selection, and additive APIs
  ([PR #41](https://github.com/santapong/Accretion/pull/41),
  [PR #42](https://github.com/santapong/Accretion/pull/42)).
- Added P7 `REPLAY_BRANCH` execution with one fresh control, one new isolated
  candidate per selected positive seed, revalidated negative avoidance guidance,
  durable seed/start/rejection evidence, repeated launch/selection/promotion/
  recovery checks, and fail-closed stale-seed pruning without substitution
  ([PR #43](https://github.com/santapong/Accretion/pull/43)).
- Added P7 planning, provenance, replay-lineage, and benchmark operator views;
  runbook, showcase, acceptance report, and accessible lifecycle/gate diagrams.
- Added a frozen 20-task, 50-source, 80-trace P7 benchmark with four treatments,
  false-accept and negative-transfer accounting, stale rejection, quality/compute
  uplift, use/rejection/null rates, negative cases, replay-only API endpoints,
  and exact fixture fingerprints.
- Added the missing frozen 12-task, 24-trace P5 static-versus-dynamic benchmark,
  replay-only API, operator route, cohort utility/non-inferiority gate, explicit
  invalid-proposal fallback evidence, and accessible release diagram.

### Changed

- Reorganized documentation into purpose-owned guides, runbooks, research,
  releases, and governance folders; added an authoritative experiment/results
  index, maintenance rules, and an accessible research-evidence diagram.
- Reworked the project README and active documentation hub, developer guide,
  showcases, and P0–P7 runbooks around the v0.2 release scope and immutable
  v0.1 static-control evidence.
- Updated the Python, frontend, and GitHub Actions toolchains to their current
  compatible releases.
- Reworked UI project defaults and event-stream state to comply with the current
  React Hooks correctness rules without duplicating query-backed state.
- Updated the operator shell and project documentation to describe implemented
  P5/P6/P7 scope while retaining `v0.1.0` as the immutable static control.
- Activated `REPLAY_BRANCH` only when P7 is independently enabled and selected
  experience passes current compatibility and applicability checks; P6-only
  deployments retain the original fail-closed reservation behavior.
- Bumped every public package and runtime version marker to `0.2.0` and aligned
  release-facing documentation with the audited eleven-route frontend.
- Made Claude execution independent of user hooks and plugins through safe mode,
  honored the typed session model override, and recorded the selected live
  calibration model alongside provider versions.

### Known limitations

- The supported browser-control surface had no connected browser during release
  finalization. No rendered route, responsive, keyboard, focus, or automated
  accessibility PASS is claimed; post-release evidence is tracked in
  [issue #52](https://github.com/santapong/Accretion/issues/52).

### Security

- Updated pytest to 9.1.1, resolving CVE-2025-71176 in development test runs.
- Kept speculative P6 candidates inside isolated workspaces with protected
  external side effects and permission expansion denied.
- Required independently verified unique selection before promotion, persisted
  cancellation before interruption, re-evaluated policy before patch application,
  and recorded parent-before/after digests for recovery.
- Restricted P7 evidence to redacted deterministic procedure, excluded patches,
  transcripts, credentials, native sessions, capability arguments/results,
  approvals, permissions, and side-effect state, and required compatibility
  revalidation before replay can launch, win selection, promote, or recover.

## [0.1.0] - 2026-08-22

### Added

- Provider-neutral Codex and Claude runtime control with normalized durable events.
- Deterministic task profiling and static DIRECT/LOOP/GRAPH/HYBRID selection.
- Bounded verifier-gated loops, validated workflow templates, checkpoints, replay,
  approvals, and isolated Git worktrees.
- Governed capability/MCP boundary, credential broker, and idempotent side-effect
  evidence.
- Complete React operator surfaces with snapshot-first resumable SSE.
- Reproducible 30-task ACR-ARCH benchmark and balanced live provider calibration.

### Security

- Deny-by-default external capability policy and task-scoped provider tool exposure.
- Credential values excluded from model context and serialized API/event payloads.
- Durable side-effect intent is recorded before execution and terminal result after.
