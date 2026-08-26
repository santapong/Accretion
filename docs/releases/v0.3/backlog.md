# v0.3 prioritized backlog

Status: M0–M5 delivered; work is parked before M6. Every M0–M5 acceptance
criterion is proven by a claiming test (`make acceptance`), and the per-stage gates
`--stage M1` through `--stage M5` all report `in scope: n   proven: n   unmet MUST: 0`.
The remaining uncovered criteria are inherited v0.1/v0.2 items listed in the
[acceptance baseline](acceptance-baseline.md).

The normative contract remains [Accretion SDD v0.3](../../sdd/Accretion_SDD_v0.3.md).
This ledger orders its existing milestones without changing acceptance criteria
or unlocking the hash-manifested v0.4+ designs.

## Delivery order

| Priority | Milestone | Outcome required before continuing |
|---:|---|---|
| 1 | M0 capability/connection refactor — **delivered** (#57) | Existing v0.1/v0.2 capabilities resolve unchanged through additive connection-aware contracts |
| 2 | M1 identity and SSO — **delivered** (#58) | Issuer/subject principals, workspace membership, OIDC Authorization Code + PKCE, and multi-user tests |
| 3 | M2 token broker and OAuth connections — **delivered** (#62, #75, #77) | Encrypted refresh/revoke lifecycle with no token values in model, API, UI, events, or logs |
| 4 | M3 remote MCP manager — **delivered** (#79) | Authenticated remote discovery, schema validation, health/circuit breaking, SSRF controls, and canonical capability bindings |
| 5 | M4 plugin manager — **delivered** | Versioned manifest validation, permission requests without grants, enable/disable/upgrade, and historical provenance |
| 6 | M5 research intelligence plugin — **delivered** | Provider-neutral research capabilities, normalized evidence, provenance, and citation verification |
| 7 | M6 frontend and administration — **next** | Plugins, Connections, MCP Servers, capability resolution, identity, and roles without exposing secrets |
| 8 | M7 enterprise authorization | Optional EMA integration behind a feature flag after the standard OAuth path is stable |
| 9 | M8 release hardening | Clean-checkout reproduction of every inherited v0.1, v0.2, and v0.3 MUST criterion |

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

## Carried technical debt

### Inherited from v0.2 release hardening

- Split or lazy-load the operator bundle to remove the current Vite chunk-size
  advisory without changing routing or backend authority.
- Automate the manual browser/accessibility release smoke while preserving the
  current deterministic component tests.
- Turn the v0.2 evidence manifest, dependency audits, secret scan, and fixture
  hash validation into a repeatable release command or protected workflow.

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

## Locked boundary

Do not begin learned node routing, contextual bandits, policy learning,
self-modifying architecture, or automatic promotion of experience into policy.
Those capabilities remain beyond v0.3 and require stable identity, capability,
connection, telemetry, and inherited release evidence first.
