# v0.3 prioritized backlog

Status: post-release planning handoff only. No v0.3 feature is included in the
v0.2.0 candidate, and implementation must not begin before v0.2.0 is released.

The normative contract remains [Accretion SDD v0.3](../../sdd/Accretion_SDD_v0.3.md).
This ledger orders its existing milestones without changing acceptance criteria
or unlocking the hash-manifested v0.4+ designs.

## Delivery order

| Priority | Milestone | Outcome required before continuing |
|---:|---|---|
| 1 | M0 capability/connection refactor | Existing v0.1/v0.2 capabilities resolve unchanged through additive connection-aware contracts |
| 2 | M1 identity and SSO | Issuer/subject principals, workspace membership, OIDC Authorization Code + PKCE, and multi-user tests |
| 3 | M2 token broker and OAuth connections | Encrypted refresh/revoke lifecycle with no token values in model, API, UI, events, or logs |
| 4 | M3 remote MCP manager | Authenticated remote discovery, schema validation, health/circuit breaking, SSRF controls, and canonical capability bindings |
| 5 | M4 plugin manager | Versioned manifest validation, permission requests without grants, enable/disable/upgrade, and historical provenance |
| 6 | M5 research intelligence plugin | Provider-neutral research capabilities, normalized evidence, provenance, and citation verification |
| 7 | M6 frontend and administration | Plugins, Connections, MCP Servers, capability resolution, identity, and roles without exposing secrets |
| 8 | M7 enterprise authorization | Optional EMA integration behind a feature flag after the standard OAuth path is stable |
| 9 | M8 release hardening | Clean-checkout reproduction of every inherited v0.1, v0.2, and v0.3 MUST criterion |

## Carried technical debt

- Split or lazy-load the operator bundle to remove the current Vite chunk-size
  advisory without changing routing or backend authority.
- Automate the manual browser/accessibility release smoke while preserving the
  current deterministic component tests.
- Turn the v0.2 evidence manifest, dependency audits, secret scan, and fixture
  hash validation into a repeatable release command or protected workflow.

## Locked boundary

Do not begin learned node routing, contextual bandits, policy learning,
self-modifying architecture, or automatic promotion of experience into policy.
Those capabilities remain beyond v0.3 and require stable identity, capability,
connection, telemetry, and inherited release evidence first.
