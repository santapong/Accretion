# Changelog

All notable changes to Accretion are documented in this file.

## [Unreleased]

Theme: **M9 operator-UI redesign**, shipping as v0.3.1. Entries are backfilled for
the three milestone PRs that merged before this file was updated.

The M9 ladder is parked after its stylesheet port; the entries below it are v0.4 work.

### v0.4 — M0 contract and feature freeze

- Unlocked the v0.4 SDD (evidence-aware node configuration routing): it moved from the
  forward package to `docs/sdd/Accretion_SDD_v0.4.md`, its fifty acceptance criteria became
  `AC4-M<owner>-0NN` rows the harness reads, ADR-051..059 record the freeze decisions, and
  every criterion starts `not_yet_due` under its owning milestone (#118).
- Turned `accretion.contracts` into a package — `contracts.py` became
  `contracts/__init__.py` byte-identically, so every existing dotted import still
  resolves — and added the two cross-release foundations the later v0.4 contracts build
  on: `contracts/canonical.py` implements ADR-056 canonical JSON and `content_hash`
  (sorted keys, no whitespace, UTF-8, integers as integers, decimals as strings, RFC 3339
  UTC `Z`, non-finite numbers refused, `content_hash` omitted from its own input) against
  committed golden vectors the tests read, and `contracts/refs.py` adds the registry §4
  typed references that did not exist yet — `RuntimeRef`, `CapabilityRef`, `ToolRef`,
  `SkillRef`, `EnvironmentRef`, `VerifierRef`, `EvidenceRef`, `PolicyRef` and
  `ApprovalArtifactRef` — while `PrincipalRef`, `PluginRef`, `ConnectionRef` and
  `ArtifactRef` are reused unchanged (#121).
- Froze the v0.4 contract family in `contracts/routing.py`: nineteen contracts
  (`ObjectiveContract`, `ObjectiveContractRef`, `NodeContract`, `VerificationSpec`,
  `TaskFeatures`, `ProjectFeatures`, `RoutingContext`, `ExecutionConfiguration`,
  `ConfigurationCandidate`, `CompatibilityDecision`, `StructuredExplanation`,
  `RoutingDecisionReceipt`, `IndependentVerificationResult`, `ExperienceRecord`,
  `FailureEvent`, `RouterModelVersion`, `RouterTrainingSnapshot`, `RouterPromotionReport`,
  `ShadowDecision`) exported as `CONTRACT_INVENTORY`, all inheriting the registry §3 header
  through a new `CanonicalContract` base that seals its own ADR-056 digest and rejects an
  unknown schema major; fifteen registry §5 enums with the total `risk_level_for` mapping;
  the fourteen ADR-055 id prefixes; seventy-six golden fixtures (minimal, complete, invalid,
  unknown-version) carrying real content hashes; and nineteen committed JSON Schema 2020-12
  documents under `docs/contracts/v0.4/` with an export-and-`--check` script. No table,
  migration, store method, route or UI file changes (#122).
- Froze v0.4 persistence: migration `0017_v04_m0_routing_contracts` adds the fourteen
  tables SDD v0.4 §13 lists plus `objective_contracts`, which §7.1 requires and ADR-058
  counts — fifteen additive tables in all (`objective_contracts`, `node_contracts`,
  `verification_specs`, `routing_requests`, `configuration_candidates`,
  `compatibility_decisions`, `routing_receipts`, `routing_overrides`,
  `verification_results`, `experience_records`, `failure_events`,
  `router_model_versions`, `router_training_snapshots`, `router_promotion_reports`,
  `shadow_decisions`) carrying the §13.1 constraints —
  unique `(content_hash, schema_version)` on every table, a unique `routing_request_id`
  on receipts, and the repository's first two partial unique indexes for "one ACTIVE
  workspace router per workspace" and "one ACTIVE adapter per project and algorithm",
  both mirrored in `MemoryStore`; every foreign key is `ON DELETE RESTRICT` so nothing
  cascades into provenance, and `experience_records` is keyed by the P7 `experience_id`
  it projects. The `StateStore` protocol gains append-only `put_`/`get_`/`list_` methods
  for each table — implemented identically in `MemoryStore` and `PostgresStore`, refusing
  a different payload under a stored id or a stored digest, treating a byte-identical
  repeat as a no-op, and offering no `update_` or `delete_` anywhere. Every `list_` takes
  `workspace_id` as a required keyword with no default, so a v0.4 listing cannot be taken
  across tenants by forgetting an argument. The receipt rule and the two ACTIVE-router
  rules are mirrored in Python on both backends too, each inside the same lock or
  transaction as the insert it guards, so a second receipt for one routing request raises
  `ValueError` rather than escaping `PostgresStore` as a driver `IntegrityError`;
  `MemoryStore` also mirrors the two foreign keys, refusing a record that names a project
  or an experience it does not hold.
  `routing_overrides` — the one §13 table PR2 froze no contract for — stores a
  *pre-contract* record: its type marker is `document_type`, held deliberately outside the
  frozen `accretion.<contract>` namespace so it makes no promise M2's `RoutingOverride`
  would have to keep, its ids come from a new `routing_override` -> `rov` kind rather than
  the v0.1 strategy-override `ovr`, its retry check ignores the store-stamped clock so an
  at-least-once redelivery is a no-op (an *explicitly supplied* `created_at` is part of
  the identity a retry is compared on), and its shape is frozen by a committed golden
  document with a recorded digest. The freeze record classifies M2's future
  `RoutingOverride` as a **Major, fail-closed** replacement of that pre-contract record
  rather than an additive Minor change, and records the discrimination rule: a row
  carrying `document_type` is an M0 pre-contract record and must never be fed to a
  contract's `model_validate`. Plus `docs/releases/v0.4/m0-freeze.md` recording the
  sha256 of every committed contract schema, the table that stores it and the migration
  that created it, checked by a test that also asserts every one of the fifteen tables has
  a frozen shape. The migration is reversible and no existing table, model, route or UI
  file changes (#123).

### Added

- Added repairable vitest anchors: `make anchors` re-addresses the test pointers
  in `docs/acceptance/criteria.toml` after a frontend test file moves, so a
  comment added above a test no longer invalidates the acceptance record (#109).
- Added Tailwind v4 and the v0.3.1 design tokens with **zero rendered diff**:
  the import is written out explicitly minus preflight, `@theme static` forces
  the fourteen tokens into the stylesheet, and content detection is scoped to
  `apps/ui`. Every pre-existing rule survives byte-identically (#110).
- Added a real-browser accessibility gate: `apps/ui/e2e/a11y.spec.ts` and the
  `browser` CI job sweep all seventeen routes on the production build with
  axe-core's full default ruleset, one `h1` per route, no horizontal overflow at
  390 px, and WCAG AA on every text node. It found and fixed five real
  `scrollable-region-focusable` violations on its first run (#111).
- Added a bundle-size budget enforced by `vite build`: `apps/ui/budget/`
  measures every emitted chunk, prints a table on every build, and fails the
  build on any of five rules — per-chunk raw size, initial JS raw and gzip,
  initial CSS raw and gzip, vendor grouping identity, and lazy-only modules.
  `apps/ui/vite.config.ts` splits the app into an app chunk plus four static
  vendor chunks so no chunk exceeds 500,000 B, with the stylesheet cascade
  provably unchanged. Discharges the v0.2 chunk-size advisory carried in
  `docs/releases/v0.3/backlog.md` (#113).
- Added a computed-style diff gate and began the Tailwind port. The gate builds
  the merge-base with `develop` alongside the branch and compares every element's
  computed styles between the two on 17 routes x 5 widths, plus a scripted
  focus/hover pass and two fixture-mocked pages that render the gate, loop,
  candidate-search, experience-transfer and revision rules the seed never reaches
  — `apps/ui/e2e/style-diff.spec.ts`, `styleDiff.ts` and its mutation table,
  `computedStyleProbe()` in `audit.ts`, `make style-diff-base`, and a required
  step in the `browser` CI job that fails rather than skips when the base is
  missing. `apps/ui/e2e/cssPort.test.ts` proves the same thing textually, against
  a pinned byte-identical copy of the pre-migration stylesheet, for the rules no
  browser pass renders. On that evidence the fonts `@import` and 113 of the 441 rules — the
  `:root` tokens, the bare element rules, the shell and nav, forms and buttons,
  page chrome, the runtime cards, `.pill`, the registry, history and audit chain,
  the run and event lists, and their `@media` entries — moved byte-verbatim from
  `apps/ui/src/styles.css` into `@layer components` in `apps/ui/src/theme.css`,
  with the palette, font and breakpoint tokens for the current design declared
  inert beside the cosmic ones. Zero computed-style differences across every route,
  width, interaction and mocked page (#116).
- Added the second Tailwind port slice: the planning review, the task studio, the
  four benchmark screens and the P6/P7 planner rules — `styles.css:35`, `:45-50`
  (including the bare `details`/`summary` rules), `:96`, `:183-188`, `:221-236`,
  `:245-274`, the 900 px block at `:275`, and their entries in the 1100/900/620 px
  blocks — moved byte-verbatim from `apps/ui/src/styles.css` into `@layer
  components` in `apps/ui/src/theme.css`. 224 of the 441 rules now live there and
  the run page is all that remains. The eight comma-joined lists that name a
  planner selector and a run-page `.experience-lineage*` selector moved whole
  rather than being split, which brings the `!important` at `:236` and the more
  specific rule it beats at `:231` across together. `apps/ui/e2e/fixtures/benchmarks.ts`
  and four new fixture-mocked passes put the benchmark rules in front of the
  computed-style probe with a task detail open and a replay status rendered —
  regions no unattended sweep can reach. Zero computed-style differences across
  every route, width, interaction and mocked page (#117).
- Completed the Tailwind port and deleted `apps/ui/src/styles.css`. The run page's
  217 remaining rules — the section heading, the page and inspector stacks, the 25
  `.pill-*` states, the execution, loop and verification chrome, the P3 approval
  gate, the M5 dynamic-workflow inspectors and replan control, the `.search-inspector`
  and `.candidate-*` families, `.experience-lineage/-capture`, the M6 diff, router
  and revision rules, and the 720 px block — moved byte-verbatim into `@layer
  components` in `apps/ui/src/theme.css`, and the two `@media` blocks that PR5b had
  to split are whole again. The 36 rules that style elements inside the React Flow
  canvas moved instead into a new **unlayered** `apps/ui/src/react-flow.css`,
  imported from `RunExecution.tsx` on the statement immediately after
  `@xyflow/react/dist/style.css`: xyflow's stylesheet is unlayered, so those rules
  would lose to it from inside a layer at any specificity, and several beat it only
  by coming later at equal specificity. `button:disabled` is there for the same
  reason — it is the rule whose move into a layer the gate caught in PR5a as
  `/runs/:runId @ 390: #148 button cursor not-allowed -> pointer`. The partition is by
  competition, not by location: `.iteration-badge`, `.gate-waiting-hint` and the
  `.pill-*` states render inside a node and are layered, because xyflow says nothing
  about them. `apps/ui/e2e/cssPort.test.ts` now spans both surviving sheets and, with
  the pre-migration file gone, its union check is the completeness proof for the whole
  migration: all 441 rules exist exactly once, in order, none edited. It also asserts
  the canvas partition in both directions, that `react-flow.css` contains no `@layer`,
  and the import adjacency; `style-diff.spec.ts` gained a built-output assertion that
  xyflow's `.react-flow__node-default` precedes ours and that neither ours nor
  `button:disabled` is inside a layer. `App.tsx` drops its second stylesheet import.
  Zero computed-style differences across every route, width, interaction and mocked
  page (#120).
- `apps/ui/e2e/style-diff.spec.ts` now answers both runtime endpoints from a fixture on
  every route, so `/` and `/runtimes` render four fixed runtimes covering all four `.pill-*`
  colour groups rather than whatever the local machine's CLIs report at that second;
  `/runtimes` is seed-independent and its element floor an equality rather than a margin.
  The probe storm and status churn that motivated it are fixed on the backend in their
  own change (#120).
- Added a cascade-inversion case to `apps/ui/e2e/cssPort.test.ts`, derived from the
  pinned sheet rather than from a hand-kept list. For every pair of rules — one now
  unlayered, one now in `@layer components` — that can match the same element and
  declares a common property with a different value, it fails when the layered rule
  used to win on specificity or source order, because an unlayered rule now beats it
  regardless. Thirty-four pairs are examined and one is exempted, with its reason
  recorded beside it: `button:disabled` (unlayered, so it can beat xyflow's
  `.react-flow__controls-button {cursor:pointer}`) now wins over
  `.benchmark-table td button` (layered), so a disabled button in a benchmark-table
  cell shows `not-allowed` where it used to show `pointer`. Both halves of that pair
  already sat on their current sides of the layer boundary on `develop`, so the port
  neither introduces it nor can fix it without changing a declaration; the repair
  belongs to the token-substitution follow-up.
- Added `apps/ui/src/routes.tsx` as the single source of both the navigation
  bar and the router, and moved the eleven pages still defined inside
  `App.tsx` into `apps/ui/src/pages/`, leaving `App.tsx` thirteen lines. Every
  moved body is byte-identical, the built stylesheet's sha256 is unchanged, and
  `OperatorShell.test.tsx` now guards the three things the move could have
  broken silently: the nav/route pairing, `end` on `/` alone, and the
  stylesheet import order React Flow's cascade depends on. Recorded as
  ADR3-M9-001 in `docs/runbooks/v03-operator-ui.md` (#114).

### Fixed

- Fixed the runtime health probe storm and the kill race it exposed. `GET /api/v1/runtimes`
  shelled out to `codex`, `claude` and `opencode` on every request, and `runtime_sessions`
  re-probed every runtime just to resolve a `runtime_id`, so one five-second tick of the
  runtime monitor asked for up to N + N·N live probes — roughly sixty subprocess spawns and a
  minute of wall time. Probes that overlapped hit their own five-second deadline and reported
  UNAVAILABLE or DEGRADED for a CLI that was merely slow, and `command_result` killed children
  that had already exited, raising `ProcessLookupError` out of `health()` as a 500. Probes are
  now memoized for thirty seconds behind a single-flight lock (`probe_result`,
  `PROBE_CACHE_SECONDS`), the kill race is suppressed, and the runtime's own
  `active_runs`/`active_sessions` counters stay live because only the subprocess results are
  cached: the operator reads a status at most thirty seconds old against the page's five-second
  poll. The tests count spawns through a tally file and prove the single flight and the expiry
  (#119).

## [0.3.0] - 2026-09-01

Theme: **Plugin, MCP & Identity Integration Platform**. Full notes in
[docs/releases/v0.3/notes.md](docs/releases/v0.3/notes.md); the release audit and
its disclosed limitations are in
[docs/releases/v0.3/audit.md](docs/releases/v0.3/audit.md).

Acceptance at release: 117 criteria in scope, 111 proven by a passing claiming
test, 3 by the frontend suite, 3 by a recorded live-provider run, **0 uncovered
and `unmet MUST: 0`**. All five SDD §24.8 release-gate conditions pass. The
per-milestone figures quoted in the entries below are historical — each records
what was true when that milestone merged.

Three criteria (`V01-P0-002`, `V01-P0-004`, `V01-P4-008`) are `manual` records
backed by a real signed-in provider run and **expire on 2027-02-28**.

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
  through `--stage M6`, plus `--stage v0.2-ui`, now run after the backend test
  suite.
- Added the v0.3 M5 research intelligence plugin: the bundled
  `accretion-research` package declaring five skills and five canonical
  capabilities over two deliberately divergent MCP backends, the SDD §7.6
  transform seam that normalizes both wire shapes into one `EvidenceCandidate`
  stream, the `research_evidence` Evidence Store with content-addressed
  deduplication and migration 0015, three research verifiers behind a new
  `EXTERNAL_EVIDENCE` verification target, and the `EvidenceClass` /
  `EvidenceTrust` / `EvidenceProvenance` / `EvidenceCandidate` /
  `EvidenceRecord` / `CitationCheck` contracts.
- Added `WorkflowNodeSpec.capability_refs` and carried it through
  `_materialize_node` into `RunManager`, closing SDD §27's exit criterion: a
  dynamic workflow now names a canonical capability id and nothing else, and
  the resolver and gateway decide which connector serves it. The field is
  additive and optional, so every template persisted before M5 still
  deserializes and an empty list is the pre-M5 execution path unchanged.
- Added `GET /api/v1/runs/{run_id}/research-evidence`, a read-only projection
  of a run's Evidence Store in the store's deterministic
  `(created_at, evidence_id)` order, gated by workspace membership.
- Added executable acceptance coverage for `AC3-RES-01` through `AC3-RES-04`,
  including a backend swap proven to change exactly the `enabled` field on two
  binding rows, a poisoning test in which a payload claiming
  `"trust": "VERIFIED"` still stores as unverified, and a ranking test in which
  an unverified record with `similarity = 1.0` still sorts below a verified
  record with `similarity = 0.01`.
- Added the `docs/runbooks/v03-research.md` operator runbook, carrying
  ADR3-M5-001 (SDD §10 adopted over §9.1 for the research capability surface,
  because §9.1's `research.citation.resolve` resolves rather than verifies and
  so cannot satisfy `AC3-RES-01`) and ADR3-M5-002 (`github.search` in,
  `python.execute` out, enforced by a test rather than by prose).
- Added the research settings to `.env.example` and `config.py`. The plugin is
  off by default behind two independent gates:
  `ACCRETION_ENABLE_RESEARCH_PLUGIN` and an
  `ACCRETION_RESEARCH_ALLOWED_HOSTS` allowlist that starts empty, so enabling
  the plugin alone opens no upstream egress.
- Added the v0.3 M6 administration surface: five operator routes under
  `/admin` — Connections, Plugins, MCP servers, Capability inspector, and
  Identity — each with an `h1`, rendering only projections the API already emits.
  No page can display a token, a refresh token, or a token handle, because no
  route it calls returns one.
- Added `GET /api/v1/mcp/servers/{mcp_server_id}/discovery`, returning the most
  recent `McpDiscoverySnapshot` so SDD §16.3's discovered tools, prompts, and
  cache TTL have a path to the browser. Additive over M3 contracts: no new
  contract, table, or migration. It never contacts the server, and 404s
  identically for a non-member, an unknown server, and a server that has never
  discovered.
- Added capability badges on the React Flow run nodes and in their accessible
  summary mirror, a graph diff that names every added, removed, and changed node
  **and edge**, and a router inspector rendering `fallback_order` and
  `observed_features`. The last two close the `V02-UI-003` and `V02-UI-006`
  specification mismatches the acceptance baseline recorded as open.
- Added executable acceptance coverage for `AC3-UI-01` through `AC3-UI-05` and
  for the six inherited `V02-UI-001..006` criteria. Every criterion in the three
  SDDs is now in scope: `NOT_YET_DUE` is empty for the first time, and
  `make acceptance` reports `in scope: 110   proven: 96   unmet MUST: 10`, all
  ten inherited v0.1/v0.2 items.
- Added `frontend_evidence` to `docs/acceptance/criteria.toml` and the acceptance
  harness: a criterion proven by pytest can now name the vitest test carrying the
  rendering half of its proof, and the pointer is checked by path, `:line` anchor,
  and exact test title. Deleting a page test or retitling one by a byte fails the
  gate. `verification = "frontend"` evidence is now checked the same way instead
  of being any non-empty string.
- Added the `docs/runbooks/v03-frontend-admin.md` operator runbook, carrying
  ADR3-M6-001 (the proof of a page is split between pytest and vitest, and the
  split is machine-checked), ADR3-M6-002 (`GET .../discovery` is added though
  SDD §17 does not list it), ADR3-M6-003 (identity is read-only; session
  enumeration and the enterprise authorization panel are M7), and ADR3-M6-004
  (a node badge names the plugin from its synthetic connector; manifest-declared
  `node_badges` is M8).

- Added the v0.3 M7 enterprise-managed authorization path, optional and off by
  default behind `ACCRETION_ENABLE_ENTERPRISE_AUTH` plus a configured
  `ACCRETION_ENTERPRISE_AUTH_TOKEN_EXCHANGE_URL`: signing in once retains the
  principal's `id_token`, sealed against its own auth session and bounded by the
  token's own `exp`; an invocation of a centrally managed MCP server exchanges it
  for an identity assertion grant (RFC 8693 `id-jag`) and presents that grant to
  the connector's authorization server (RFC 7523 `jwt-bearer`), minting a real
  `Connection` and `TokenHandle` so revocation, isolation, health and audit are
  the unchanged M2 implementations. With the flag down the deployment is
  byte-identical to the pre-M7 one: no manager is constructed, nothing is
  retained, and no exchange or grant call is made.
- Added the `IdentityAssertion` and append-only `EnterpriseAuthGrant` contracts,
  their `identity_assertions` and `enterprise_auth_grants` tables, and migration
  0016. No field was added to any existing persisted model, and the store gained
  no deletion surface: revocation destroys the sealed assertion through the
  existing `delete_secret_record` and marks the row `REVOKED`, so the row remains
  as evidence (AC3-PLG-05 stays a closed structural fact).
- Added `GET /api/v1/enterprise-auth/profile`,
  `POST /api/v1/mcp/servers/{mcp_server_id}/enterprise-authorize`, and the
  append-only `GET /api/v1/audit/enterprise-auth` trail, all under
  `/api/v1/enterprise-auth/` or the existing MCP prefix rather than
  `/api/v1/auth/`, which is exempt from the session middleware. None of them can
  return an identity assertion, an enterprise grant, or an access token.
- Added the enterprise authorization panel to the Identity admin page and an
  "Authorize (enterprise)" action to the MCP servers page, both of which state a
  disabled deployment outright rather than rendering an empty panel.
- Added executable acceptance coverage for `AC3-EMA-01` through `AC3-EMA-07`,
  including a flag-off run proving zero exchange and grant calls after a real
  sign-in, three deliberately malformed assertions producing three distinct
  `REFUSED_*` outcomes before the authorization server is contacted, a
  second principal receiving his own connection and handle rather than the
  first's, a mid-session token expiry renewing with `grant_calls == 2` and a
  `REFRESHED` row, and a three-sentinel secret scan over events, envelopes,
  bundles, the three new routes, grant `detail`, and the OpenTelemetry export.
  `make acceptance` now reports `in scope: 117   proven: 103   unmet MUST: 10` —
  the ten unchanged inherited v0.1/v0.2 items.
- Added the `docs/runbooks/v03-enterprise-auth.md` operator runbook, carrying
  ADR3-M7-001 (the EMA criteria are SDD §24.9, appended rather than renumbering
  the release gate), ADR3-M7-002 (the retained `id_token`, its accepted risk and
  its five tested mitigations), ADR3-M7-003 (EMA mints a real connection, and
  re-acquisition lives in the broker so `get_access_material` stays the single
  expiry authority), and ADR3-M7-004 (the assertion row is evidence and
  revocation never deletes it), plus the configuration guide and the exact
  verification commands.
- Added the enterprise settings to `.env.example` and `config.py`. The flag alone
  opens nothing: without a token-exchange URL the subsystem is inert, and a
  connector absent from `ACCRETION_ENTERPRISE_AUTH_AUDIENCES` cannot be
  enterprise-authorized.
- Added `--stage M7` to CI, and made an empty stage selection an explicit
  non-zero error, so a stage gate can no longer pass by selecting no criteria.

### Changed

- `EncryptedTokenBroker` gained `register_reacquirer(connector_id, fn)`, consulted
  inside `refresh()` before the no-refresh-token failure. A jwt-bearer grant returns
  no refresh token, so without the hook every enterprise handle would expire at the
  end of its first lifetime; with it, `get_access_material` remains the single
  authority on expiry and a mid-session expiry renews with no user interaction. The
  hook refuses any connector whose `auth_type` is not `EMA`, so an interactively
  consented handle can never be resealed with enterprise authority.
- `RemoteMcpManager._authorization()` gained one additive branch: an `EMA` connector
  with no `ACTIVE` connection mints one, and a refusal marks the server
  `AUTH_REQUIRED` exactly as an unauthorized OAuth connector does. Everything below
  it is untouched, and the flag-off event sequence is byte-identical to M6's.
- `IdentityService.complete_login` retains the assertion when the flag is on, and
  `logout` destroys it before revoking the auth session row.
- `apps/ui/src/api.ts` gained the M6 client functions (plugin detail,
  installations and audit, connectors and connections with connect / reauthorize
  / revoke / health, MCP servers with capabilities and discovery, capability
  resolution, workspaces, auth providers), and `MeResponse` moved from a
  hand-written interface in `api.ts` to the generated schema types. vitest
  fixtures are typed as `components["schemas"][...]`, so backend schema drift
  breaks `npm run check` rather than producing a green suite over a stale shape.
- The read-only `/capabilities` registry page now links to the capability
  inspector, because a capability listed there may still be unresolvable.
- The API process now builds its `VerifierRegistry` explicitly, including
  `research_verifiers(store)`, and wires a `GatewayCapabilityInvoker` onto the
  run manager. Both closed the same class of gap: a component that resolved in
  tests and raised in production because only the test supplied it.
- The MCP gateway process now passes `default_transform_registry()` to
  `CapabilityGateway`, so a binding's `output_transform_ref` resolves in the one
  process that actually serves capability calls to a running agent.

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

### Release hardening (M8)

- Closed the ten inherited unmet MUST acceptance criteria. Seven needed a
  claiming test rather than a behaviour change: the graph validator's cycle,
  fan-out, denied-capability, privilege-expansion and risk-expansion branches,
  the six search stop reasons, the N=1,2,4 quality curve and the benchmark
  version axes were all implemented and simply unclaimed, so a regression would
  not have named what it broke.
- `V02-P7-003` is now proven against the real `ExperienceService.assess()`, with
  all 19 compatibility reason codes provoked by distinct single-variable
  perturbations and a guard test that fails if a code is added without coverage.
- Added `scripts/release_gate.py` and `make release-gate`, making each of SDD
  §24.8's five conditions independently executable and independently failable.
  `capability_policy_bypass` is derived from `CapabilityGateway` audit rows and
  `secret_exposure_incidents` from the secret-scan suites (ADR3-M8-002).
- Added `scripts/live_acceptance.py`, which produces a dated evidence document
  from a real signed-in Codex and Claude run. `V01-P0-002`, `V01-P0-004` and
  `V01-P4-008` are recorded as `manual` criteria expiring 2027-02-28.
- Hardened the acceptance harness before widening its authority: an unreadable
  waiver end date now counts as expired, waivers need an ISO date inside 180
  days, a failing claimed test outranks any recorded belief, and a claimed test
  that reports no outcome classifies `FAILING` rather than `PROVEN`
  (ADR3-M8-001).
- Pinned all four ACR-ARCH fixtures to literal digests — `config.v1.json` and
  `environments.v1.json` were previously unhashed — and proved the three version
  axes move independently (ADR3-M8-004).
- CI now gates the full unscoped acceptance harness plus the release gate,
  replacing eight stage-scoped gates that each re-ran the whole suite and could
  report PASS over an empty scope. A `clean-checkout` job proves the result
  reproduces from a fresh clone with no caches.

### Fixed

- Closed the four accessibility findings inherited from v0.2 (F1–F4). axe-core
  4.10.2 now reports zero violations across all seventeen routes, no route
  scrolls horizontally at 390 px, and none of 1,421 measured text nodes falls
  below WCAG AA. Two of the findings were partly misdiagnosed and the
  corrections are recorded in
  [browser-a11y-evidence.md](docs/releases/v0.3/browser-a11y-evidence.md): the
  status-pill palette always passed AA (pills lost their colour to a more
  specific `.panel-header > span` rule), and the five `/admin/*` pages already
  had an `h1`.
- Fixed the M6 administration pages scrolling the document sideways at 390 px:
  their registry tables had no horizontal scroll container.
- Introduced the stylesheet's first CSS custom properties (`--ink-dim`,
  `--ink-muted`, `--ink-amber`); every colour had previously been a repeated
  literal.
- Isolated `tests/test_p5_postgres_store.py`, which failed on a second run
  against the same database and was green in CI only because each run used a
  fresh container.

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
