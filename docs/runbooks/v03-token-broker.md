# Token broker runbook

How to operate the credential store introduced by v0.3 M2. The normative contract is
[Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §13.

## What the broker holds

The broker is the only component that receives or decrypts refresh tokens
(ADR3-004). Everything else — the resolver, the capability gateway, agent runtimes,
the API, and the frontend — sees an opaque `token_handle_ref` and never a token
value (INV3-002, INV3-003).

Two tables carry the state, and neither holds plaintext:

- `token_handles` — handle metadata: connector, owner, issuer, granted scopes,
  audience, expiry, status, and the `secret_store_key` that addresses the ciphertext.
- `secret_records` — the AES-256-GCM envelope: key id, nonce, and ciphertext.

The master key lives outside PostgreSQL, so a database dump alone cannot open a
stored credential.

## Master key

Generate one before enabling any OAuth connector:

```bash
uv run python -c \
  "from accretion.secrets_store import generate_master_key; print(generate_master_key())"
```

Set it as `ACCRETION_TOKEN_ENCRYPTION_KEY`. It must decode to exactly 32 bytes. A
missing or malformed key fails closed: the broker refuses to store or open
credentials rather than falling back to plaintext.

### Rotation

Every sealed record stores the `key_id` it was sealed under. Opening a record whose
key is no longer available fails closed with a message naming the key, rather than
returning corrupt material. There is no automatic re-wrap in M2, so rotating the key
means every existing connection must be re-authorized. Plan rotation as an operator
action, not a routine one.

## Deviation from SDD §13.3

§13.3 states:

```text
Preferred:
OS keyring / dedicated encrypted secret store

Acceptable development fallback:
application-level envelope encryption with a master key outside PostgreSQL
```

M2 ships the **fallback**, behind the `SecretStore` abstraction that OQ3-02 requires.
This is a deliberate, recorded deviation: an OS keyring is awkward headless, in
containers, and under systemd, and it would make the test suite depend on a system
keyring. A keyring or KMS backend can replace `EnvelopeSecretStore` without changing
callers or any stored row, because the key id and envelope travel with each record.

Revisit when the deployment target stops being local-first.

## Lifecycle and failure modes

| Situation | Broker behavior |
|---|---|
| Access token within two minutes of expiry | Refreshes transparently before returning material |
| Grant has no refresh token | Handle becomes `EXPIRED`; the connection needs re-authorization |
| Authorization server rejects the refresh | Handle becomes `ERROR`; fails closed on the next call |
| Requested scopes exceed the granted scopes | Refused before any call is made |
| Requested audience is not covered by the grant | Refused (AC3-CON-06) |
| Revocation | Ciphertext is deleted, handle becomes `REVOKED`, capabilities fail closed (INV3-012) |

Revocation is best effort against the provider and authoritative locally: if the
authorization server is unreachable, the local credential is still destroyed.

## Diagnosing without leaking

Token handles are safe to log and are deliberately exempt from redaction, because
INV3-011 needs them as the correlation key for connection, refresh, and revocation
audit events. Token values are never loggable: `EphemeralCredential` renders as
`EphemeralCredential(handle=..., redacted)` in both `repr` and `str`, and only the
tool-execution boundary calls `reveal()`.

When a capability fails to resolve, start from the handle id in the event trace, then
check the handle's `status` and `scopes`. Never add a debug line that formats a token.
