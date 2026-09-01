# Enterprise-managed authorization runbook

How v0.3 M7 delivers optional Enterprise-Managed Authorization (EMA). The normative
contract is [Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §8, §24.9 and §27 M7,
together with OQ3-08 and INV3-009.

EMA is **off by default**. With the flag off, an `EMA` connector behaves exactly as an
unauthorized OAuth connector, and nothing in this runbook is reachable. EMA also does
not replace Accretion policy: a centrally issued token still passes through capability
policy, connection isolation and the audit trail unchanged (INV3-002, AC3-SEC-01..05).

## Acceptance criteria

Seven MUST criteria, `AC3-EMA-01..07`, recorded in SDD §24.9. All seven are now claimed
by a marked, passing test: `scripts/check_acceptance.py --stage M7` reports
`in scope: 7   proven: 7   unmet MUST: 0`. Nothing in `docs/acceptance/criteria.toml`
is `not_yet_due` any more.

> **Superseded in M8.** `--stage M7` remains a useful local diagnostic, but CI no
> longer runs it: the eight stage gates were replaced by a single unscoped
> `check_acceptance.py`, which covers these seven criteria along with every other.
> See [the release-hardening runbook](v03-release-hardening.md).

| Criterion | Claimed by |
|---|---|
| `AC3-EMA-01` | `tests/test_v03_m7_identity_retention.py` (+ a checked vitest pointer) |
| `AC3-EMA-02` | `tests/test_v03_m7_enterprise_mcp.py` (+ a checked vitest pointer) |
| `AC3-EMA-03` | `tests/test_v03_m7_enterprise_auth.py` |
| `AC3-EMA-04` | `tests/test_v03_m7_identity_retention.py` (logout half) and `tests/test_v03_m7_enterprise_mcp.py` (revocation half) |
| `AC3-EMA-05` | `tests/test_v03_m7_enterprise_secret_scan.py` |
| `AC3-EMA-06` | `tests/test_v03_m7_enterprise_mcp.py` |
| `AC3-EMA-07` | `tests/test_v03_m7_enterprise_mcp.py` |

## Decisions

### ADR3-M7-001 — the EMA criteria are recorded in §24.9, not §24.8

**Context.** M7 introduces the first acceptance criteria for enterprise-managed
authorization, so the v0.3 SDD needs a new criteria table. The obvious insertion point,
§24.8, is already occupied by the **Release gate** block, and that section number is
cited by name from `docs/releases/v0.3/backlog.md`.

**Decision.** Append a new **§24.9 Enterprise-managed authorization** immediately after
the release-gate block. No existing heading is renumbered or moved, and the release
gate keeps the number the backlog cites.

**Consequences.** The seven `AC3-EMA-*` rows are parsed by the acceptance harness from
their new section exactly as every other table is; `_CATEGORY_MILESTONE` gains
`"EMA": "M7"` so `stage_of("AC3-EMA-01")` resolves to `M7` instead of falling through to
`unassigned`. Because an unassigned category would have made `--stage M7` select zero
criteria — and the CLI used to print `PASS` for an empty selection — the same change
makes an empty stage selection an explicit non-zero error. `--stage M7` is therefore
non-vacuous from the moment it enters CI: it reports the seven rows as `NOT_YET_DUE`,
and would go red if the category mapping or the SDD table were removed.

### ADR3-M7-002 — the principal's OIDC `id_token` is retained, sealed

**Context.** The M7 exit criterion is that a principal who has signed in once can invoke
a centrally managed MCP server with no further end-user authorization step, including
when the enterprise access token expires mid-session. Any exchange (RFC 8693
token-exchange for an identity assertion, then RFC 7523 jwt-bearer at the authorization
server) needs a `subject_token` at the moment of exchange. Per-server identity assertion
grants are single-audience, and an MCP server may be registered *after* the principal
signed in, so minting them all at login cannot cover the criterion. `OidcClient`
returns only the `id_token`; there is no IdP refresh token to hold instead.

**Decision.** Retain the principal's `id_token` for the life of the login, and only it.

**Accepted risk.** A retained bearer assertion is a credential at rest that did not
previously exist, and it names the principal.

**Mitigations, each of which is a tested property.**

- Retained **only when `enable_enterprise_auth` is on**. With the flag off nothing is
  stored and no exchange call is made (AC3-EMA-01).
- Sealed through `EnvelopeSecretStore.seal(..., associated_id=<auth_session_id>)`, so
  the ciphertext is bound to the session that produced it and cannot be unsealed
  against another. The token never reaches a contract field, an event, an envelope, a
  frontend payload, or an OpenTelemetry span (AC3-EMA-05).
- `expires_at` is the token's **own `exp`**, never the session TTL. The assertion cannot
  outlive the credential it wraps, and there is no silent renewal past that point:
  expiry fails closed to `REAUTH_REQUIRED` (AC3-EMA-07).
- Deleted in `IdentityService.logout`, before the auth session row is revoked. Logout
  destroys the secret record itself, not merely a status flag, and the store is read
  back to prove its absence (AC3-EMA-04).
- Resolvable only for its own principal; another principal's request mints its own
  assertion, connection and handle or is refused (AC3-EMA-06).

Workspace-shared and `SERVICE_ACCOUNT` EMA are explicitly **out of scope** for M7 and
deferred to M8/v0.4, because INV3-009 requires admin policy for shared connections.

### ADR3-M7-003 — EMA mints a real `Connection` + `TokenHandle`; re-acquisition lives in the broker

**Context.** §8 describes EMA as a distinct authorization path, while §12.3 describes
connections as the single governed home for credentials. Treating EMA as a parallel path
would duplicate revocation, isolation, health and audit.

**Decision.** An enterprise authorization mints a **real `Connection`** (`ACTIVE`, USER
scope, `token_handle_ref` set) and a real `TokenHandle`, exactly as OAuth does. EMA is a
new way to *obtain* material, not a new place to keep it.

**Consequences.** Revocation, connection isolation, health checks and the audit trail
are the M2 implementations, unchanged and already proven — an EMA connection revokes
like any other. `_authorization()` in the MCP manager gains a single additive branch
(mint when no ACTIVE connection exists); everything below it is untouched.

**Re-acquisition.** `EncryptedTokenBroker.get_access_material` refreshes when material
is stale, and `refresh()` marks a handle `EXPIRED` and raises when there is no refresh
token — which is every jwt-bearer grant. Rather than teach the manager a second expiry
rule, the broker gains a per-connector `register_reacquirer(connector_id, fn)` hook,
consulted inside `refresh()` before the no-refresh-token failure. The EMA manager
registers its own hook at wiring time. `get_access_material` therefore remains the
single authority on expiry, and a mid-session token expiry renews with no user
interaction (AC3-EMA-07).

### ADR3-M7-004 — the assertion row is evidence; revocation never deletes it

**Context.** `tests/test_v03_m4_plugin_primitives.py` turns AC3-PLG-05 into a structural
fact: the state store may expose no deletion method outside a closed allowlist.

> `docs/sdd/Accretion_SDD_v0.3.md:1399`
> `| AC3-PLG-05 | MUST | Plugin removal cannot delete evidence/artifacts from prior runs. |`

M7 must nevertheless destroy the retained assertion:

> `docs/sdd/Accretion_SDD_v0.3.md:1449`
> `| AC3-EMA-04 | MUST | Ending the session or revoking the connection prevents subsequent enterprise-authorized invocation and destroys the retained assertion. |`

**Decision.** The allowlist stays exactly `{"delete_secret_record"}`. The store gains no
deletion surface of any kind for M7. What AC3-EMA-04 requires destroyed is the *assertion
material*, and that material lives only in the secret store; the `identity_assertions`
row holds no token and is evidence of who was authorized and when. Revocation is
therefore two existing operations:

1. `upsert_identity_assertion(assertion.model_copy(update={"status": REVOKED}))`
2. `delete_secret_record(assertion.secret_store_key)`

After step 2 the sealed assertion is unrecoverable — proven by reading
`get_secret_record` back as `None` — while the row remains as an audit trail.

**Consequences.** The allowlist remains **closed**, not advisory: adding
`delete_identity_assertion` or `delete_enterprise_auth_grant` fails the M4 structural
test, which scans `StateStore`, `MemoryStore` and `PostgresStore`. `enterprise_auth_grants`
is append-only in the same sense — no update and no delete path exists in any layer, and
a duplicate `grant_id` is refused identically by `MemoryStore` and by the Postgres unique
constraint.

## Configuring it

### Settings

| Setting (`ACCRETION_` env var) | Default | Effect |
|---|---|---|
| `enable_enterprise_auth` | `False` | Master switch. Off ⇒ an `EMA` connector reports `AUTH_REQUIRED`, nothing is retained at login, and no exchange or grant call is made. |
| `enterprise_auth_token_exchange_url` | `""` | The identity provider's RFC 8693 token-exchange endpoint. Empty ⇒ the subsystem is inert even with the flag raised. |
| `enterprise_auth_audiences` | `{}` | `connector_id -> audience`. A connector absent from this map cannot be enterprise-authorized (`REFUSED_AUDIENCE`). |

```bash
ACCRETION_ENABLE_ENTERPRISE_AUTH=true
ACCRETION_ENTERPRISE_AUTH_TOKEN_EXCHANGE_URL=https://login.example.com/oauth2/v2.0/token
ACCRETION_ENTERPRISE_AUTH_AUDIENCES={"github-enterprise":"https://mcp.example.com"}
```

Both gates must be open before anything happens: `build_enterprise_auth_manager` returns
`None` unless the flag is on *and* an exchange URL is set *and* a token broker exists.
Returning `None` rather than a disabled instance keeps a flag-down deployment
byte-identical to the pre-M7 one — no collaborator, no outbound HTTP client, no
retention. Turning the flag on ahead of the enterprise deployment that backs it is
therefore deliberately inert rather than an error.

EMA reuses the existing OIDC settings for the sign-in half: `oidc_issuer` and
`oidc_client_id` are the current authority on a retained assertion, checked locally on
every acquisition (see "IdP trust" below). It reuses the connector definition for the
authorization-server half: `authorization_server` is the issuer whose `/token` endpoint
receives the jwt-bearer grant, `resource_server` (falling back to the configured
audience) is sent as the exchange `resource`, and `default_scopes` are the scopes
requested. A connector must have `auth_type = EMA` — the re-acquisition hook refuses any
other, so an interactively consented handle can never be resealed with enterprise
authority the user did not delegate.

### The two hops

1. **Token exchange (RFC 8693)** at `enterprise_auth_token_exchange_url`:
   `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`,
   `subject_token` = the retained `id_token`,
   `subject_token_type=urn:ietf:params:oauth:token-type:id_token`,
   `requested_token_type=urn:ietf:params:oauth:token-type:id-jag`,
   `audience` = the connector's configured audience,
   `resource` = `resource_server` or that audience. A response whose
   `issued_token_type` is not the ID-JAG type is refused.
2. **JWT bearer (RFC 7523)** at the connector's authorization server:
   `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`, `assertion` = the ID-JAG,
   `scope` = the connector's default scopes. The answer is parsed by
   `OAuthClient.parse_token_response` into the M2 `OAuthTokenResponse`, so sealing and
   the redacting `__repr__` apply to enterprise tokens unchanged.

The ID-JAG's `iss`, `aud` and `exp` are validated **locally, between the two hops**: an
assertion grant the operator's policy rejects never reaches the authorization server, so
a refusal costs the authorization server nothing and cannot be laundered into an access
token. Each refusal is one appended `EnterpriseAuthGrant` row with a distinct outcome
(`REFUSED_ISSUER`, `REFUSED_AUDIENCE`, `REFUSED_EXPIRED`, `REFUSED_MISSING`,
`REFUSED_DISABLED`, `REFUSED_CONFIGURATION`, `REFUSED_UPSTREAM`), readable at
`GET /api/v1/audit/enterprise-auth`. `detail` is prose only — a test asserts no row
carries token material.

### IdP trust requirements

On the **Accretion** side:

- `oidc_issuer` must be the enterprise IdP. It, not the issuer stored on the assertion
  row, is the expectation on every acquisition, so rotating `oidc_issuer` immediately
  invalidates every assertion minted under the old one.
- `oidc_client_id` must be present if you want the audience of the retained `id_token`
  checked; with no client id configured there is no audience to expect.

On the **MCP authorization server** side, EMA only holds if the AS is configured to:

- accept `urn:ietf:params:oauth:grant-type:jwt-bearer` from this deployment;
- trust the IdP named by `oidc_issuer` as an assertion issuer, and verify ID-JAG
  signatures against that IdP's JWKS;
- require `aud` to equal the audience the operator recorded in
  `enterprise_auth_audiences` for that connector, and reject any other;
- honour the ID-JAG `exp` and issue access tokens no longer-lived than the enterprise
  policy allows — Accretion renews them without user interaction, so a short access
  token lifetime is cheap and a long one is the risk;
- issue **no refresh token** (none is expected; renewal is a fresh grant), and return
  the granted scopes it actually issued, which are what the `Connection` records.

The control plane presents **no client credentials** on either hop by default
(`IdentityAssertionClient` and `JwtBearerClient` take an optional `client_id`/
`client_secret` that the wiring leaves empty), so the exchange endpoint and the
authorization server must authenticate this deployment by other means — a private
network path or mutual TLS. Deployments needing client authentication on the exchange
should not enable EMA until that is wired; there is no egress allowlist for the exchange
URL in v0.3 either (deferred to M8).

### Protocol references

- RFC 8693 — OAuth 2.0 Token Exchange (the `subject_token` → ID-JAG hop).
- RFC 7523 — JSON Web Token Profile for OAuth 2.0 Client Authentication and
  Authorization Grants (the ID-JAG → access token hop).
- RFC 6749 §4 / §5 — the token endpoint and token response shape both hops speak.
- MCP Enterprise-Managed Authorization SEP (`SEP-991`, the enterprise-managed
  authorization proposal against the MCP authorization specification), which is where
  the ID-JAG token type `urn:ietf:params:oauth:token-type:id-jag` and the sign-in-once
  model come from. SDD v0.3 §8 is the normative statement for Accretion; the SEP is the
  interoperability target.

## Operating

### Routes

| Route | Purpose |
|---|---|
| `GET /api/v1/enterprise-auth/profile` | Whether the deployment has EMA on, the configured audiences, and whether the caller holds a live assertion. Never the token. |
| `POST /api/v1/mcp/servers/{mcp_server_id}/enterprise-authorize` | Mint the caller's own enterprise-authorized connection to one server. `409` when the flag is off. |
| `GET /api/v1/audit/enterprise-auth` | The append-only grant trail, prose `detail` only. |

The routes live under `/api/v1/enterprise-auth/`, not `/api/v1/auth/`, because the latter
prefix is exempt from the session middleware and an EMA route must know its caller.

### Revoking

- **Logout** destroys the sealed assertion (`delete_secret_record`) and marks the row
  `REVOKED` before the auth session is revoked.
- **Revoking the connection** uses the M2 path unchanged; the next acquisition is refused
  before anything is exchanged.

In both cases the `identity_assertions` row survives as evidence — see ADR3-M7-004.

## Verifying the milestone

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py --stage M7
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
make check && make test
uv run --no-sync python scripts/check_docs.py
cd apps/ui && npm run check && npm run test && npm run build
git diff --exit-code apps/ui/src/api/schema.d.ts
```

`--stage M7` reports `in scope: 7   proven: 7   unmet MUST: 0`. At the time of
writing the full `make acceptance` reported
`in scope: 117   proven: 103   unmet MUST: 10` — M7 added seven in-scope criteria and
proved all seven; the ten unmet MUSTs were the inherited v0.1/v0.2 items in the
[acceptance baseline](../releases/v0.3/acceptance-baseline.md), which M8 owns.

Migration `0016_v03_m7_enterprise_auth` must be reversible, against PostgreSQL on port
5433:

```bash
uv run --no-sync alembic upgrade head
uv run --no-sync alembic downgrade 0015_v03_m5_research_evidence
uv run --no-sync alembic upgrade head
```

The Postgres store round-trip and migration tests skip unless
`ACCRETION_TEST_POSTGRES_URL` is set, so set it before reading their result as evidence:

```bash
ACCRETION_TEST_POSTGRES_URL=postgresql+asyncpg://accretion:accretion@localhost:5433/accretion \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v03_m7_postgres_store.py tests/test_v03_m7_postgres_migration.py
```
