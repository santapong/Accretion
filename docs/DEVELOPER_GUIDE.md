# Developer guide

This guide takes a new contributor from a clean checkout to a verified local
change. For a five-minute product tour, use the [showcase](SHOWCASE.md).

<img src="assets/developer-journey.svg" alt="Developer journey from clone through a protected contribution" width="100%" />

## 1. Choose a baseline

- Evaluate the shipped release from `v0.1.0`.
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
| Operator UI | `apps/ui/src/` | component tests + production build |
| Benchmark | `src/accretion/benchmark.py` + `evals/` | fixture hashes + replay metrics |

## 6. Preserve the authority boundary

<img src="assets/trust-boundary.svg" alt="Deterministic capability and credential boundary around external runtimes" width="100%" />

Provider output may propose work, but it cannot raise permission ceilings,
validate its own result, expose credentials, or silently retry uncertain side
effects. New integrations must enter through capabilities and durable policy.

## 7. Verify before opening a PR

```bash
make check
make test
npm run build
npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts
```

Changes to persistence must also pass `alembic upgrade head`, `alembic downgrade
base`, and a second upgrade against a clean PostgreSQL database. Live-provider
tests remain explicitly opt-in.

## 8. Submit the change

Follow [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[branch policy](BRANCH_POLICY.md). Keep the branch focused, explain the outcome
and recovery path, and let the required backend and frontend checks finish before
squash-merging.
