# v0.3 prioritized backlog

Status: post-v0.2 planning handoff only. No v0.3 feature is included in
v0.2.0; implementation begins from `develop` only after the v0.2.0 tag is
published.

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

### Manifest and release hygiene

- Make `runtime_compatibility.toml` authoritative or remove it. Nothing reads
  it, while the version ranges that actually gate `DEGRADED` live inside each
  adapter's health check, so the two can drift without any signal.
- Record the v0.3 M0 capability/connection layer, the M1 identity layer, and the
  opencode runtime adapter in the changelog; the unreleased section is empty.
- Add the identity settings introduced by M1 to `.env.example`, which documents
  no authentication mode and no identity-provider keys.

## Locked boundary

Do not begin learned node routing, contextual bandits, policy learning,
self-modifying architecture, or automatic promotion of experience into policy.
Those capabilities remain beyond v0.3 and require stable identity, capability,
connection, telemetry, and inherited release evidence first.
