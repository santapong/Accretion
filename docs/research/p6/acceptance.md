# P6 acceptance report

Status: implementation evidence prepared on 2026-08-24. P7 experience retrieval
and replay are not part of this report.

> This is the frozen P6 milestone report. Current `develop` also includes the P7
> frontend surfaces documented in the [frontend guide](../../guides/frontend.md); the
> historical counts here are intentionally unchanged.

<img src="../../assets/p6-search-lifecycle.svg" alt="P6 bounded candidate-search authority, selection, promotion, and recovery lifecycle" width="100%" />

## Acceptance mapping

| Criterion | Evidence |
|---|---|
| `V02-P6-001` | Best-of-2 executor tests create separate worktree leases and runtime sessions; each candidate retains its own artifact, trajectory, and verifier references. |
| `V02-P6-002` | Shared-budget tests cover per-branch reservations, parent accounting, total wall/turn/tool ceilings, concurrent completion, and the global-limit-one parent-slot case. |
| `V02-P6-003` | Search planning and execution tests deny protected external side-effect capabilities and freeze the parent policy snapshot before candidate launch. |
| `V02-P6-004` | Scoring tests require independent eligibility, select only a unique rounded best score, persist promotion intent, re-evaluate policy, apply only the winner, and record before/after digests. |
| `V02-P6-005` | Tests cover acceptance, shared-budget exhaustion, low expected gain, low diversity, verifier uncertainty, and persist-before-interrupt operator cancellation. |
| `V02-P6-006` | Candidate-failure and recovery tests preserve sibling evidence and parent state, retain interrupted trajectories, conservatively charge reservations, and prohibit automatic rerun. |
| `V02-P6-007` | Cross-provider and generator-reviewer tests persist provider, runtime ID, model, version, reviewer provider, session, spend, and terminal reason for every candidate. |
| `V02-P6-008` | Run UI component tests render a separate candidate search tree with status, lineage, provenance, score, quality, cost/latency proxies, spend, selection, and pruned/failure reason. |
| `V02-P6-009` | Frozen benchmark tests reproduce N=1/2/4 quality-vs-compute points across exactly 12 held-out tasks, preserve provider and null results, and verify exact fixture hashes. |

## Frozen benchmark result

<img src="../../assets/p6-quality-compute.svg" alt="Frozen replay P6 quality versus compute result for one, two, and four candidates" width="100%" />

| Candidates | Verified accepts | Acceptance rate | Mean quality | Mean turns | Mean tool calls | Mean latency (ms) |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 / 12 | 0.666667 | 0.472500 | 1 | 1.833 | 866.667 |
| 2 | 10 / 12 | 0.833333 | 0.608333 | 2 | 3.750 | 937.500 |
| 4 | 12 / 12 | 1.000000 | 0.768333 | 4 | 8.750 | 1091.667 |

The fixture contains one explicit null-gain task (`p6-007`). At N=4, the Claude
replay partition has 12/12 eligible results with mean quality 0.768333; the Codex
partition has 11/12 with mean quality 0.683333. These are deterministic fixture
properties, not claims about current live-provider performance.

Fixture SHA-256 fingerprints:

- `config.v1.json`: `9b910c71729ef6bfef5299cb0b8f22f9c75706268ab59185ca17aacc86c8804a`
- `tasks.v1.json`: `11fcdcfb2a698dec4c7aa00af125345cccfb15efb7edf5441cc29530dde4a63f`
- `replay-traces.v1.json`: `ffb2085c69931a6af1881ab0f16c44c0bfc19c30b4d77a740290b6faa42e6810`

## Evidence commands

```bash
uv run ruff check .
uv run mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts
npm run check
npm run test
npm run build
```

PostgreSQL migration reversal and a PostgreSQL-backed full suite are required PR
gates. The signed-in live-runtime tests remain opt-in and are not required to
prove deterministic search authority or reproduce the frozen replay benchmark.

Recorded on 2026-08-24:

- strict Ruff and mypy checks: PASS;
- P6-focused search and benchmark tests: 16 passed;
- complete backend suite without PostgreSQL: 191 passed, 12 expected database
  and live-provider tests skipped;
- complete PostgreSQL-backed backend suite: 200 passed, 3 opt-in live-provider
  tests skipped;
- Alembic PostgreSQL 16 cycle: upgrade to `0008`, downgrade to base, and upgrade
  to `0008`: PASS;
- generated OpenAPI TypeScript contract: idempotent;
- frontend ESLint/TypeScript, 19 Vitest cases, and production build: PASS;
- accessible P6 SVG assets: valid XML.

## Scope conclusion

P6 provides observable, bounded comparison among independently isolated
candidates while preserving the v0.1 and P5 capability, credential, verifier,
approval, budget, durable-state, and recovery boundaries. Replay-based experience
retrieval, learned routing, automatic policy promotion, and unbounded fan-out are
absent.
