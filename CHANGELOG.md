# Changelog

Notable changes to Accretion, organized by release and user impact.
Unreleased changes are available on `develop`; versioned releases are tagged
from `main`. Detailed milestone entries are retained in the
[engineering change archive](docs/releases/engineering-change-history.md).

## [Unreleased]

Post-v0.4.1 maintenance and the completed, approved v0.4 closure. No new release
tag or change to the published v0.4.1 tree is implied.

### Fixed

- Serialize shared exploration-budget admission so concurrent AUTO requests
  cannot independently admit work against the same allowance. Recover durable
  observed costs and overruns after restart, including conservative revision
  handling; malformed accounting is refused.
  ([#179](https://github.com/santapong/Accretion/pull/179))
- Preserve verified stored-writer identities when projecting older contracts
  for reads. Validate seals and scope, retain historical references, and refuse
  unknown projected inputs at execution and accounting boundaries.
  ([#179](https://github.com/santapong/Accretion/pull/179))
- Synchronize Python dependency requirements with `uv.lock`, resolving the
  failing lock checks in five Dependabot requests. Use Dependabot's `uv`
  ecosystem for future Python updates.
  ([#180](https://github.com/santapong/Accretion/pull/180))

### Changed

- Integrate all ten dependency requests from #169–#178, including Vitest 5,
  pytest-cov 7.1 and updates to Pydantic, Alembic, cryptography, React Flow and
  frontend tooling. The combined update passed the existing suites and an
  explicit coverage-plugin check.
  ([#180](https://github.com/santapong/Accretion/pull/180))
- Refresh the README, project banner and core diagrams; present release
  summaries separately from the complete engineering history.

### Security

- Pin `js-yaml` 4.3.2 under `@redocly/openapi-core` 1.34.19 to address
  [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh).
  The dependency review recorded zero npm audit findings after the fix.
  ([#180](https://github.com/santapong/Accretion/pull/180))

### Documentation and research

- Import the frozen v1.1–v1.8 design and research package, with integrity
  validation for all 187 files. These are forward designs, not implemented
  release capabilities.
  ([#168](https://github.com/santapong/Accretion/pull/168))
- Reconcile the completed v0.4 milestones and closure evidence. Record a scoped
  NO-GO for the full routing-benefit claim while retaining the partial paired
  synthetic result. Add isolated fake-pilot preparation with
  PASS/FAIL/INCONCLUSIVE evidence and zero provider calls.
  ([#179](https://github.com/santapong/Accretion/pull/179))

**Compatibility:** no public contract or database migration changes. Routing
stays off by default. Budget accounting is cumulative normalized cost; drain or
disable older AUTO writers before a mixed-version rollout. See the
[accounting runbook](docs/runbooks/v04-routing-accounting.md),
[closure record](docs/releases/v0.4/closure-execution-2026-09-08.md) and
[dependency review](docs/releases/v0.4/dependency-review-2026-09-09.md).

## [0.4.1] - 2026-09-07

Hardening of evidence-aware routing and the registered benchmark analysis.

### Fixed

- Include run-projected experience in training snapshots by resolving the
  underlying `experience_id`, while preserving retraction handling.
  ([#161](https://github.com/santapong/Accretion/pull/161))
- Handle release-branch pushes consistently in the browser CI gate, with an
  explicit release-bridge exception for the computed-style comparison.
  ([#160](https://github.com/santapong/Accretion/pull/160))

### Changed

- Align default routing utility weights with the registered benchmark weights,
  using one shared constant for selection and objective creation.
  ([#162](https://github.com/santapong/Accretion/pull/162))
- Make benchmark pooling an explicit registered choice and report the rule
  alongside each gate result.
  ([#163](https://github.com/santapong/Accretion/pull/163))

### Research

- Confirm and freeze amendment 1, then perform its authorized locked and drift
  corpus re-read. Preserve the original results alongside the amendment,
  including expectations that did not hold. The scientific access log contains
  four rows in total; no priced routing-provider experiment was run.
  ([#164](https://github.com/santapong/Accretion/pull/164))

[Release notes and upgrade guidance](docs/releases/v0.4/notes.md) ·
[Audit and baseline](docs/releases/v0.4/baseline.md)

## [0.4.0] - 2026-09-07

**Evidence-aware node configuration routing.** Delivered milestones M0–M10.

### Added

- Frozen configuration contracts and compatibility gates, with deterministic
  receipt-first selection across eligible AGENT, TOOL and VERIFIER nodes.
- Verified experience and feedback pipelines, offline ranking and calibration,
  project adapters and controlled cold start.
- Shadow evaluation by branched rollout, guarded exploration with circuit
  breakers and budget accounting, and append-only promotion/rollback records.
- Experiment Studio views for routing decisions, shadow comparisons, evidence
  and router administration.
- A preregistered routing benchmark with locked and drift corpora, preserved
  outcomes, ablations and explicit limits on the research claim.

### Changed

- Ship the completed operator stylesheet port within v0.4.0. The parked v0.3.1
  UI ladder was not a separately tagged release.

**Release evidence:** 167 criteria in scope, 159 Python-proven, five frontend
criteria and three recorded manual witnesses; zero unmet MUST criteria and all
five release-gate conditions passed. Node routing is opt-in, the fallback
catalog is FAKE-only, and live-provider routing benefit is not established.

[Release notes](docs/releases/v0.4/notes.md) · [Release audit](docs/releases/v0.4/audit.md)

## [0.3.0] - 2026-09-01

**Governed integrations and operator administration.**

### Added

- Connection-aware capability bindings, workspace identity, OIDC/SSO and session
  management with a local-principal default.
- An encrypted token broker, the GitHub connector lifecycle and authenticated
  remote MCP discovery and invocation.
- Workspace-scoped plugin installation, trust and dependency resolution, and
  connector-attributed research evidence.
- Operator administration for plugins, connections, MCP servers, capability
  inspection and identity.
- Optional enterprise-managed authorization through the existing policy and
  connection-isolation boundaries.
- An executable five-condition release gate and automated browser accessibility
  verification.

[Release notes](docs/releases/v0.3/notes.md) · [Release audit](docs/releases/v0.3/audit.md)

## [0.2.0] - 2026-08-24

**Validated dynamic workflows, bounded search and verified experience.**

### Added

- P5 typed workflow proposals, deterministic graph validation, bounded repair,
  static fallback, immutable revisions and safe replanning.
- P6 best-of-N, hypothesis, cross-provider and generator-reviewer search with
  isolated candidates, shared budgets and independent ranking.
- P7 verified-experience materialization and retrieval, negative guidance,
  compatibility checks and isolated replay with a fresh control.
- Operator views, runbooks and frozen benchmarks for each research track.

The three capabilities are opt-in. Read the release audit for their measured
scope and recorded browser exception.

[Release notes](docs/releases/v0.2/notes.md) · [Release audit](docs/releases/v0.2/audit.md)

## [0.1.0] - 2026-08-22

**The deterministic local control-plane foundation.**

### Added

- Codex and Claude runtime control with normalized durable events.
- Deterministic DIRECT/LOOP/GRAPH/HYBRID planning, bounded feedback loops,
  validated static graphs, approvals and disposable Git worktrees.
- Independent verification, governed capability/MCP execution and durable
  side-effect evidence.
- A React operator interface with authoritative snapshots and resumable SSE.
- The frozen 30-task ACR-ARCH benchmark and recorded live-provider calibration.

### Security

- Deny-by-default external capability policy and task-scoped provider tools.
- Credential values excluded from model context and serialized API/event
  payloads, with durable side-effect intent recorded before dispatch.

[Release notes](docs/releases/v0.1/notes.md) · [Frozen baseline](docs/releases/v0.1/baseline.md)

[Unreleased]: https://github.com/santapong/Accretion/compare/v0.4.1...develop
[0.4.1]: https://github.com/santapong/Accretion/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/santapong/Accretion/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/santapong/Accretion/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/santapong/Accretion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/santapong/Accretion/tree/v0.1.0
