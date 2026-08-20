# Accretion SDD Index — v0.1 to v0.3

## Release map

| Release | Primary responsibility | Prerequisite |
|---|---|---|
| **v0.1 — Observable Static Meta-Harness** | Claude/Codex runtimes, TaskProfiler, deterministic DIRECT/LOOP/GRAPH/HYBRID selection, static templates, verifier/harness/capability foundation, basic MCP gateway, React Flow, ACR-ARCH | none |
| **v0.2 — Dynamic Workflow Meta-Harness** | Workflow synthesis/validation/replanning, performance-aware runtime routing, cross-provider search, basic experience retrieval/replay | v0.1 acceptance gate |
| **v0.3 — Plugin, MCP & Identity Integration Platform** | Plugin manager, connector registry, remote MCP lifecycle, OAuth/OIDC, SSO, token broker, per-user/workspace connections, research/design plugins, enterprise-managed authorization | v0.2 acceptance gate |
| **v0.4 — Learned Orchestration (planned)** | Learned runtime/resource policy, contextual bandits/offline policy research | stable v0.3 telemetry + eval data |

## Important boundary

**MCP is split across releases:**

- v0.1 provides the minimal provider-neutral MCP/tool gateway and capability registry.
- v0.2 dynamically selects/uses those capabilities.
- v0.3 turns MCP into a full managed integration product: authentication, remote servers, discovery, account connections, scopes, plugins, SSO, enterprise authorization, admin UI.

## Recommended implementation order

1. Implement and release `Accretion_SDD_v0.1.md`.
2. Run ACR-ARCH and stabilize the runtime/harness foundation.
3. Implement and release `Accretion_SDD_v0.2.md`.
4. Preserve v0.1/v0.2 APIs and implement `Accretion_SDD_v0.3.md` as an additive integration layer.
5. Move learned routing/policy to v0.4 after v0.3 generates trustworthy identity/capability/connection telemetry.
