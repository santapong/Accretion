# v0.3 prioritized backlog

Status: M0–M7 delivered; work is parked before M8. Every M0–M7 acceptance
criterion is proven by a claiming test (`make acceptance`), and the per-stage gates
`--stage M1` through `--stage M7`, plus `--stage v0.2-ui`, all report
`in scope: n   proven: n   unmet MUST: 0`. No criterion is `not_yet_due` any more.
The remaining uncovered criteria are inherited v0.1/v0.2 items listed in the
[acceptance baseline](acceptance-baseline.md).

The normative contract remains [Accretion SDD v0.3](../../sdd/Accretion_SDD_v0.3.md).
This ledger orders its existing milestones without changing acceptance criteria
or unlocking the hash-manifested v0.4+ designs.

> **M8 complete as of 2026-09-01.** All ten inherited unmet MUSTs are closed and
> the acceptance harness reports `unmet MUST: 0`. Everything still listed below is
> deliberately deferred to v0.4 — none of it is an acceptance criterion, and each
> item would have added contracts, routes or migrations during a release freeze.
> See the [release-hardening runbook](../../runbooks/v03-release-hardening.md) for
> the decisions, and the [release audit](audit.md) for the disclosed limitations.

## Delivery order

| Priority | Milestone | Outcome required before continuing |
|---:|---|---|
| 1 | M0 capability/connection refactor — **delivered** (#57) | Existing v0.1/v0.2 capabilities resolve unchanged through additive connection-aware contracts |
| 2 | M1 identity and SSO — **delivered** (#58) | Issuer/subject principals, workspace membership, OIDC Authorization Code + PKCE, and multi-user tests |
| 3 | M2 token broker and OAuth connections — **delivered** (#62, #75, #77) | Encrypted refresh/revoke lifecycle with no token values in model, API, UI, events, or logs |
| 4 | M3 remote MCP manager — **delivered** (#79) | Authenticated remote discovery, schema validation, health/circuit breaking, SSRF controls, and canonical capability bindings |
| 5 | M4 plugin manager — **delivered** | Versioned manifest validation, permission requests without grants, enable/disable/upgrade, and historical provenance |
| 6 | M5 research intelligence plugin — **delivered** | Provider-neutral research capabilities, normalized evidence, provenance, and citation verification |
| 7 | M6 frontend and administration — **delivered** | Plugins, Connections, MCP Servers, capability resolution, identity, and roles without exposing secrets |
| 8 | M7 enterprise authorization — **delivered** | Optional EMA behind `enable_enterprise_auth`: sign in once, RFC 8693 exchange to an ID-JAG, RFC 7523 jwt-bearer grant, a real `Connection` + `TokenHandle`, broker-driven renewal without user interaction, and destruction of the retained assertion on logout or revoke |
| 9 | M8 release hardening — **next** | Clean-checkout reproduction of every inherited v0.1, v0.2, and v0.3 MUST criterion |

## M7 deferrals

Recorded here rather than in the runbook because they are backlog items, not operating
instructions. Each is out of scope for M7 by decision, not by omission:

- **Workspace-shared and `SERVICE_ACCOUNT` EMA → M8/v0.4.** M7 mints `USER`-scoped
  connections only. INV3-009 requires admin policy for a shared connection, and that
  policy surface does not exist yet; shipping shared EMA without it would let one
  principal's enterprise grant serve another's invocation.
- **Session enumeration UI → M8.** The identity page reports whether *the caller* holds
  a live assertion. Listing and ending another principal's sessions is an administrative
  surface with its own authorization questions (ADR3-M6-003 deferred the same thing).
- **Real identity-provider interoperability (Entra, Okta) → M8, as an expiring `manual`
  record if at all.** The seven criteria are proven against in-process fakes; no
  commercial IdP has been exercised. If it is recorded, it must be a `manual` entry with
  a `last_verified` date that goes stale in 180 days, never a test.
- **Token-exchange egress allowlist → M8.** `enterprise_auth_token_exchange_url` is
  reached without a host allowlist of the kind M3 applies to MCP endpoints and M5 to
  research upstreams. The URL is operator-supplied and the flag is off by default, so it
  is a hardening gap rather than a live exposure — but it is the one place in the M7 path
  where configuration alone decides where a credential travels. Related: the control
  plane presents no client credentials on either hop, so the exchange endpoint must
  authenticate it by network path or mutual TLS today.

## M8 inherited UI findings (F1–F4)

Minor findings deferred from the v0.2.0 post-release browser/accessibility
validation ([evidence](../v0.2/browser-a11y-evidence.md), issue #52 / PR #55).
Resolve within M8 release hardening:

- **F1** — `/runs/:id` horizontal overflow at 390 px: the "Materialize run
  experience" action and status chips push past the viewport edge.
- **F2** — WCAG AA color contrast on status pills (`pill-succeeded`) and dim
  monospace metadata text across `/history`, `/capabilities`, the three
  benchmark routes, and `/runs/:id`; adjust ink tokens.
- **F3** — Missing `h1` on every route except the dashboard; promote route
  titles to `h1`-level headings.
- **F4** — The normalized-trace `event-list` scroll region on `/runs/:id` is
  not keyboard-focusable; add `tabindex="0"` with an appropriate role/label.

**All four closed in M8** — see
[browser-a11y-evidence.md](browser-a11y-evidence.md): axe-core 4.10.2 reports
zero violations across all 17 routes, no route scrolls horizontally at 390 px,
and 0 of 1,421 measured text nodes fall below WCAG AA. Two corrections to the
findings as written are recorded there: the status-pill palette itself always
passed AA (the pills lost their colour to a more specific `.panel-header > span`
rule), and the five `/admin/*` pages already had an `h1`. One new finding was
found and fixed in the same pass: the M6 admin pages scrolled the document
sideways at 390 px because their registry tables had no scroll container.

## Carried technical debt

### Inherited from v0.2 release hardening

- Split or lazy-load the operator bundle to remove the current Vite chunk-size
  advisory without changing routing or backend authority.
- Automate the manual browser/accessibility release smoke while preserving the
  current deterministic component tests.
- Turn the v0.2 evidence manifest, dependency audits, secret scan, and fixture
  hash validation into a repeatable release command or protected workflow.

### P7 experience benchmark provenance — v0.4

- **Serve the assessed stale-rejection figure from the API.** M8 gave
  `ExperienceBenchmarkRunner.run()` an optional `stale_assessor` and a
  `stale_rejection_source` field on the gate (`DECLARED` / `ASSESSED`). The
  acceptance evidence exercises the `ASSESSED` path against the real
  `MatchDisposition` values, but the two API routes still take the `DECLARED`
  path, which counts `retrieval_outcome` in `sources.v1.json` rather than
  measuring this system. Closing that means an async runner holding a store, a
  verifier registry and a repository, extending `sources.v1.json` with the
  fields an assessment needs, and changing both routes — contracts, fixtures and
  routes during a release freeze, so it is deliberately out of M8's scope.
- Note the limit of what any test can prove here while the declared outcome
  stays a hard pin: the assessed count and the declared count are necessarily
  equal, so only "every stale source is assessed, and disagreement is fatal" is
  falsifiable. Making the assessment authoritative — reporting disagreement on
  the gate instead of raising — is the v0.4 design question.

### Runtime adapter pattern

Recorded while adding the third runtime adapter (#59). Each item is a pattern
that held for two adapters and stopped holding for three.

- Extract the adapter scaffolding now duplicated across `runtimes/claude.py`,
  `runtimes/codex.py`, and `runtimes/opencode.py` — the call-terminal set, the
  `submit` preamble, `events`, `_call_id`, `_canonical_session`, `_finish_call`,
  `terminate`, and `artifacts` — without weakening the one-terminal-event
  guarantee each adapter currently proves on its own.
- Add a shared runtime conformance suite that every adapter must pass, covering
  side-effect-free event normalization, one terminal event followed by a closed
  stream, session reuse across sequential calls, and a credential-free spawn
  environment. Adapters are pinned today by hand-copied per-adapter tests that
  can drift apart.
- Give `command_result` an explicit child environment. It passes none, so every
  runtime health probe inherits the full control-plane environment including the
  database URL and the OIDC client secret.

### Multi-provider generalization

Deferred deliberately by #59 so the adapter could land without reopening frozen
benchmark evidence. Each is a two-provider assumption that a third provider
cannot satisfy.

- Generalize the cross-provider and generator/reviewer search modes in
  `orchestration/search.py`, which select exactly two providers after the
  availability check.
- Redesign the live calibration balance in `live_sample.py`, which fixes ten
  assignments across two providers and cannot represent a third.
- Seed the provider buckets in `search_benchmark.py` from the provider
  enumeration; they are pre-seeded with two providers, so a replay trace naming
  any other provider raises `KeyError`.
- Extend `PlannerRuntime` and `RuntimeRequirement` so static graph nodes can pin
  the opencode runtime, which is currently selectable only for whole runs.
- Make the MCP capability gateway multi-tenant by deriving the run identity from
  the workspace lease rather than a per-process environment variable, so a
  runtime that shares one provider server across concurrent runs can carry
  governed capabilities without misattributing side effects.

### v0.3 M2 and M3 — closed

M2 shipped in three steps: the token broker core (#62), the broker in the
execution path (#75), and the GitHub connector, section 17 connection routes,
principal-bound runs, role enforcement, and the automated secret scan (#77),
which closed AC3-CON-01, AC3-CON-02, AC3-ID-04, AC3-ID-05, AC3-SEC-01, and
AC3-SEC-04. "Encrypted outside normal relational state" (AC3-CON-02) is
proven as a property of the database dump rather than argued; the deviation
from an OS keyring for the master key stays recorded in the token broker
runbook.

M3 shipped as one change (#79) and proved AC3-MCP-02 through AC3-MCP-08. Two
facts worth carrying forward:

- The MCP SDK makes `opentelemetry-api` a transitive dependency. Accretion does
  not instrument it and no tracer provider is configured, and the secret-scan
  guard now asserts exactly that; if OpenTelemetry is ever wired up, span export
  becomes a scanned surface.
- Remote endpoint policy is configured by `ACCRETION_MCP_ALLOWED_HOSTS`,
  `ACCRETION_MCP_ALLOWED_PORTS`, and `ACCRETION_MCP_ALLOW_LOCAL_HTTP`; the
  defaults admit any public HTTPS host on 443. An M6 administration surface
  should expose the resulting server states without exposing credentials.

### Manifest and release hygiene

- Make `runtime_compatibility.toml` authoritative or remove it. Nothing reads
  it, while the version ranges that actually gate `DEGRADED` live inside each
  adapter's health check, so the two can drift without any signal.
- ~~Record the v0.3 M0–M3 layers and the opencode runtime adapter in the
  changelog.~~ Done at the 2026-08-26 park.
- ~~Add the identity settings introduced by M1 to `.env.example`.~~ Done at the
  2026-08-26 park, together with the M3 endpoint-policy settings.

### v0.3 M4 — closed

M4 shipped the plugin manager and proved AC3-PLG-01 through AC3-PLG-06. What a
later reader needs to know without re-deriving it:

- **The SDD names the plugin states twice and the lists differ.** §20.3 was adopted
  over §9.2 and recorded as ADR3-M4-001 in the
  [plugin manager runbook](../../runbooks/v03-plugins.md). §9.2's
  `CONFIGURATION_REQUIRED` is the single dissenting token and `SETUP_REQUIRED` names
  the same condition; only §20.3 carries `FAILED`, which AC3-PLG-02's policy-denial
  branch needs as a terminal state. `docs/sdd/` is hash-manifested, so §9.2 keeps the
  dissenting token until someone decides whether to correct it upstream or annotate it
  as superseded.
- **Installations are workspace-scoped; the version registry is global.**
  `plugin_installations` is unique on `(workspace_id, plugin_id)`, mirroring M3's
  remote MCP servers, while `plugin_versions` holds one immutable row per
  `(plugin_id, version)` so a historical trace dereferences the version that actually
  ran regardless of who still has it installed. The SDD states neither.
- **Deferrals, stated rather than silent.** First-class `consent_records` and
  `scope_grants` tables → M6 (consent is embedded in `PluginInstallation`, and the
  lift is additive). YAML manifests → indefinite; JSON keeps digests consistent with
  the governance checksum convention and adds no parser dependency, with the package
  source as the single seam. Live plugin health probing → M5, and **re-deferred by M5 to M6** (see below). `ui.pages` and
  `node_badges` rendering → M6, validated and persisted here but drawn nowhere. Key
  rotation/revocation and archive ingestion (zip-slip, quarantine) → v0.4. A real
  `capability_policy_bypass` counter — §24.8 is currently a number with no counter
  behind it — → M8.
- **Open product question.** `allowlisted` means "trusted" while installation state
  means "enabled", and `ExperienceService` filters replay on
  `list_plugins(allowlisted_only=True)`. M4 deliberately pinned that behaviour with
  regression tests rather than changing it: AC3-PLG-03 is about capability execution,
  not replay. Whether a disabled-but-allowlisted plugin should block experience replay
  is a product call for M6/M8.

### v0.3 M5 — closed

M5 shipped the research intelligence plugin and proved AC3-RES-01 through AC3-RES-04,
closing §27's exit criterion *"v0.2 dynamic workflow can use research plugin without
provider-specific logic"*. The operating detail is in the
[research intelligence runbook](../../runbooks/v03-research.md); what a later reader
needs without re-deriving it:

- **The SDD names the research capability surface twice and the lists differ.** §10 was
  adopted over §9.1 and recorded as ADR3-M5-001. The decisive member is the one on
  which the two genuinely disagree: §9.1's `research.citation.resolve` *resolves* an
  identifier, which is a lookup, while AC3-RES-01 asks for citation *verification*,
  which is a comparison between what a candidate claimed and what a resolver returned.
  §9.1's surface therefore cannot satisfy AC3-RES-01 however it is implemented.
  `docs/sdd/` is hash-manifested, so §9.1 keeps its token.
- **`github.search` in, `python.execute` out** (ADR3-M5-002). No M5 criterion needs code
  execution, and a capability that can run code can manufacture any evidence it likes,
  which contradicts the trust model's premise that a source cannot set its own trust.
  The decision is enforced by a test asserting `python.execute` is absent, not by this
  paragraph.
- **A new `research_evidence` table; the orphaned `evidence` table was left alone.**
  The v0.4 cross-release registry pins `EvidenceRef` identity, so squatting the existing
  table would force a Major migration later.
- **Unverified evidence is unrankable, not low-scored.** `trust_score` is `None` for
  `UNVERIFIED` and `QUARANTINED` records and a model validator refuses to construct one
  with a score, mirroring `CandidateScore.total_score`. A low-but-nonzero score would be
  a knob an upstream could turn by writing better text; structural absence is not.
- **Deferrals, stated rather than silent.**
  - *No research benchmark.* M5's connectors are faked, so a benchmark would report the
    quality of `tests/fake_research_api.py` and read as the quality of the pipeline —
    exactly the failure the acceptance baseline exists to prevent. Revisit when a real
    upstream is reachable.
  - *Live plugin health probing → **re-deferred to M6***. M4 deferred it to M5; M5
    defers it again for the same reason and one more. The reason: every endpoint M5
    registers is an in-process fake over `ASGITransport`, so a probe would report the
    health of the test harness. The addition: probing is a scheduled activity and M5
    added no scheduler, and wiring one for a single consumer puts a background loop in
    the codebase before there is a second thing to run on it. M6 owns plugin
    administration, where both the health display and the scheduler belong.
  - *Real upstream egress.* `McpEndpointPolicy` governs the connection *to* an MCP
    server and does not inspect what that server then fetches. M5 tests entirely
    offline. Real upstream URLs stay gated behind `ACCRETION_ENABLE_RESEARCH_PLUGIN`
    **and** a populated `ACCRETION_RESEARCH_ALLOWED_HOSTS`, which is empty by default,
    and the adapter's own egress needs a policy in the shape of `McpEndpointPolicy`
    before production use.
- **M4's handler-seam debt stands and is now visible.** Plugin-declared capabilities
  still have no handler seam into `CapabilityExecutor`, so the bundled
  `accretion-sample-plugin` remains installable and non-executable in production. M5 is
  unaffected — research capabilities are MCP bindings and `RemoteMcpManager` already
  executes those — but the debt did not go away and should be scheduled.
- **Open seam for M6.** The planner hangs `capability_refs` on `AGENT` and `LOOP` nodes
  (`orchestration/fragments.py`), while `RunManager` spends them on `TOOL` nodes. A
  hand-authored template with a capability-bearing `TOOL` node executes them today; a
  fragment-planned graph does not. Deciding which node kind owns capability spend is M6
  work, and until then the exit criterion is proven on the hand-authored path.
- **`GET /api/v1/runs/{run_id}/research-evidence` takes `workspace_id` as a required
  parameter** because `Run` reaches a task, a project and a principal, and none of the
  three reaches a workspace. Narrowing the gate to the run's own workspace needs that
  link built first — M6.

### v0.3 M6 — closed

M6 shipped the administration surface and the run-page inspectors, proving `AC3-UI-01`
through `AC3-UI-05` and taking over the six inherited `V02-UI-001..006` criteria. The
operating detail is in the
[frontend and administration runbook](../../runbooks/v03-frontend-admin.md); what a later
reader needs without re-deriving it:

- **The proof of a page is split, and the split is machine-checked** (ADR3-M6-001). Each
  `AC3-UI-*` criterion is claimed by a marked pytest test proving the API half in-process
  against the real managers, and carries a `frontend_evidence` pointer — a new
  `criteria.toml` key — naming the vitest test that proves the rendering half. The
  pointer is path-, line- and title-checked by `acceptance.py`, so deleting a page test
  or retitling one by a byte fails `--stage M6`. `criteria.toml` was therefore flipped in
  the PRs that landed the markers, not batched into the close-out: the harness fails
  closed when a marked test claims a `not_yet_due` criterion.
- **The plan's predicted counts do not describe the delivered shape.** `m6-plan.md`
  expected `--stage M6` `proven: 2` and `make acceptance` `FRONTEND 9`, both derived from
  putting most criteria on `verification = "frontend"`. Because that setting *replaces* a
  pytest claim rather than adding to it, adopting it would have meant discarding the
  in-process proof. Delivered: `--stage M6` reports `in scope: 5   proven: 5`,
  `--stage v0.2-ui` reports `6 / 6`, and `FRONTEND` stays at 3 — the pre-existing
  `V01-P4-004`, `V02-P6-008` and `V02-P7-007` pointers — because `FRONTEND` counts
  criteria whose *only* proof is vitest.
- **One new route, no new contract, no migration** (ADR3-M6-002).
  `GET /api/v1/mcp/servers/{id}/discovery` returns the most recent `McpDiscoverySnapshot`
  so §16.3's tools/prompts/cache-TTL requirement has a path to the browser;
  `/capabilities` returns the mapped canonical set, not the raw snapshot. It is additive
  over M3 contracts, never contacts the server, and 404s identically for a non-member, a
  non-existent server, and a server that has never discovered. §17's route list does not
  name it and `docs/sdd/` is hash-manifested, so the divergence is recorded rather than
  patched.
- **Identity is read-only and stays that way until M7** (ADR3-M6-003). §16.4's active
  sessions and enterprise authorization panel are deferred: no route returns an
  `AuthSession` row, and an endpoint enumerating sessions is one that tells an attacker
  which sessions to target, so it belongs with the milestone that designs its revocation.
  Role assignment is an authorization write and M7 owns the policy that should govern it.
- **Node badges come from the audit, not from the manifest** (ADR3-M6-004).
  `PluginUiContribution.node_badges` is typed `list[dict[str, Any]]` with no declared key
  for the label, the node predicate, or the projected value, so rendering it would mean
  inventing the entry shape in the UI. The badge instead reads the plugin id out of the
  synthetic connector the gateway already records in
  `CapabilityExecutionResult.connector_id`. Declaring the entry contract is M8's.
- **"Non-authoritative" is structural, not a review note.** `apps/ui/src/runBadges.ts`
  imports only `./types`, holds no state and names no transport, asserted in pytest; the
  vitest half asserts no interactive role and zero fetches on click across both the
  React Flow node and its accessible mirror. Making a badge authoritative would require
  adding an import the test forbids.
- **Deferrals, stated rather than silent.**
  - *`GET /audit/connections`, `GET /audit/capabilities`, `POST /capabilities/{id}/dry-run`
    → M8.* Each needs a contract and a store method that do not exist plus a migration,
    which is not a frontend milestone's work.
  - *F1 (390 px overflow) and F2 (WCAG AA contrast) stay open.* jsdom has no layout engine
    and `apps/ui/src/test/setup.ts` fakes `offsetWidth` and `getBoundingClientRect` with
    constants, so an assertion about either would assert the stub. They remain browser/axe
    evidence for M8. F3 and F4 are unchanged for the inherited routes; the five new pages
    each carry an `h1` and focusable scroll regions, so they add nothing to either finding.
  - *Live plugin health probing → **re-deferred to M8***. M4 deferred it to M5, M5 to M6.
    The reason is unchanged and is not about the UI: probing is a scheduled activity and
    there is still no scheduler. `/admin/plugins` renders the health the store holds; it
    does not collect it.
  - *First-class `consent_records` and `scope_grants` tables → M8.* M6 adds no persisted
    field and no table; `PluginDetail` already composes what the page renders, so the lift
    stays additive.
- **M4's open product question is still open.** Whether a disabled-but-allowlisted
  plugin should block experience replay (`ExperienceService` filters on
  `list_plugins(allowlisted_only=True)`) is a product call, not a rendering one. M6 made
  the two states *visible* on `/admin/plugins` — trust and installation state are separate
  columns and read differently — which is as far as a frontend milestone can take it. The
  decision is M8's.
- **Two M5 seams are still open and were not M6 work after all.** The planner hangs
  `capability_refs` on `AGENT`/`LOOP` nodes while `RunManager` spends them on `TOOL`
  nodes; and `GET /api/v1/runs/{run_id}/research-evidence` still takes `workspace_id`
  explicitly because `Run` reaches no workspace. Both are execution-path decisions with no
  frontend component, and both move to M8.

## Locked boundary

Do not begin learned node routing, contextual bandits, policy learning,
self-modifying architecture, or automatic promotion of experience into policy.
Those capabilities remain beyond v0.3 and require stable identity, capability,
connection, telemetry, and inherited release evidence first.
