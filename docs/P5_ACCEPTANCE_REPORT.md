# P5 acceptance report

Status: implementation evidence prepared on 2026-08-23. P6 and P7 are not part
of this report.

<img src="assets/p5-dynamic-workflow.svg" alt="P5 validated workflow proposal, activation, fallback, and revision lifecycle" width="100%" />

## Acceptance mapping

| Criterion | Evidence |
|---|---|
| `V02-P5-001` | `FragmentWorkflowPlanner` emits schema-valid, versioned proposals from four reviewed fragments; repeatability and API tests cover typed nodes, edges, verifier and capability declarations. |
| `V02-P5-002` | Adversarial validator and service fallback tests reject an unknown capability before graph installation. |
| `V02-P5-003` | Model validation requires traversal bounds; deterministic validation rejects unbounded cycles, unsupported P5 loop-back, fan-out/merge execution, excessive depth, and concurrency excess. |
| `V02-P5-004` | Path analysis rejects verifier and high-risk approval bypasses. Materialization adds explicit fail/inconclusive/denial terminal routes. |
| `V02-P5-005` | Task allowed/denied capability ceilings and maximum risk are frozen into `PolicySnapshot`; privilege/risk expansion tests reject. |
| `V02-P5-006` | One bounded repair is permitted; rejection then cancels the inert dynamic run and starts the existing validated static strategy. Replan rejection remains paused for a human. |
| `V02-P5-007` | Proposals, validations, and contiguous graph revisions are immutable; revision carries proposal ID, planner/validator evidence through linked records, normalized hash, and activation time. |
| `V02-P5-008` | Mid-run safe-replan test pauses execution and creates revision 2 with parent revision 1; revision 1 remains byte-for-byte unchanged. |
| `V02-P5-009` | Replan reconciliation rejects removal/rewrite of completed nodes and persists protected node/side-effect references; checkpoint tests exercise cursor preservation. |
| `V02-P5-010` | Runtime decision records contain candidates, runtime versions, scores/features, selected runtime/reason, policy version, and fallback order; API and UI expose them. |

## Evidence commands

The authoritative final counts are recorded by the branch/PR checks. Run:

```bash
uv run ruff check .
uv run mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
npm run api:generate
git diff --exit-code -- openapi.json apps/ui/src/api/schema.d.ts
npm run check
npm run test
npm run build
```

PostgreSQL migration reversal and PostgreSQL-backed tests remain a required PR
gate when a disposable database is available. Live runtime tests remain opt-in
and are not required to prove deterministic P5 graph authority.

Recorded on 2026-08-23:

- strict Ruff and mypy checks: PASS;
- P5-focused backend tests: 24 passed;
- complete PostgreSQL-backed backend suite: 183 passed, 3 opt-in live-provider
  tests skipped;
- Alembic PostgreSQL 16 cycle: upgrade to `0007`, downgrade to base, and
  upgrade to `0007`: PASS;
- frontend ESLint/TypeScript and 16 Vitest cases: PASS.

## Scope conclusion

P5 changes workflow structure under deterministic validation while retaining
all v0.1 permission, verifier, approval, credential, budget, durable-state, and
recovery boundaries. Candidate search, experience retrieval, learned routing,
and self-modifying policy are absent.
