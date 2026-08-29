# Enterprise-managed authorization runbook

How v0.3 M7 delivers optional Enterprise-Managed Authorization (EMA). The normative
contract is [Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §8, §24.9 and §27 M7,
together with OQ3-08 and INV3-009.

EMA is **off by default**. With the flag off, an `EMA` connector behaves exactly as an
unauthorized OAuth connector, and nothing in this runbook is reachable. EMA also does
not replace Accretion policy: a centrally issued token still passes through capability
policy, connection isolation and the audit trail unchanged (INV3-002, AC3-SEC-01..05).

## Acceptance criteria

Seven MUST criteria, `AC3-EMA-01..07`, recorded in SDD §24.9. Until the milestone's
implementation PRs land they are `not_yet_due` in `docs/acceptance/criteria.toml`, and
CI runs `scripts/check_acceptance.py --stage M7` on every pull request.

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

## Operating

| Setting | Default | Effect |
|---|---|---|
| `enable_enterprise_auth` | `False` | Master switch. Off ⇒ EMA connectors report `AUTH_REQUIRED`. |
| `enterprise_auth_token_exchange_url` | `""` | Empty ⇒ inert even with the flag on. |
| `enterprise_auth_audiences` | `{}` | Per-connector audience for the identity assertion. |

Turning the flag on without an exchange URL is deliberately inert rather than an error,
so the flag can be enabled ahead of the enterprise deployment that backs it.

## Verification

```bash
uv run --no-sync python scripts/check_acceptance.py --stage M7
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
```
