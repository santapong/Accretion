# Changelog

All notable changes to Accretion are documented in this file.

## [Unreleased]

Status: v0.3 milestones M0–M4 delivered on `develop`; parked 2026-08-26 before M5.

### Added

- Added the v0.3 M0 connection-aware capability layer: connector, connection,
  and binding contracts, the capability resolver, and migration 0010, with
  every v0.1/v0.2 capability resolving unchanged (#57).
- Added the v0.3 M1 identity layer: principals keyed by issuer and subject,
  workspaces and memberships, an OIDC Authorization Code + PKCE client with a
  fake IdP for tests, session middleware with a `LOCAL_PRINCIPAL` default mode,
  `/me` and `/auth` routes, and migration 0011 (#58).
- Added the opencode runtime adapter as the third governed runtime (#59).
- Added the acceptance-criteria harness: `docs/acceptance/criteria.toml`
  records how each SDD criterion is verified, tests claim criteria with
  `@pytest.mark.acceptance`, and `make acceptance` computes the status instead
  of documents asserting it (#63, #74).
- Added the v0.3 M2 token broker and OAuth connections: the encrypted secret
  store and master key, single-use OAuth transactions, broker-backed capability
  invocation, the GitHub connector with the `connect`, `oauth/callback`,
  `reauthorize`, `revoke`, and `health` routes, principal-bound runs, and
  migration 0012 (#62, #75, #77).
- Added the v0.3 M3 remote MCP manager with authenticated MCP SDK v2 HTTP
  discovery and invocation, explicit canonical capability mappings, durable
  per-connection discovery snapshots, server lifecycle/audit records, health
  state and circuit breaking, workspace-admin lifecycle APIs, and migration
  0013 (#79).
- Added executable acceptance coverage for `AC3-MCP-02` through
  `AC3-MCP-08`, plus a real SDK v2 ASGI server test and PostgreSQL migration
  round-trip coverage. Existing local stdio coverage continues to prove
  `AC3-MCP-01`.
- Added the v0.3 M4 plugin manager: `MetaPluginManifest` alongside the unchanged
  `MetaPlugin` registry projection, the SDD §20.3 nine-state lifecycle behind a
  single audited transition, Ed25519 and digest-pinned trust levels with a
  risk-to-trust floor, connector dependency resolution, workspace-scoped
  installations over a global immutable version registry, install / enable /
  disable / upgrade / rollback / remove routes, an append-only
  `GET /api/v1/audit/plugins` trail, two bundled fixture packages, and migration
  0014.
- Added executable acceptance coverage for `AC3-PLG-01` through `AC3-PLG-06`,
  including a structural test asserting `StateStore` exposes no deletion method
  beyond `delete_secret_record`, so "removal cannot delete evidence" fails the
  moment one is added.
- Added the `docs/runbooks/v03-plugins.md` operator runbook, carrying ADR3-M4-001
  (SDD §20.3 adopted over §9.2 for the plugin state machine).
- Added milestone acceptance gates to CI: `check_acceptance.py --stage M1`
  through `--stage M4` now run after the backend test suite.

### Changed

- Capability resolution now treats disabled remote bindings and unavailable MCP
  server lifecycle states as non-executable, and remote calls pass through the
  existing Accretion authorization and credential boundaries.
- `WorkspaceRole` and principal status now change outcomes: a disabled
  principal is refused at the capability boundary, not only at HTTP (#77).
- Generated frontend API types now include the connection, identity, and M3
  MCP server lifecycle routes.
- `.env.example` documents the identity (`AUTH_MODE`, OIDC, session), remote
  MCP endpoint-policy, and plugin trust settings.
- `GET /api/v1/plugins` now filters by workspace membership. Built-in registry
  rows stay visible to everyone; a row contributed by an installation is visible
  only to members of the workspace that installed it. Before M4 every
  authenticated principal saw every registry row, including another tenant's.
- Capability resolution now treats a disabled plugin's capabilities as
  non-executable. The resolver gates on the owning installation's state, so a
  capability re-flagged `enabled=True` by hand still does not resolve while its
  plugin is disabled, removed, or awaiting connector setup.

### Security

- Claude Code runs now carry a sandbox and a meaningful tool allowlist, closing
  the runtime egress asymmetry with Codex and opencode (`V01-P4-001`, #78).
- Hardened the connection surface and made the token audience/issuer guard
  fail closed (#61, #77).
- The OAuth callback stays behind the session middleware so the returning
  browser must be the session that began the flow; unknown, replayed, and
  expired states return one indistinguishable response (#77).
- Remote MCP endpoint registration requires HTTPS (except explicitly enabled
  loopback development endpoints), rejects credentials/query fragments, checks
  hostname and port policy, and rejects every non-public DNS answer before each
  network operation. Redirects and ambient proxy credentials are disabled.
- Discovered tool schemas are checked before publication; credentials remain
  ephemeral; authorization failures atomically expose `AUTH_REQUIRED` and
  `REAUTH_REQUIRED`; remote listings and results are bounded by configured item,
  time, and response-size limits.
- The M2 secret scan's OpenTelemetry guard now verifies that Accretion does not
  instrument OpenTelemetry and no tracer provider or SDK is configured, since
  the MCP SDK makes `opentelemetry-api` a transitive dependency.
- Plugin manifests are requests, not grants (ADR3-006). Every capability a
  package declares is put through the existing `CapabilityPolicyEngine`; the
  complete grant set is computed before anything is registered; a denied
  capability is never registered at all, and a package whose requests are only
  partly granted installs `DISABLED` while one whose requests are wholly denied
  installs `FAILED`. No plugin gains authority automatically.
- Upgrade and rollback re-run the full policy evaluation against the new
  manifest rather than inheriting the previous verdict, so a later version that
  adds a permission must earn it on its own merits.
- MCP servers declared by a plugin manifest are registered disabled and pass the
  same M3 endpoint policy as operator-registered servers, so a manifest cannot
  reach an endpoint the M3 routes would have refused.
- Packages are verified before installation: the canonical manifest digest is
  checked against the pinned digest, detached Ed25519 signatures are verified
  against operator-configured keys, and a capability's risk level sets a minimum
  trust floor. Unsigned packages install only when explicitly permitted, and a
  `SHA256_PIN` alone never confers authorship.
- Consent must echo the manifest digest the administrator was shown and may
  narrow but never widen what policy granted.
- Removal never deletes evidence. Disable and remove flip capability and binding
  flags without deleting rows; `StateStore` exposes exactly one deletion method
  in the whole interface (`delete_secret_record`), a structural test asserts it
  gains no second, and migration 0014 introduces no `ON DELETE CASCADE`.

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
