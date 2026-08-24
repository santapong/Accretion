# P7 acceptance report

Status: implementation evidence prepared on 2026-08-24. This report closes P7;
it does not by itself authorize a v0.2 release tag or merge to `main`.

The [frontend guide](../../guides/frontend.md) provides the route-level map for the UI
evidence summarized by `V02-P7-007`.

<img src="../../assets/p7-experience-replay.svg" alt="P7 verified experience materialization, retrieval, operator selection, replay execution, and repeated compatibility revalidation" width="100%" />

## Acceptance mapping

| Criterion | Evidence |
|---|---|
| `V02-P7-001` | Contract, service, memory/PostgreSQL, migration, and API tests materialize terminal run/candidate evidence into immutable `Experience`, controlled `TrajectorySegment`, and versioned embedding records with repository, commit, manifest, policy, verifier, prompt/context/tool, and runtime provenance. |
| `V02-P7-002` | Deterministic embedding tests cover 384-dimensional NFKC/redacted signed feature hashing; retrieval persists exact cosine similarity plus environment, version, freshness, final compatibility, transfer risk, disposition, and reasons. |
| `V02-P7-003` | Compatibility tests reject retracted, cross-repository, missing/non-ancestor, architecture/policy/verifier/capability-incompatible, max-age, and protected-side-effect evidence; the frozen corpus rejects 19/20 stale/incompatible sources. |
| `V02-P7-004` | Failed and out-ranked materialization tests persist `MEDIUM` negative evidence and failure taxonomy; planning and replay tests allow it only as revalidated avoidance guidance and never as a seed. |
| `V02-P7-005` | Replay integration tests persist each seed's exact search/candidate/match/experience/segment references, controlled procedure, assumptions, required revalidations, validation state, and timestamps. |
| `V02-P7-006` | Safe-segment, redaction, candidate isolation, and launch-race tests prove replay carries no patch/transcript/tool arguments/results/credentials/native session/permission/approval/side-effect state, starts a new worktree/session, and prunes invalid seeds before allocation. |
| `V02-P7-007` | Planning and run UI tests render provenance, component compatibility, transfer risk, disposition/reasons, safe segment kinds and IDs, procedural guidance, fresh/replay treatment, and revalidation state. |
| `V02-P7-008` | The frozen 20-task, 50-source, 80-trace benchmark reports all four treatments, success, quality, turns, tools, latency, compute, uplift, false accepts, negative transfer, stale rejection, use/rejection/null rates, negative tasks, thresholds, and exact fixture hashes. |

## Frozen benchmark result

<img src="../../assets/p7-transfer-gate.svg" alt="P7 benchmark gate passing with quality uplift, reduced tool calls, 95 percent stale rejection, 3.33 percent negative transfer, and no false-accept increase" width="100%" />

| Treatment | Success | Mean quality | Uplift | Mean turns | Mean tool calls | Tool reduction | False accepts | Negative transfers |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fresh | 18 / 20 | 0.711000 | 0.000000 | 5.0 | 10.0 | 0% | 1 | 0 |
| Success only | 19 / 20 | 0.741000 | 0.030000 | 5.0 | 9.0 | 10% | 1 | 1 |
| Success + failure | 20 / 20 | 0.776000 | 0.065000 | 4.0 | 9.0 | 10% | 1 | 0 |
| Replay | 20 / 20 | 0.781500 | 0.070500 | 4.0 | 8.0 | 20% | 1 | 1 |

Gate results:

- false accepts do not increase: **PASS**;
- stale/incompatible rejection `0.95` against threshold `>= 0.95`: **PASS**;
- negative transfer `0.033333` against threshold `<= 0.05`: **PASS**;
- replay quality uplift `0.070500` against threshold `>= 0.03`: **PASS**;
- replay tool-call reduction `0.20` against threshold `>= 0.10`: **PASS**; and
- replay success rate does not regress: **PASS**.

The preserved negative results are `p7-010` under success-only guidance and
`p7-020` under replay. These are deterministic fixture properties, not current
live-provider performance claims.

Fixture SHA-256 fingerprints:

- `config.v1.json`: `42c21144b551edaaaed08d6976807e771da82b055c0455678b9b78c02531be9c`
- `tasks.v1.json`: `4913e7d6d7fc5c676a009ecee328f9e13d225d02b67fdc846ada5caefa3917ff`
- `sources.v1.json`: `968898ea94cb9d1633680ab9a80c4ca92e3b975d5c629069458d851467b713b3`
- `replay-traces.v1.json`: `38f1c0b5a1832b8472c63d87ad20a825fd83bca05d3a306d4c087372889ed7a9`

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

Recorded on 2026-08-24 against the final P7 tree:

- Ruff and mypy: **PASS**;
- memory-backed backend suite: **202 passed, 13 skipped** (the skips are the
  PostgreSQL and explicitly opt-in live-runtime cases);
- PostgreSQL 16 + pgvector migration cycle, `upgrade head` → `downgrade base` →
  `upgrade head`, through `0009_p7_experience_contracts`: **PASS**;
- complete PostgreSQL-backed backend suite: **212 passed, 3 skipped** (all three
  are explicitly opt-in signed-in live-runtime cases);
- generated OpenAPI client, ESLint, and TypeScript: **PASS**;
- operator UI: **21 passed**; and
- production UI build: **PASS** (with the existing non-blocking bundle-size
  advisory).

The database evidence used a disposable local PostgreSQL fixture and retained no
test data. Signed-in live-runtime tests remain opt-in and are not needed to prove
P7's deterministic authority boundary or reproduce the frozen replay report.

## Scope conclusion

P7 provides explicit, explainable, repository-scoped procedural reuse while
preserving every v0.1, P5, and P6 authority, verifier, isolation, budget,
promotion, and recovery invariant. It does not implement automatic capture or
selection, cross-repository transfer, trust mutation, policy/skill promotion,
reinforcement learning, raw transcript retrieval, or release automation.
