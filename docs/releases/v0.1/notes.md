# Accretion v0.1.0 release notes

Accretion v0.1.0 is the first stable observable static meta-harness release. It
supervises signed-in Codex and Claude Code runtimes through a provider-neutral,
local-first control plane while keeping architecture selection, workflow topology,
policy, and independent acceptance authoritative in code.

<img src="../../assets/accretion-architecture.svg" alt="Accretion v0.1 architecture showing the operator interface, authoritative planning and execution control plane, runtime adapters, durable state, isolated worktrees, normalized events, and independent verification" width="100%" />

New users can reproduce one verified run with the
[deterministic showcase](../../guides/showcase.md); contributors should start at the
[documentation hub](../../README.md).

## Shipped scope

- Structured Codex App Server and Claude stream-JSON adapters with normalized,
  append-only events, health classification, interrupt/resume, and isolated Git
  worktrees.
- Versioned prompt/context contracts, typed deterministic profiling, explainable
  DIRECT/LOOP/GRAPH/HYBRID selection, audited overrides, and a safe unknown fallback.
- Bounded verifier-gated feedback loops and five validated static templates,
  including checkpoint/replay and approval-gated GRAPH/HYBRID execution.
- Immutable capability, skill, plugin, and policy registries; task-scoped MCP tool
  exposure; deny-by-default policy; approval-bound idempotent side effects; and
  credential injection only at the executor boundary.
- Complete operator routes for dashboard, task profiling, live runs, runtimes,
  trace history, approvals, capabilities, and ACR-ARCH. SSE reconnect begins from
  an authoritative snapshot and a monotonic cursor.
- A frozen ACR-ARCH v1 corpus with 30 versioned tasks, 68 replay scenarios balanced
  34/34 across Claude and Codex, independently versioned environments/configuration,
  raw cost/latency/risk/human-burden dimensions, utility, and selector regret.

## Reproducibility

The v0.1 release gate was reproduced from a clean checkout of candidate
`934ea75cf06be820ac6fdd0946e3203982c779c4` on PostgreSQL 16.

| Artifact | SHA-256 |
|---|---|
| ACR-ARCH task corpus | `9251bb918912e73a2dade20189f93cc26cd7bc217a0dea03713ef252843b9dd7` |
| ACR-ARCH replay traces | `2f62f87eaf079914d41f47bea57a4dd04ce469d0e54f1d6a38faa6de0dd6f051` |

The replay produced 30 tasks and 68 scenario metrics. The optional signed-in
calibration selected two tasks from every category, split them evenly across both
providers, and deterministically verified 10/10 provider-written artifacts. Live
calibration does not mutate the frozen replay dataset.

## Runtime compatibility

- Python 3.12 or newer
- Node.js 22 or newer
- PostgreSQL 16
- Codex CLI `>=0.148,<0.149` for live Codex execution
- Claude Code `>=2.1.231,<2.2` for live Claude execution

Live providers remain disabled by default. Accretion checks CLI health and sign-in
state without reading or copying raw authentication tokens.

## Upgrade and verification

```bash
uv sync --all-groups
npm ci
uv run alembic upgrade head
make check
make test
npm run build
```

For signed-in provider acceptance and the separate ACR-ARCH calibration:

```bash
ACCRETION_LIVE_PROVIDERS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin -m live

ACCRETION_LIVE_PROVIDERS=1 \
  uv run --no-sync python scripts/run_acr_arch_live_sample.py
```

## Known constraints

- v0.1 executes only validated static templates; dynamic workflow synthesis and
  learned routing remain locked to later SDDs.
- The visual browser pass could not run in the release environment because no
  controllable browser instance was available. The formal P4 page/functionality
  criterion is covered by route/component tests, API tests, type checking, and the
  production build; no visual-browser result is claimed.
