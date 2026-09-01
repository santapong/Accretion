# Frontend and administration runbook

> **Deferral note (M8).** Items below recorded as "deferred to M8" were not taken:
> M8 was scoped to release hardening — closing the inherited acceptance criteria and
> making the release gate executable — and each deferral adds contracts, routes or
> migrations. They are v0.4 items; see
> [the v0.3 backlog](../releases/v0.3/backlog.md). The F1–F4 accessibility findings
> *were* closed in M8; see
> [browser and accessibility evidence](../releases/v0.3/browser-a11y-evidence.md).

How to operate the administration surface introduced by v0.3 M6. The normative
contract is [Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §16, §17 and §27.

§27's exit criterion for M6 is *"operator can diagnose setup and authorization
without shell access"*. That word — **diagnose** — is what the milestone turns on.
Every page below answers a question an operator would otherwise answer with `psql`
or a Python REPL, and none of them answers it with a value the operator is not
allowed to see. The governing rule is inherited from M2 and unchanged: **the API
projects, it never reveals**. No route these pages call returns a token, a refresh
token, a client secret, or a token handle, and the pages cannot render what the API
does not carry.

## What ships

Five routes under `/admin`, registered in `apps/ui/src/App.tsx` and reachable from
the operator navigation.

| Route | Page | The question it answers |
|---|---|---|
| `/admin/connections` | `ConnectionsPage` | Which connections exist, what state each is in, and what happens if I connect, re-consent, or revoke? |
| `/admin/plugins` | `PluginsPage` | Which plugin version is *installed* here, in what state, with which grants and which connector dependencies unmet? |
| `/admin/mcp` | `McpServersPage` | Which remote MCP servers are reachable, which breaker is open, and what did discovery actually find? |
| `/admin/capabilities/inspect` | `CapabilityInspectorPage` | For this capability and this principal, which binding, backend and connection would a run really use — and if none, why? |
| `/admin/identity` | `IdentityPage` | Who am I to this system, under which issuer and auth mode, and with which workspace roles? |

Each page has an `h1`, so none of them adds to the inherited F3 finding. The run page
gains capability badges on the React Flow nodes, a graph diff that names identities,
and a router inspector; those close `AC3-UI-05` and the inherited `V02-UI-001..006`.

## Two rules that hold across every page

**A page renders a projection, never a secret.** `ConnectionSummary` is the only
connection shape the API emits, and `tests/test_v03_m6_admin_surface.py` pushes a
sentinel token value through the real broker and asserts it appears in none of the six
responses the Connections page makes. The page test additionally pins the key set:
it reads no key `ConnectionSummary` does not declare, so a future field carrying a
credential cannot be rendered by accident.

**A badge is a projection, not state.** `apps/ui/src/runBadges.ts` imports only
`./types`. It holds no state, names no transport and cannot reach `api.ts`, which is
asserted structurally in pytest rather than argued. A badge therefore cannot become
authoritative by drifting: to make it authoritative someone would have to add an
import that the test forbids.

## Diagnosing a capability that will not run

Start at `/admin/capabilities/inspect`. `POST /api/v1/capabilities/resolve` returns a
`ResolvedCapability` whose `outcome` is the runtime's own verdict, and the page renders
that verdict in prose rather than as an enum (`apps/ui/src/pages/capabilityResolution.ts`).

| Outcome | What to do |
|---|---|
| `OK` | The capability is bound and connected. If a run still fails, the failure is downstream of resolution — check the MCP server's health on `/admin/mcp`. |
| `NO_CONNECTOR_REQUIRED` | Resolved; the capability needs no connector at all. Nothing to configure. |
| `NO_CONNECTION` | This *principal* holds no usable connection. Connect on `/admin/connections`. Another principal having one is irrelevant and is deliberately invisible here. |
| `REQUIRE_REAUTH` | Consent lapsed. Use the row's reauthorize action; it posts to the reauthorize route for that row only. |
| `DISABLED` | The binding, the plugin installation, or the MCP server lifecycle state makes it non-executable. Check `/admin/plugins` and `/admin/mcp` before touching the binding. |

An unresolved capability always states its reason. The page never renders an empty
panel next to a capability the runtime is refusing to run — that is the failure mode
the inspector exists to remove, and it is asserted directly.

Resolution is per-principal and refuses to be otherwise: resolving as another principal
returns the API's own refusal, which the page renders verbatim rather than blanking.

## ADR3-M6-001 — the proof of a page is split between pytest and vitest

**Status:** accepted, v0.3 M6.

**Context.** `AC3-UI-01` through `AC3-UI-05` name *pages*, but each has two halves that
fail independently. One half is whether the API the page renders really carries the
diagnosis — the installed version and state of a really-installed package, a really-open
circuit breaker, a really-discovered tool set, a resolution really computed against the
store. The other half is whether the page renders it: whether two rows that differ in
the data read differently on screen, whether a denial states its reason, whether a badge
offers no interactive role. pytest cannot see the second half; vitest cannot prove the
first, because in vitest the API is a fixture the test itself wrote.

**Decision.** Claim each criterion with a marked pytest test that proves the API half
in-process against the real managers, and carry the rendering half as a machine-checked
`frontend_evidence` pointer in `docs/acceptance/criteria.toml`. The vitest fixtures for
`AC3-UI-01`/`-03` are *generated by* that pytest run and byte-compared, so the two halves
cannot drift apart silently.

`frontend_evidence` is a new policy key (`src/accretion/acceptance.py`,
`frontend_evidence_errors`). It is checked exactly as `verification = "frontend"`
evidence is: the path must exist under `apps/ui/`, must name a vitest spec, must carry a
`:line` anchor, and the anchored line must open a `test`/`it` whose title is *exactly*
the prose the pointer carries. Deleting `PluginsPage.test.tsx`, renaming it, or drifting
a test title by one byte fails `--stage M6`.

**Why not the plan's split.** `m6-plan.md` decision 1 put `AC3-UI-01`, `-03`, `-05` and
`V02-UI-*` on `verification = "frontend"` and predicted `--stage M6` reporting
`proven: 2`. Two things made that unworkable. First, the harness fails closed when a
marked test claims a criterion the policy still calls `not_yet_due`, so the pytest proof
and the policy flip have to land in the same commit — which is also why `criteria.toml`
was flipped in PR3–PR6 rather than held to PR7. Second, `verification = "frontend"`
*replaces* the pytest claim rather than adding to it; adopting it would have meant either
merging marked tests that prove a criterion while claiming nothing, or deleting the
in-process proof that the API half is real. `frontend_evidence` keeps both.

**Consequences.** `--stage M6` reports `in scope: 5   proven: 5   unmet MUST: 0`
(a local diagnostic since M8; CI gates the unscoped harness), and
`make acceptance` reports `FRONTEND: 3` — the three pre-existing pointers
`V01-P4-004`, `V02-P6-008`, `V02-P7-007` — because `FRONTEND` counts criteria whose
*only* proof is vitest and none of M6's are. The plan's `proven: 2` and `FRONTEND 9` do
not describe this shape and are superseded here.

**`AC3-UI-02` and `AC3-UI-04` are the same decision, not an exception.** They are
sometimes described as "pytest-marked, so no frontend entry is needed". They carry one
anyway, for the same reason as the other three: their pytest claims cover the API surface
(no request returns a credential; the resolution is real), while the page halves — that
`connect` hands the browser to the authorization URL *without rendering it*, that a
denial states its reason, that the identity page offers no way to change a role — are
proved only in vitest and would otherwise be held by nothing.

## ADR3-M6-002 — `GET /api/v1/mcp/servers/{id}/discovery` is added, though §17 does not list it

**Status:** accepted, v0.3 M6.

**Context.** §16.3 requires the MCP page to show discovered tools, resources and prompts
and the cache TTL. §17's route list (`docs/sdd/Accretion_SDD_v0.3.md:1084-1090`) stops at
`GET /api/v1/mcp/servers/{id}/capabilities`, which returns the *canonical capabilities*
bound to the server — not the raw discovery snapshot. There is no route through which the
data §16.3 asks for reaches a browser.

**Decision.** Add `GET /api/v1/mcp/servers/{id}/discovery`, returning the most recent
`McpDiscoverySnapshot`.

**Why.** It is additive over contracts and store methods that already exist —
`McpDiscoverySnapshot` and `list_mcp_discovery_snapshots` were built in M3 — so it adds
no contract, no table and no migration, and it is a Minor change under the locked v0.4
registry. The alternative, rendering only what `/capabilities` returns, would leave a §16
requirement unmet while the data sat in the database unreachable. `docs/sdd/` is
hash-manifested, so §17 keeps its list and this ADR records the divergence.

**Why it is safe to expose to any workspace member.** Unlike `refresh-discovery`, this
route never contacts the server: it reads the persisted snapshot, so it cannot be used to
drive outbound traffic. Tenancy is unchanged — the server is fetched through the same
membership-checked helper as every other MCP route, and a non-member gets 404 rather than
403, so another workspace's server stays invisible rather than merely forbidden. A server
with no snapshot yet is also a 404, which is what a non-existent server returns; the two
are deliberately indistinguishable.

**Deferred with it.** `GET /api/v1/audit/connections`, `GET /api/v1/audit/capabilities`
and `POST /api/v1/capabilities/{id}/dry-run` are **not** added. Each needs a contract and
a store method that do not exist, plus a migration — which is not a frontend milestone's
work. They are M8.

## ADR3-M6-003 — the identity page is read-only; sessions and enterprise authorization are M7

**Status:** accepted, v0.3 M6.

**Context.** §16.4 asks the Identity/SSO page to show the current IdP, the principal's
subject and issuer, workspace roles, **active sessions**, and the **enterprise
authorization configuration**. M6 is a frontend milestone.

**Decision.** Render the first three from `GET /me`, `GET /workspaces` and
`GET /auth/providers`. Add no mutation of any kind. Defer session enumeration and the
enterprise authorization panel to M7.

**Why.** Role assignment and session revocation are authorization writes, and M7 is the
milestone that owns enterprise authorization; shipping a role editor in M6 would place the
control before the policy that is supposed to govern it. Session enumeration is blocked on
something simpler: there is no route that returns an `AuthSession` row, and adding one is
backend work with a real security surface — an endpoint listing sessions is an endpoint
that tells an attacker which sessions to target — that belongs with the milestone that
designs the revocation alongside it. "Current session" is therefore rendered from what the
API does carry: the auth mode, the configured provider and issuer, and the (issuer,
subject) this browser is authenticated as.

**Consequences.** The page is proved read-only rather than described as read-only: a
vitest test asserts it issues only reads and offers no control that would change a role.
Identity is keyed on `(issuer, subject)` and both are shown, rather than a display name
that looks unique and is not.

## ADR3-M6-004 — a node badge names the plugin from its synthetic connector

**Status:** accepted, v0.3 M6.

**Context.** §16.6 allows capability/integration badges on execution nodes, and
`AC3-UI-05` requires them to carry plugin, connection and capability metadata "without
becoming authoritative state". `PluginUiContribution.node_badges` exists in the manifest
contract but every field of an entry is unspecified: it is typed `list[dict[str, Any]]`
with no declared key for the label, the node predicate, or the value to project.

**Decision.** Derive the badge from the audit the gateway already persists. The plugin
half is the plugin id read out of the synthetic, credential-free connector every plugin
capability is bound through (`plugin_connector_id`), which the gateway records in
`CapabilityExecutionResult.connector_id`. Defer `node_badges` rendering to M8.

**Why.** Rendering `node_badges` now would mean inventing its entry shape in the UI, and
reaching it needs a per-plugin request the run page does not make. The audit path needs
neither: the badge is a pure projection of rows that were written when the call actually
ran, so a badge cannot claim provenance that did not happen. M8 owns declaring the entry
contract, and a projection that carries it alongside the audit rather than one plugin
fetch per badge.

**Non-authoritative, enforced structurally.** Badges render inside the React Flow node
and are mirrored by the same component in the accessible `projection-node-summary` list,
so canvas and mirror cannot disagree. Across *both* placements the vitest half asserts no
interactive role and zero fetches on click, and a newer audit replaces the badge
identities instead of showing a cached copy.

## Verifying the milestone

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py --stage M6
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py --stage v0.2-ui
cd apps/ui && npm run check && npm run test && npm run build
```

Both stages run in CI after `--stage M5`. `--stage M6` reports
`in scope: 5   proven: 5   unmet MUST: 0`; `--stage v0.2-ui` reports
`in scope: 6   proven: 6   unmet MUST: 0`.

Any new route means running `npm run api:generate` at the repository root and committing
`apps/ui/src/api/schema.d.ts` in the same change; CI fails on `git diff --exit-code`
otherwise. The vitest fixtures are typed as `components["schemas"][...]`, so schema drift
breaks `npm run check` rather than producing a green suite over a stale shape.

## What M6 deliberately did not build

### No layout or contrast assertions

F1 (horizontal overflow at 390 px) and F2 (WCAG AA contrast) are untestable in this
setup. jsdom has no layout engine, and `apps/ui/src/test/setup.ts` fakes `offsetWidth` and
`getBoundingClientRect` with constants, so an assertion about either would be asserting
the stub rather than the page. The new badge, diff, router and admin styles inherit the
existing tokens. Both findings stay open against the browser/axe evidence and M8.

### No live plugin health probing — re-deferred again, to M8

M4 deferred it to M5, M5 re-deferred it to M6, and M6 re-defers it to M8. The reason is
unchanged and is not about the UI: probing is a *scheduled* activity and there is still
no scheduler, so wiring one for a single consumer would put a background loop in the
codebase before there is a second thing to run on it. `/admin/plugins` renders the health
the store already holds; it does not collect it.

### No consent or scope-grant tables

M4 deferred first-class `consent_records` and `scope_grants` tables to M6. M6 adds no
persisted fields to `MetaPlugin` or `PluginInstallation` and no tables at all:
`PluginDetail` already composes the version, state, grants, connector status and
lifecycle history the page renders. The lift stays additive and is now M8's.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A server's page shows tools but `/capabilities` is empty | Discovery found tools that no canonical capability mapping claims. The snapshot is raw; capabilities are the mapped subset. |
| `GET .../discovery` returns 404 for a server that plainly exists | Either the caller is not a member of the owning workspace — 404 is deliberate, so another tenant's server is invisible rather than forbidden — or discovery has never run. Both look identical on purpose. |
| The inspector says `NO_CONNECTION` for a capability a colleague can run | Correct, and not a bug. Resolution is per-principal; another principal's connection is not yours and is not shown. |
| A capability is `DISABLED` but its binding row says `enabled = true` | The owning plugin installation or MCP server lifecycle state makes it non-executable. The resolver gates on both. |
| `--stage M6` fails naming a `frontend evidence` line | A vitest test moved or was retitled. Update the pointer in `docs/acceptance/criteria.toml`; do not delete it. |
| `npm run check` fails on a fixture after a backend change | The generated schema moved under a fixture typed against it. Regenerate `schema.d.ts` and fix the fixture — the fixture is meant to break here. |
