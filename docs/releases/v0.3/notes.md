# Accretion v0.3.0 release notes

Version: `v0.3.0`. Theme: **Plugin, MCP & Identity Integration Platform**.

v0.3.0 turns the v0.2 control plane into an integration platform: capabilities
now resolve through connectors, connections and bindings rather than environment
variables; principals sign in through OIDC; credentials live in an encrypted
broker instead of the process environment; and remote MCP servers, plugins and a
research capability set are governed by the same policy, audit and isolation
rules as everything before them.

Every dynamic and integration feature remains **disabled by default**, and none
of them can expand permissions, bypass approvals or verifiers, expose
credentials, or erase durable execution history.

## Highlights

- **M0 connection-aware capabilities** — connector, connection and binding
  contracts with a capability resolver; every v0.1/v0.2 capability resolves
  unchanged.
- **M1 identity and SSO** — principals keyed by issuer and subject, workspaces
  and memberships, an OIDC Authorization Code + PKCE client, session middleware
  with a `LOCAL_PRINCIPAL` default for local development.
- **M2 token broker** — an encrypted secret store, single-use OAuth
  transactions, broker-backed capability invocation, and a GitHub connector with
  the full connect / callback / reauthorize / revoke / health lifecycle.
- **M3 remote MCP** — authenticated MCP SDK v2 HTTP discovery and invocation,
  durable per-connection discovery snapshots, server lifecycle and audit
  records, health and circuit-breaker state.
- **M4 plugin manager** — a plugin version registry, workspace-scoped
  installations, manifest trust and dependency resolution, and a resolver gate.
- **M5 research intelligence** — an MCP-backed research capability set with
  evidence normalization, connector-attributed sources, and trust that the
  connector cannot assert for itself.
- **M6 operator administration** — Plugins, Connections, MCP Servers, Capability
  Inspector and Identity pages, plus run-detail inspectors that project real
  audit provenance onto the graph.
- **M7 enterprise-managed authorization** — optional EMA, off by default, in
  which a centrally issued token still passes through capability policy,
  connection isolation and the audit trail unchanged.
- **M8 release hardening** — the release gate became executable and every
  inherited acceptance criterion is now claimed.

## Acceptance

| | Count |
|---|---:|
| Criteria across the three SDDs | 117 |
| Proven by a passing claiming test | 111 |
| Proven by the frontend suite | 3 |
| Proven by a recorded live-provider run | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

`make acceptance` exits PASS, and CI gates that full harness rather than the
eight stage-scoped subsets it replaces. `make release-gate` evaluates SDD §24.8
as five separately failable conditions, all of which pass.

## Honest limitations

This release does not claim more than it proved.

- **The P5/P6/P7 benchmarks are replays of frozen traces, not live
  experiments.** Every published figure is pinned to a literal value and shown
  to be derived from the corpus it summarizes, but a replay is a
  reproducibility guarantee, not a new measurement. See ADR3-M8-003.
- **The P7 stale-rejection figure is served from the declared corpus.**
  `V02-P7-003` is proven directly against the real `ExperienceService.assess()`
  across all 19 reason codes, but the benchmark's two API routes still report
  `stale_rejection_source: DECLARED`. The assessed path exists and is exercised
  by the acceptance tests. Closing the document half is a v0.4 item; see
  ADR3-M8-005.
- **Three criteria are `manual`.** `V01-P0-002`, `V01-P0-004` and `V01-P4-008`
  describe real vendor CLIs and cannot run in CI. They are backed by a dated
  evidence document from a real signed-in run and **expire on 2027-02-28**.
- **The §24.8 counters are derived, not telemetered.** SDD §21's fourteen
  metrics remain unimplemented; `secret_exposure_incidents` and
  `capability_policy_bypass` are computed from test outcomes and audit rows.
  See ADR3-M8-002.
- **The token broker's reach is not universal.** Deferred v0.4 work is
  enumerated in [backlog.md](backlog.md) rather than implied to be absent.

## Accessibility

axe-core 4.10.2 reports **zero violations across all seventeen routes**. No
route scrolls horizontally at 390 px, and none of 1,421 measured text nodes
falls below WCAG AA. The four findings inherited from v0.2 (F1–F4) are closed;
two of them were partly misdiagnosed and the corrections are recorded rather
than quietly fixed. See
[browser-a11y-evidence.md](browser-a11y-evidence.md).

## Upgrading

Migrations `0010` through `0016` apply in order and are reversible against a
clean database:

```bash
uv run alembic upgrade head
```

The `LOCAL_PRINCIPAL` session mode is the default, so an existing local
deployment keeps working without an identity provider. Every v0.3 feature flag
defaults to off; enabling one is an explicit per-project decision.

## Links

- [Release audit](audit.md)
- [Acceptance baseline](acceptance-baseline.md)
- [Release-hardening runbook](../../runbooks/v03-release-hardening.md)
- [Deferred work](backlog.md)
