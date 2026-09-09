<div align="center">

<img src="docs/assets/accretion-banner.png" alt="Accretion — Control the workflow. Trust the evidence. Conceptual artwork of runtime streams converging into an ordered evidence trace." width="100%" />

**An observable control plane for local AI coding agents.**

[![CI](https://github.com/santapong/Accretion/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/santapong/Accretion/actions/workflows/ci.yml?query=branch%3Adevelop)
[![Release](https://img.shields.io/badge/release-v0.4.1-65c997)](docs/releases/v0.4/notes.md)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Node](https://img.shields.io/badge/Node.js-24-339933?logo=nodedotjs&logoColor=white)](.github/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-d6ae67)](LICENSE)

[Quick start](#quick-start) · [Architecture](#architecture) · [Documentation](docs/README.md) · [Changelog](CHANGELOG.md) · [Research](#research-and-evidence)

</div>

Accretion coordinates **Codex, Claude Code, opencode and a deterministic fake runtime**
through one API and operator interface. It turns a task into a governed execution:
an inspectable plan, an isolated Git worktree, bounded feedback loops and an
independently verified outcome. Decisions, events and evidence remain available
for inspection and recovery.

**Latest release: v0.4.1.** The v0.4 milestones and approved closure are complete.
`develop` includes the subsequent [runtime repairs and dependency maintenance](CHANGELOG.md#unreleased);
those changes have not been promoted to a new release.

## Why Accretion

- **See the decision.** Inspect the selected workflow, routing receipt, alternatives
  and override history before relying on an agent's output.
- **Bound the work.** Apply capability policy, approval gates and limits on time,
  turns, tool calls and iterations.
- **Verify the result.** Evaluate an immutable candidate with independent verifiers.
  Completion requires evidence; uncertainty escalates to the operator.
- **Keep the history.** Follow normalized events across runtimes, reconnect to live
  runs and reconcile interrupted execution from durable state.

## Quick start

Use **Python 3.12+, uv, Node.js 24, npm, Git and Docker Compose**. Node 24 is the
version used by CI. A provider account is not required for the fake-runtime demo.

```bash
# Evaluate the immutable release. Use --branch develop for current development.
git clone --branch v0.4.1 https://github.com/santapong/Accretion.git
cd Accretion
cp .env.example .env

uv sync --locked --all-groups
npm ci
docker compose up -d postgres
uv run --no-sync alembic upgrade head
```

Start the API and UI in separate terminals:

```bash
make api
```

```bash
make ui
```

Open the **operator UI at http://localhost:5173** or the
**API reference at http://localhost:8000/docs**.
Then run the bounded, read-only demonstration against a repository:

```bash
uv run --no-sync python examples/showcase.py --repository "$PWD"
```

The [showcase guide](docs/guides/showcase.md) explains the resulting run, verifier
findings and audit records. The [developer guide](docs/guides/developer.md) covers
setup and contribution workflows in more detail.

Live providers are disabled by default. To use installed, signed-in coding
runtimes, set `ACCRETION_ENABLE_LIVE_PROVIDERS=true` in `.env` and follow the
[runtime runbook](docs/runbooks/p0-runtime.md) for supported CLI versions and
connection checks.

## Capabilities

| Capability | What it provides |
|---|---|
| Workflow orchestration | Direct tasks, bounded feedback loops, checkpointed graphs and hybrid research/development workflows |
| Runtime adapters | A shared lifecycle and event model for Codex App Server, Claude Code, opencode and FAKE |
| Independent verification | Output-contract, Git-diff, trajectory-policy and bounded command verifiers |
| Governance and isolation | Capability and connection policy, human approvals, credential brokering and disposable Git worktrees |
| Recovery and observability | Durable run state, append-only events, resumable SSE, audit history and conservative restart reconciliation |
| Integrations | Remote MCP, workspace-scoped plugins, identity/SSO and optional enterprise-managed authorization |
| Search and experience | Opt-in dynamic workflows, bounded candidate search and verified-experience retrieval and replay |
| Node configuration routing | Receipt-first selection, verified feedback, offline ranking, shadow evaluation, guarded exploration and gated promotion |

Dynamic workflows, candidate search, experience retrieval and node routing each
have explicit opt-in controls. See [configuration](#configuration) and the
[operational runbooks](docs/README.md) for their scope.

## How runs work

<img src="docs/assets/project-overview.svg" alt="Four stages of an Accretion run: define the objective and limits, plan under deterministic policy, execute in an isolated worktree, and verify an immutable candidate. Pass completes; failure can trigger bounded repair; inconclusive evidence escalates." width="100%" />

A failed candidate can return structured repair findings to the same runtime
session while budget remains. An inconclusive result, exhausted budget or
uncertain side effect requires an explicit recovery decision. A provider's
completion message cannot override the acceptance policy.

## Architecture

<img src="docs/assets/accretion-architecture.svg" alt="The React operator interface communicates with the FastAPI control plane through HTTP and resumable SSE. Planning, optional routing, execution and verification share governed persistence. Codex, Claude Code, opencode and FAKE connect through runtime adapters; PostgreSQL stores state and events, and Git worktrees isolate mutable runs." width="100%" />

The **control plane owns authority and state**. Runtime adapters normalize
provider behavior; the UI reads authoritative snapshots and ordered events.
Verifiers inspect captured candidates, and governed capability execution checks
policy before dispatch. Recovery preserves uncertainty when an external action
cannot be confirmed.

Read the [v0.4 system design](docs/sdd/Accretion_SDD_v0.4.md),
[trust boundary](docs/assets/trust-boundary.svg) and
[accounting runbook](docs/runbooks/v04-routing-accounting.md) for the contracts
and operational limits behind this diagram.

## Operator interface

The React interface supports task creation and planning, live execution graphs,
pause/resume/cancel controls, approvals, runtime health, history and audit.
Administration pages cover connections, plugins, MCP servers, capabilities,
identity and router activation. The Experiment Studio exposes routing decisions,
shadow comparisons and promotion evidence.

Explore the [frontend guide](docs/guides/frontend.md) and
[route map](docs/assets/operator-ui-map.svg) for the complete operator journey.

## Project status

| Track | Status | Source of truth |
|---|---|---|
| v0.4.1 | Released on 7 September 2026 | [Release notes](docs/releases/v0.4/notes.md) and [frozen baseline](docs/releases/v0.4/baseline.md) |
| v0.4 M0–M10 | Delivered | [Milestone index](docs/releases/v0.4/README.md) and [acceptance baseline](docs/releases/v0.4/acceptance-baseline.md) |
| Approved post-release closure | Merged into `develop` on 9 September 2026 | [Closure execution](docs/releases/v0.4/closure-execution-2026-09-08.md) and [dependency review](docs/releases/v0.4/dependency-review-2026-09-09.md) |
| Earlier releases | Preserved as versioned baselines | [v0.1](docs/releases/v0.1/baseline.md), [v0.2](docs/releases/v0.2/baseline.md), [v0.3](docs/releases/v0.3/baseline.md) |
| v0.5 | Wave 2 construction boundary; complete episodes and qualification remain pending | [Execution checkpoint](docs/releases/v0.5/execution-2026-09-09.md) and [approved plan](docs/releases/v0.5/completion-plan-2026-09-09.md) |
| v0.6–v1.8 | Forward designs, with separate implementation and research gates | [v0.5–v1.0 package](docs/sdd/future/v0.4-v1.0/00_READ_ME_FIRST.md) and [v1.1–v1.8 package](docs/sdd/future/v1.1-v1.8/README.md) |

Node routing is **off by default**, and the shipped fallback catalog is
**FAKE-only**. Selected TOOL nodes enforce exact capability bindings; routed
AGENT sessions carrying selected tools remain denied. Exploration accounting
uses cumulative normalized cost, not monetary billing. These boundaries are
part of the [documented release scope](docs/releases/v0.4/notes.md#honest-limitations).

## Research and evidence

Accretion includes frozen benchmarks for workflow selection, dynamic graphs,
candidate search, verified experience and node routing. The
[research index](docs/research/README.md) links each experiment to its protocol,
fixtures, results, limitations and reproduction procedure.

The v0.4 router study retains **partial paired synthetic evidence** and a
**scoped NO-GO for the full routing-benefit claim**. A priced live-provider
routing study has not been run. The [research handoff](docs/releases/v0.4/research-handoff-2026-09-08.md)
and [frozen results](docs/research/v0.4/results.md) explain that distinction.

Engineering validation recorded on **9 September 2026**:

| Check | Recorded result |
|---|---|
| Backend | 3,544 tests passed; six signed-in provider tests intentionally skipped |
| Frontend | 286 tests passed; generated API contract and production build passed |
| Acceptance | 167 criteria in scope; 159 Python-proven, five frontend and three recorded manual witnesses; zero unmet MUST criteria |
| Release gate | All five conditions passed |
| Protected integration | Backend, frontend, browser and clean-checkout checks passed for both merged PRs |

These results are tied to the commits and runs in the
[closure record](docs/releases/v0.4/closure-execution-2026-09-08.md) and
[dependency review](docs/releases/v0.4/dependency-review-2026-09-09.md).
They do not establish live-provider routing benefit.

## Configuration

Settings use the `ACCRETION_` prefix and can be placed in `.env`.

| Setting | Default | Purpose |
|---|---|---|
| `ACCRETION_ENABLE_LIVE_PROVIDERS` | `false` | Enable signed-in coding runtimes |
| `ACCRETION_ENABLE_DYNAMIC_WORKFLOWS` | `false` | Enable validated dynamic workflow services |
| `ACCRETION_ENABLE_CANDIDATE_SEARCH` | `false` | Enable candidate search; project opt-in also applies |
| `ACCRETION_ENABLE_EXPERIENCE_RETRIEVAL` | `false` | Enable verified-experience retrieval and replay |
| `ACCRETION_ENABLE_NODE_ROUTING` | `false` | Enable evidence-aware node configuration routing |
| `ACCRETION_GLOBAL_MAX_RUNS` | `4` | Limit concurrent runs globally |
| `ACCRETION_PROVIDER_MAX_RUNS` | `2` | Limit concurrent runs per provider |
| `ACCRETION_PROJECT_MAX_RUNS` | `2` | Limit concurrent runs per project |

See [.env.example](.env.example) for database, identity, capability and integration
settings. Provider tests have a separate, explicit opt-in procedure in the
[runtime runbook](docs/runbooks/p0-runtime.md).

## Verification

From the repository root:

```bash
make docs-check
make check
make test
make acceptance
make release-gate
npm run api:generate
npm run build
```

Set `ACCRETION_TEST_POSTGRES_URL` to a disposable PostgreSQL test database for
integration and acceptance checks. Keep signed-in provider tests opt-in.
The [developer guide](docs/guides/developer.md) and
[release-hardening runbook](docs/runbooks/v03-release-hardening.md) describe the
required environment and gate evidence.

## Repository guide

| Path | Contents |
|---|---|
| [`apps/ui/`](apps/ui/) | React operator interface and generated API types |
| [`src/accretion/`](src/accretion/) | API, orchestration, runtime adapters, governance, routing and verification |
| [`migrations/`](migrations/) | Versioned PostgreSQL schema |
| [`tests/`](tests/) | Unit, API, PostgreSQL and opt-in provider checks |
| [`evals/`](evals/) | Frozen benchmark fixtures and evaluation inputs |
| [`examples/`](examples/) | Bounded demonstrations through the public API |
| [`docs/`](docs/) | Guides, runbooks, research, release evidence and system designs |

## Contributing and security

Contributions use short-lived branches and pull requests into protected
`develop`. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[branch policy](docs/governance/branch-policy.md). Stable releases are promoted
to `main` through a separate release workflow.

Report security concerns through [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) · Created by [Santapong](https://resume.draveniq.dev).
