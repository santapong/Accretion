# Accretion SDD Index — v0.1 to v0.3

## Release map

| Release | Primary responsibility | Prerequisite |
|---|---|---|
| **v0.1 — Observable Static Meta-Harness** | Claude/Codex runtimes, TaskProfiler, deterministic DIRECT/LOOP/GRAPH/HYBRID selection, static templates, verifier/harness/capability foundation, basic MCP gateway, React Flow, ACR-ARCH | none |
| **v0.2 — Dynamic Workflow Meta-Harness** | Workflow synthesis/validation/replanning, performance-aware runtime routing, cross-provider search, basic experience retrieval/replay | v0.1 acceptance gate |
| **v0.3 — Plugin, MCP & Identity Integration Platform** | Plugin manager, connector registry, remote MCP lifecycle, OAuth/OIDC, SSO, token broker, per-user/workspace connections, research/design plugins, enterprise-managed authorization | v0.2 acceptance gate |
| **v0.4 — Evidence-Aware Node Configuration Routing (locked SDD in `future/v0.4-v1.0/`)** | Learned node-level execution configuration routing, contextual bandits/offline policy research | v0.3 acceptance gate + stable telemetry/eval data |

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

## Forward architecture (v0.4–v1.0)

The complete forward architecture from v0.4 through v1.0 lives in
[`future/v0.4-v1.0/`](future/v0.4-v1.0/00_READ_ME_FIRST.md), integrated verbatim from the
`Accretion_v0.4_to_v1.0_Codex_Package` (all files verified against its
`MANIFEST.sha256`). Start with `00_READ_ME_FIRST.md`; the normative reading order is:

1. `01_GOVERNANCE/Accretion_Golden_Direction_v0.4.md`;
2. `01_GOVERNANCE/Accretion_Cross_Release_Contract_Registry_v0.4_to_v1.0.md`;
3. the currently unlocked SDD;
4. earlier released SDDs/ADRs;
5. locked future SDDs (`02_SDDS/`, v0.4–v1.0);
6. `03_RESEARCH/Accretion_v0.4_Research_Protocol.md`;
7. background charters (`04_BACKGROUND/`).

These SDDs are **locked**: each release unlocks only when the previous release's
acceptance gate passes (v0.4 requires the v0.1–v0.3 gates). They may guide interface
seams and naming, but no feature from a locked release ships early, and the package
files themselves must not be edited (they are hash-manifested).

Baseline note: the package was prepared against `develop@9b59977` and describes P2 as
planned. The v0.1 P0-P4 implementation and release gate have since completed; this
historical note does not unlock v0.2 or v0.3 scope before the `v0.1.0` release tag.
