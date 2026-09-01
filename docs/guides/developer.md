# Developer guide

This guide takes a new contributor from a clean checkout to a verified local
change. For a five-minute product tour, use the [showcase](showcase.md). For the
implemented route map and React data model, use the
[operator frontend guide](frontend.md).

<img src="../assets/developer-journey.svg" alt="Developer journey from clone through a protected contribution" width="100%" />

## 1. Choose a baseline

- Evaluate the current release from `v0.3.0`, the previous release from `v0.2.0`,
  and the frozen static control from `v0.1.0`.
- Build the next release from the latest `develop`.
- Do not base work on the historical `codex/v0.1-local-control-plane` prototype.

```bash
git clone https://github.com/santapong/Accretion.git
cd Accretion
git switch develop
```

## 2. Install and initialize

Prerequisites are Python 3.12+, `uv`, Node.js 22+, npm, Git, and Docker Compose.

```bash
cp .env.example .env
uv sync --all-groups
npm ci
make dev-db
make migrate
```

## 3. Start the local stack

Run these in separate terminals:

```bash
make api
```

```bash
make ui
```

Open the operator UI at `http://localhost:5173` and API documentation at
`http://localhost:8000/docs`. Start with the `FAKE` runtime. Signed-in Codex and
Claude sessions remain opt-in and are not required for local development.

<img src="../assets/operator-ui-map.svg" alt="Implemented Accretion operator frontend routes and their authoritative FastAPI snapshot, React Query, and resumable event flow" width="100%" />

The UI is complete for the P0–P7 and v0.3 M6 administration scope. It renders
API-backed evidence across seventeen routes; it does not own run state or
acceptance. The v0.3 clean-checkout and accessibility evidence is recorded in the
[release audit](../releases/v0.3/audit.md) and
[browser and accessibility evidence](../releases/v0.3/browser-a11y-evidence.md).

## 4. Run the deterministic showcase

```bash
uv run python examples/showcase.py --repository "$PWD"
```

The expected terminal state is `SUCCEEDED`, with `output-contract` and
`trajectory-policy` verification results. Inspect the same run in the UI or use:

```bash
curl http://localhost:8000/api/v1/runs/<run-id>/audit
curl http://localhost:8000/api/v1/runs/<run-id>/graph
curl http://localhost:8000/api/v1/runs/<run-id>/trace
```

## 5. Find the code you need

| Change | Primary code | Evidence |
|---|---|---|
| API contract | `src/accretion/api/` | API tests + generated TypeScript schema |
| Task profiling or selection | `src/accretion/planning.py` | planning tests + recorded decision rationale |
| Runtime protocol | `src/accretion/runtimes/` | protocol fixtures + opt-in live tests |
| Loop or graph execution | `src/accretion/services/run_manager.py` | state-machine, replay, and PostgreSQL tests |
| Persistence | `src/accretion/persistence/` + `migrations/` | migration round-trip + store tests |
| Verification | `src/accretion/verifiers/` | fail-closed verifier tests |
| Capability governance | `src/accretion/governance.py` | policy and side-effect-ledger tests |
| Operator UI | `apps/ui/src/` | generated API contract + component tests + production build |
| Benchmark | `src/accretion/*_benchmark.py` + `evals/` | fixture hashes + deterministic ACR-ARCH/P5/P6/P7 replay metrics |
| Verified experience | `src/accretion/experience/` | compatibility, redaction, retrieval, replay, and PostgreSQL tests |

## 6. Preserve the authority boundary

<img src="../assets/trust-boundary.svg" alt="Deterministic capability and credential boundary around external runtimes" width="100%" />

Provider output may propose work, but it cannot raise permission ceilings,
validate its own result, expose credentials, or silently retry uncertain side
effects. New integrations must enter through capabilities and durable policy.

## 7. Verify before opening a PR

```bash
make check
make test
make acceptance       # every SDD criterion; CI gates this, unscoped
make release-gate     # the five SDD 24.8 conditions
npm run build
npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts
```

`make acceptance` is what CI enforces, so a criterion that loses its claiming
test fails here before review. `scripts/check_acceptance.py --stage <milestone>`
still exists as a faster local diagnostic, but it is no longer what gates the
repository — and unlike the unscoped run it reports PASS over an empty scope.

Changes to persistence must also pass `alembic upgrade head`, `alembic downgrade
base`, and a second upgrade against a **clean** PostgreSQL database. A
development database holding real run data correctly fails the downgrade:
migration 0004 refuses to drop `agent_events.node_id` values longer than 40
characters. Live-provider tests remain explicitly opt-in.

## 8. Submit the change

Follow [CONTRIBUTING.md](../../CONTRIBUTING.md) and the
[branch policy](../governance/branch-policy.md). Keep the branch focused, explain the outcome
and recovery path, and let the required backend and frontend checks finish before
squash-merging.
