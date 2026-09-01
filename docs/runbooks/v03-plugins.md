# Plugin manager runbook

How to operate the plugin manager introduced by v0.3 M4. The normative contract is
[Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §9, §19.3, and §20.3.

The governing rule is ADR3-006 (§9.4): **plugin manifests are requests; policy is
authority**. A manifest declares what a package would like to do. Nothing in it grants
anything. Every capability a plugin ships is put through the same
`CapabilityPolicyEngine` that governs every other capability, and only what that engine
returns is registered.

## The state machine

`PluginInstallation.state` is one of nine states:

```text
DISCOVERED → VALIDATING → INSTALLED → SETUP_REQUIRED → READY → ENABLED ⇄ DISABLED
                                   ↘ FAILED                            ↘ REMOVED
```

| State | Meaning |
|---|---|
| `DISCOVERED` | The package reference resolved and an installation row exists. Nothing is registered. |
| `VALIDATING` | Trust, digest, manifest shape, and connector dependencies are being checked. |
| `INSTALLED` | Granted skills, capabilities, and verifiers are registered. Plugin-declared MCP servers are registered `enabled=False`. |
| `SETUP_REQUIRED` | A **required** connector is unresolved. Registrations stay in place; execution is refused until the gap is closed. |
| `READY` | Everything policy granted is registered and satisfied, but bindings are not yet executable. |
| `ENABLED` | Bindings are executable. |
| `DISABLED` | Bindings exist with `enabled=False`. Reached by an administrator, or automatically when policy withheld any requested capability. |
| `FAILED` | Terminal for this attempt: trust, manifest, or policy refused the package outright. Only `VALIDATING` (retry) or `REMOVED` leave it. |
| `REMOVED` | Terminal. Registrations are disabled; no evidence is touched. |

Every state change goes through one private `_transition` in
`src/accretion/plugins/manager.py`, which validates the edge against a single
`_ALLOWED_TRANSITIONS` table, persists, and only then appends a `PluginAuditEvent`.
§20.3's "all state transitions emit append-only audit events" is therefore a property
of the code shape rather than of per-call-site discipline.

Installation state is **workspace-scoped** (`UNIQUE(workspace_id, plugin_id)`), mirroring
M3's remote MCP servers. The version registry — `plugin_versions`, holding the full
manifest, its digest, and the trust level earned per `(plugin_id, version)` — is
**global**, because a historical trace must dereference the version it actually ran
regardless of which workspace still has it installed. The SDD states neither; both are
M4 decisions.

State is never written into `MetaPlugin` or `plugins.definition`. That table is
immutable per `(plugin_id, version)` — `upsert_plugin` raises `ValueError` on any drift
— so storing lifecycle state there would raise on the first enable. `MetaPlugin` remains
the narrow registry projection; `MetaPluginManifest` is the new package declaration;
mutable state lives in `plugin_installations`.

### ADR3-M4-001 — §20.3 is the plugin state machine, not §9.2

**Status:** accepted, v0.3 M4.

**Context.** The SDD lists plugin states twice and the lists differ. §9.2 gives a linear
chain `DISCOVERED → VALIDATING → INSTALLED → CONFIGURATION_REQUIRED → READY → ENABLED →
DISABLED → REMOVED`. §20.3 gives `DISCOVERED, VALIDATING, INSTALLED, SETUP_REQUIRED,
READY, ENABLED, DISABLED, FAILED, REMOVED`. An implementation must pick one; the token
that reaches `apps/ui/src/api/schema.d.ts` becomes a registry-stability liability the
moment anything outside this repository reads it.

**Decision.** Adopt §20.3 verbatim. No aliases.

**Why.** §20.3 is the superset. `CONFIGURATION_REQUIRED` is the single dissenting token,
and `SETUP_REQUIRED` names the same condition. Only §20.3 carries `FAILED`, which
AC3-PLG-02's "fails according to policy" branch needs as a terminal state — a package
whose every requested capability is denied has no meaningful `DISABLED` to land in,
because there is nothing to re-enable. §20.3 also matches the M6 plugin-administration
mock. Adding `CONFIGURATION_REQUIRED` as an alias would put two tokens for one condition
into the generated frontend types and make both permanent.

**Consequences.** §9.2 keeps the dissenting token. `docs/sdd/` is hash-manifested and
must not be edited, so this record is the divergence note. Someone should decide whether
§9.2 is corrected upstream in v0.4 or annotated as superseded; until then, §20.3 is what
the code, the API, and the generated types mean.

## Trust levels

`PluginTrustLevel` is §19.3's list, ranked `BLOCKED < UNVERIFIED_DEV <
WORKSPACE_APPROVED < SIGNED_THIRD_PARTY < BUILTIN`.

| Level | How a package earns it |
|---|---|
| `BUILTIN` | The plugin id is listed in `ACCRETION_PLUGIN_BUILTIN_IDS`. If it also carries a `SHA256_PIN`, the pin must match. |
| `SIGNED_THIRD_PARTY` | A detached Ed25519 signature over the canonical manifest digest verifies against a configured key (the default level a key confers). |
| `WORKSPACE_APPROVED` | Same, with the key configured as `WORKSPACE_APPROVED:<key>`. |
| `UNVERIFIED_DEV` | No signature, or only a `SHA256_PIN` — which pins content but attests no authorship. Refused unless `ACCRETION_PLUGIN_ALLOW_UNVERIFIED_DEV=true`. |
| `BLOCKED` | Never returned by verification; it is the floor that `satisfies()` always refuses. |

§19.3's "risky capabilities may require a minimum plugin trust level" is enforced as a
floor computed from the manifest's own capabilities: `LOW`/`MEDIUM` need
`UNVERIFIED_DEV`, `HIGH` needs `WORKSPACE_APPROVED`, `CRITICAL` needs
`SIGNED_THIRD_PARTY`. The floor is the strongest requirement across every declared
capability, and it is checked at install, so a package cannot ship a `CRITICAL`
capability behind an unsigned manifest.

Signature verification costs no new dependency: `cryptography` is already direct, for
the token broker.

### Registering an Ed25519 key

Generate a keypair and print the public half in the form the settings expect:

```bash
uv run python - <<'PY'
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

private = Ed25519PrivateKey.generate()
print("private (keep offline):", base64.b64encode(
    private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
).decode())
print("public  (settings):", base64.b64encode(
    private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
).decode())
PY
```

Configure the public half, keyed by the `key_id` the manifests will name:

```bash
ACCRETION_PLUGIN_TRUSTED_KEYS='{"vendor-2026":"<base64 public key>"}'
# or, to confer the lower level:
ACCRETION_PLUGIN_TRUSTED_KEYS='{"internal-2026":"WORKSPACE_APPROVED:<base64 public key>"}'
```

A key may only confer `WORKSPACE_APPROVED` or `SIGNED_THIRD_PARTY`. Naming `BUILTIN` or
`BLOCKED` is refused at startup, so a key cannot promote a third-party package to the
level reserved for packages shipped in the image.

The signature itself is over the **canonical manifest digest** — the same
canonical-JSON digest `governance.py` uses for policy checksums — signed as its ASCII
hex string, and placed in the manifest's `signature` block:

```json
"signature": {"algorithm": "ED25519", "key_id": "vendor-2026", "value": "<base64 signature>"}
```

Key rotation and revocation are **not** in M4. Rotating a key today means re-signing and
re-installing every package that key signed. Deferred to v0.4, with archive ingestion.

## Installing the bundled sample plugin

`accretion-sample-plugin` ships in the image at
`src/accretion/plugins/bundled/accretion-sample-plugin/plugin.json`. It declares one
`LOW`-risk read capability (`accretion.sample.echo`), one `MEDIUM`-risk side-effecting
one (`accretion.sample.record`), two skills, and one *optional* connector. It exists to
exercise the manager end to end; it is not a product feature.

A package reference is a single path segment resolved under the configured package
roots — anything containing a separator, a drive, or `..` is refused before it touches
the filesystem.

1. **Read the manifest and its digest.** Consent must echo the digest the administrator
   was actually shown, so fetch it first:

   ```bash
   curl -s "$API/api/v1/plugins/accretion-sample-plugin?workspace_id=$WS" | jq '.'
   ```

   Before install there is no installation, so read the digest from the package:

   ```bash
   uv run python -c "
   import json, pathlib
   from accretion.contracts import MetaPluginManifest
   from accretion.plugins.manifest import canonical_manifest_digest
   path = pathlib.Path('src/accretion/plugins/bundled/accretion-sample-plugin/plugin.json')
   print(canonical_manifest_digest(MetaPluginManifest.model_validate(json.loads(path.read_text()))))
   "
   ```

2. **Install.** The caller must be a workspace administrator.

   ```bash
   curl -s -X POST "$API/api/v1/plugins/install" \
     -H 'content-type: application/json' \
     -d '{
       "workspace_id": "'"$WS"'",
       "reference": "accretion-sample-plugin",
       "consent_digest": "'"$DIGEST"'",
       "consent_capability_ids": ["accretion.sample.echo", "accretion.sample.record"]
     }'
   ```

   `consent_capability_ids` may **narrow** what policy granted; it can never widen it.
   Leaving it empty consents to nothing. A `consent_digest` that does not match the
   manifest actually being installed is refused — that is what stops a package being
   swapped between the approval screen and the install call.

3. **Confirm without restarting.** The response should be `ENABLED`. The capability is
   resolvable immediately, in the same process — no restart, no reload (AC3-PLG-01):

   ```bash
   curl -s "$API/api/v1/capabilities" | jq '.[] | select(.capability_id | startswith("accretion.sample"))'
   ```

4. **Read the trail.**

   ```bash
   curl -s "$API/api/v1/audit/plugins?plugin_id=accretion-sample-plugin&workspace_id=$WS" | jq '.'
   ```

A healthy install emits, in order: `DISCOVERED`, `INSTALL_VALIDATING`,
`INSTALL_INSTALLED`, `READY`, `ENABLED`.

### When a plugin asks for more than policy allows

`dev-overreach` is the counter-fixture: a package requesting `github.write`, which the
local capability policy does not grant. Installing it demonstrates AC3-PLG-02.

- If policy withholds **some** requested capability, the denied capability is never
  registered — there is no `Capability` row and no `CapabilityBinding` — and the
  installation lands `DISABLED` with a `DISABLED_BY_POLICY` event naming
  `withheld_capability_ids`. An administrator must act deliberately.
- If policy withholds **every** requested capability, the installation lands `FAILED`.

In neither branch does the plugin gain authority automatically, and in neither branch is
policy consulted a second time at execution to make up the difference. The grant set is
computed in full *before* anything is registered.

Plugin-declared MCP servers are registered `enabled=False` and pass M3's endpoint policy
(`ACCRETION_MCP_ALLOWED_HOSTS`, `ACCRETION_MCP_ALLOWED_PORTS`,
`ACCRETION_MCP_ALLOW_LOCAL_HTTP`). A plugin cannot use its manifest to reach an endpoint
an operator would have refused through the M3 routes — see the
[token broker runbook](v03-token-broker.md) for the credential half of that boundary.

## Reading `GET /api/v1/audit/plugins`

```bash
curl -s "$API/api/v1/audit/plugins?plugin_id=<id>&workspace_id=<ws>" | jq '.'
```

Both query parameters are optional; the response is always filtered to workspaces the
calling principal is a member of, and requesting a workspace the caller is not in is an
authorization error rather than an empty list.

Each `PluginAuditEvent` carries `from_state`, `to_state`, `event_type`, the acting
principal, an optional `correlation_id` taken from the request's `x-request-id`, and a
`details` object. The events worth recognizing:

| `event_type` | What it records |
|---|---|
| `DISCOVERED` | The reference resolved; `details.reference` and `details.version`. |
| `INSTALL_VALIDATING` / `UPGRADE_VALIDATING` / `ROLLBACK_VALIDATING` | Trust and manifest checks began. |
| `INSTALL_INSTALLED` / `UPGRADE_INSTALLED` / `ROLLBACK_INSTALLED` | Registration completed; `details.granted_capability_ids`, `registered_mcp_server_ids`, `trust_level`. |
| `SETUP_REQUIRED` | `details.missing_connectors` names the unresolved required connectors. |
| `READY` | Everything granted is registered and satisfied. |
| `ENABLED` | Bindings became executable. |
| `DISABLED` | An administrator disabled the installation. |
| `DISABLED_BY_POLICY` | Policy withheld capabilities; `details.withheld_capability_ids`. |
| `REMOVED` | `details.evidence_deleted` is always `false`. |

The table is append-only. There is no update or delete path for an audit event on either
store backend.

## Disable versus remove

**Disable** flips `Capability.enabled` and `CapabilityBinding.enabled` to `False` for the
ids this installation registered. It deletes nothing. The rows stay, which is what lets a
historical trace keep dereferencing the exact binding a past run used (AC3-PLG-03).

Disabling is enforced in two independent places, deliberately:

1. The cascade — the flags themselves.
2. The **resolver gate** — `resolver._resolve_binding` reads
   `provider_projections["accretion"]["installation_id"]` and refuses to resolve a
   capability whose installation is not in a live, enabled state. This mirrors the M3
   remote-MCP branch and adds no new `CapabilityResolutionOutcome` values.

The gate matters because the cascade alone is not sufficient: a plugin-contributed
capability that someone re-flags `enabled=True` by hand would otherwise resolve after its
plugin was disabled. The acceptance suite asserts exactly that adversarial case.

**Remove** disables the same registrations and moves the installation to `REMOVED`.
It never deletes evidence — not artifacts, not events, not run results, not the
`plugin_versions` row a past trace points at (AC3-PLG-05).

This is not a promise the code makes politely; it is structural. `StateStore` exposes
exactly one deletion method in the entire interface, `delete_secret_record`, which exists
because INV3-012 requires revocation to destroy ciphertext. There is no
`delete_artifact`, no `delete_event`, no `delete_run`. A structural test asserts that the
store surface grows no second deletion method, so "removal cannot delete evidence" fails
the moment someone adds one. Migration `0014_v03_m4_plugin_manager` likewise introduces
no `ON DELETE CASCADE`: dropping a plugin row cannot take evidence with it.

`accretion-core-governance` is protected: it cannot be removed or upgraded through these
routes at all.

## Upgrade and rollback

```bash
curl -s -X POST "$API/api/v1/plugins/<id>/upgrade" \
  -H 'content-type: application/json' \
  -d '{"workspace_id": "'"$WS"'", "reference": "<new package>",
       "consent_digest": "<new manifest digest>",
       "consent_capability_ids": ["..."]}'
```

Upgrade **re-runs the entire install sequence** against the new manifest: trust, digest,
manifest shape, dependency resolution, a fresh policy evaluation, and fresh consent. It
does not diff against the previous decision and it does not inherit it. A v1.1 manifest
that adds `secrets.read` must be granted `secrets.read` on its own merits or land
`DISABLED`; inheriting the v1.0 verdict is the concrete `capability_policy_bypass` risk
this design exists to remove.

The previous version is not disturbed. `plugin_versions` keeps its full manifest and
digest, and `PluginInstallation.previous_version` records where the installation came
from, so a historical trace still dereferences the version that actually ran
(AC3-PLG-04). Re-registering an old version with different content still raises — the
registry stays immutable per `(plugin_id, version)`.

Rollback returns the installation to `previous_version`, through the same sequence and
the same policy evaluation:

```bash
curl -s -X POST "$API/api/v1/plugins/<id>/rollback" \
  -H 'content-type: application/json' -d '{"workspace_id": "'"$WS"'"}'
```

Rollback is not a special case with relaxed checks. A version that would not install
today does not roll back today either, which is the point: an upgrade is reversible
precisely because the earlier version is still a fully verified package rather than a
saved decision.

## Canonical ids across providers

A capability's `capability_id` is the same string for every provider. What differs is the
**projection**: `provider_projections` maps a provider to how that capability surfaces
there — a Claude tool name, a Codex function name. Two providers rendering the same
capability resolve to the same `Capability` and the same canonical id (AC3-PLG-06).

A projection that names an id outside the plugin's own `capabilities` is rejected at
install. Without that check, "canonical ids stay stable" would be an unenforced claim
rather than an invariant.

Accretion's own bookkeeping lives under the reserved `"accretion"` projection key —
`plugin_id` and `installation_id` — which is what the resolver gate reads.

## What M4 does not do

Recorded so an operator does not go looking for it: no live plugin health probing (M5);
no `ui.pages` or `node_badges` rendering — declarations are validated and persisted but
nothing draws them (M6); no first-class `consent_records` or `scope_grants` tables,
consent is embedded in the installation (M6); no YAML manifests, JSON only, so digests
stay consistent with the governance checksum convention; no key rotation or revocation
and no archive ingestion (v0.4). The `capability_policy_bypass` counter now exists: M8 derives it from `CapabilityGateway` audit rows in `scripts/release_gate.py` (ADR3-M8-002), and `make release-gate` reports it.
