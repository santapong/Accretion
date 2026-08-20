<div align="center">

# Accretion

### Observable, deterministic orchestration for local AI coding agents

Accretion supervises Codex and Claude Code through one provider-neutral control
plane, with isolated workspaces, durable planning decisions, and a complete
normalized execution trace.

[![CI](https://github.com/santapong/Accretion/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/santapong/Accretion/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![License](https://img.shields.io/badge/License-MIT-6f42c1.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--release-f59e0b)](#project-status)

<br />

<img src="docs/assets/accretion-hero.png" alt="Two agent-runtime streams converging through the Accretion control plane into an observable event trace and isolated workspaces" width="100%" />

</div>

> [!IMPORTANT]
> Accretion is pre-release, local-first software. P0 runtime feasibility and P1
> deterministic planning are implemented. LOOP, GRAPH, and HYBRID execution
> remain intentionally blocked until their P2/P3 engines are available.

## Why Accretion

AI coding runtimes expose different protocols, session models, event formats,
and failure behavior. Accretion puts a durable control plane around them so an
operator can answer four questions before and during every run:

- **What will execute?** Typed task contracts produce an inspectable profile and
  a versioned strategy decision.
- **Why was it selected?** Every score, unknown feature, matched rule, alternative,
  and override is visible and persisted.
- **Where can it make changes?** Each mutable run receives an isolated disposable
  Git worktree with explicit capability constraints.
- **What actually happened?** Provider activity becomes a normalized,
  append-only event stream that survives UI reconnects and backend restarts.

## Capabilities

| Area | Included today |
|---|---|
| Runtime control | Structured Codex App Server and Claude Code adapters, plus a deterministic fake runtime |
| Task planning | Versioned prompt/context contracts, deterministic profiling, static strategy selection, and explicit unknown handling |
| Safety | High-risk and irreversible-task gates, audited overrides, capability allow/deny lists, and side-effect idempotency |
| Isolation | One Git worktree lease per mutable run with captured diff artifacts |
| Observability | Durable PostgreSQL state, monotonic normalized events, resumable SSE, runtime health, and usage pressure |
| Operator experience | React task creation, planning review, override feedback, runtime dashboard, and live trace inspection |

## Architecture

<div align="center">
  <a href="docs/assets/accretion-architecture.svg">
    <img src="docs/assets/accretion-architecture.svg" alt="Accretion architecture: the React operator interface sends tasks to the FastAPI control plane for deterministic planning and safe execution through Codex, Claude, or the fake runtime; PostgreSQL, resumable SSE, and disposable Git worktrees provide the durable foundation" width="100%" />
  </a>
</div>

<p align="center"><sub>Click the diagram to open the full-size version.</sub></p>

The profiler reads typed task metadata and deterministic repository evidence. It
does **not** parse objective keywords or invoke an LLM. The selector persists one
of the following static decisions:

| Mode | Template | P1 behavior |
|---|---|---|
| `DIRECT` | `direct-v1` | Executable now |
| `LOOP` | `feedback-loop-v1` | Selected and persisted; execution arrives in P2 |
| `GRAPH` | `fixed-graph-v1` | Selected and persisted; execution arrives in P3 |
| `HYBRID` | `hybrid-rd-v1` | Selected and persisted; execution arrives in P3 |
| Safe fallback | `safe-unknown-v1` | Used for low confidence or required unknowns; execution blocked in P1 |

## Project status

| Milestone | Status | Scope |
|---|---|---|
| P0 — Runtime feasibility | Complete | Runtime adapters, isolation, normalized events, health, recovery, and idempotency |
| P1 — Deterministic planning | Complete | Prompt/context contracts, profiling, selection, persistence, API, and New Task UI |
| P2 — Feedback loops | Planned | Bounded loop execution and independent verification |
| P3 — Static graphs | Planned | GRAPH/HYBRID engines, checkpoints, replay, and React Flow visualization |

See the [v0.1 system design](docs/sdd/Accretion_SDD_v0.1.md) and the
[multi-release SDD index](docs/sdd/Accretion_SDD_INDEX_v0.3.md) for the full
architecture and acceptance criteria.

## Quick start

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 22+ and npm
- Git
- Docker with Compose
- Optional: supported, signed-in Codex and Claude Code CLIs for live providers

### Install and migrate

```bash
git clone --branch develop https://github.com/santapong/Accretion.git
cd Accretion
cp .env.example .env

uv sync --all-groups
npm install
docker compose up -d postgres
uv run alembic upgrade head
```

### Start the control plane

Run the API and UI in separate terminals:

```bash
make api
```

```bash
make ui
```

Open `http://localhost:5173`. The API is available at
`http://localhost:8000`, with interactive documentation at
`http://localhost:8000/docs`.

Live providers are disabled by default. Enable them only after confirming the
local CLIs are installed and signed in:

```dotenv
ACCRETION_ENABLE_LIVE_PROVIDERS=true
```

Accretion checks CLI authentication status but never reads or copies raw
provider credentials.

## Configuration

All settings use the `ACCRETION_` prefix and can be placed in `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `ACCRETION_DATABASE_URL` | Local PostgreSQL | Authoritative state and event store |
| `ACCRETION_DATA_DIR` | `.accretion` | Worktrees and captured artifacts |
| `ACCRETION_ENABLE_LIVE_PROVIDERS` | `false` | Allows Codex and Claude executions |
| `ACCRETION_GLOBAL_MAX_RUNS` | `4` | Global concurrency ceiling |
| `ACCRETION_PROVIDER_MAX_RUNS` | `2` | Per-provider concurrency ceiling |
| `ACCRETION_PROJECT_MAX_RUNS` | `2` | Per-project concurrency ceiling |
| `ACCRETION_OPERATOR_IDENTITY` | `local-operator` | Identity recorded in override audits |

See [.env.example](.env.example) for the complete local configuration.

## Verification

Run the standard backend and frontend checks:

```bash
make check
make test
npm run build
npm run api:generate
```

PostgreSQL integration tests use `ACCRETION_TEST_POSTGRES_URL`. Signed-in live
provider tests are deliberately opt-in:

```bash
ACCRETION_LIVE_PROVIDERS=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin -m live
```

The validated CLI range and recorded acceptance evidence are documented in the
[P0 runbook](docs/P0_RUNBOOK.md).

## Repository guide

```text
apps/ui/                  React operator interface
src/accretion/api/        FastAPI routes and schemas
src/accretion/runtimes/   Codex, Claude, and fake runtime adapters
src/accretion/persistence Durable state, planning history, and side effects
src/accretion/planning.py Deterministic profiler and selector policy
migrations/               Alembic schema history
tests/                    Unit, API, PostgreSQL, and live acceptance tests
docs/sdd/                 Versioned system design specifications
```

## Contributing and security

Development uses short-lived branches and pull requests into protected
`develop`; stable releases are promoted to `main`. Before contributing, read
[CONTRIBUTING.md](CONTRIBUTING.md) and the
[branch policy](docs/BRANCH_POLICY.md).

Please report security concerns through the process in
[SECURITY.md](SECURITY.md), not through a public issue.

## License

Accretion is available under the [MIT License](LICENSE).
