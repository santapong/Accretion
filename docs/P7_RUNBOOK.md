# Accretion P7 verified-experience runbook

<img src="assets/p7-experience-replay.svg" alt="P7 lifecycle from explicit terminal evidence materialization through repository-scoped retrieval, compatibility scoring, operator-frozen context, fresh-control replay search, and repeated fail-closed revalidation" width="100%" />

## Scope

P7 lets verified local evidence inform a new candidate without becoming policy or
authority. Operators explicitly materialize terminal runs or candidates, retrieve
within the same repository, inspect compatibility, freeze at most three accepted
matches into `ContextBundle` v2, and attach `REPLAY_BRANCH` to an accepted P5
agent node. Candidate 1 is always a fresh control. Each positive experience adds
one isolated replay candidate; negative experience supplies avoidance guidance
only.

P7 never reuses a patch, transcript, runtime/native session, credentials,
capability arguments or results, permissions, approvals, verifier acceptance,
side-effect identifiers, or mutable workspace state.

The [frontend guide](FRONTEND_GUIDE.md) maps retrieval, frozen selection,
materialization, fresh/replay treatment labels, experience lineage, repeated
revalidation, and the benchmark gate to their implemented operator routes.

## Enable P7

All three deployment flags default off and must be enabled together:

```dotenv
ACCRETION_ENABLE_DYNAMIC_WORKFLOWS=true
ACCRETION_ENABLE_CANDIDATE_SEARCH=true
ACCRETION_ENABLE_EXPERIENCE_RETRIEVAL=true
```

Then opt in one project at its current feature revision:

```bash
curl -X PATCH http://localhost:8000/api/v2/projects/PROJECT_ID/features \
  -H 'Content-Type: application/json' \
  -d '{
    "dynamic_workflows":true,
    "candidate_search":true,
    "experience_retrieval":true,
    "expected_revision":1
  }'
```

The operator UI performs this feature update when **Retrieve matches** or
**Materialize run experience** is chosen. Project opt-in cannot override a
disabled deployment flag.

## Materialize terminal evidence

Open a completed run and choose **Materialize run experience**, or call:

```bash
curl -X POST http://localhost:8000/api/v2/runs/RUN_ID/experiences \
  -H 'Content-Type: application/json' \
  -d '{}'
```

For a terminal P6 candidate, include its identifier:

```bash
curl -X POST http://localhost:8000/api/v2/runs/RUN_ID/experiences \
  -H 'Content-Type: application/json' \
  -d '{"candidate_id":"CANDIDATE_ID"}'
```

A successful source becomes `HIGH`-trust positive evidence only when its policy,
required verifier results, artifacts, approvals, and terminal state are complete.
A complete failed, requires-human, or out-ranked source becomes `MEDIUM`-trust
negative knowledge with a failure taxonomy. Cancelled or incomplete evidence is
rejected. Repeating the same materialization is idempotent; conflicting terminal
evidence fails closed.

## Retrieve, inspect, and freeze

Retrieval must happen after deterministic profiling and before a P5 proposal.
In Planning Review:

1. Choose **Retrieve matches**.
2. Inspect rank, polarity, trust, semantic/environment/version/freshness scores,
   final compatibility, transfer risk, reasons, source run/commit/runtime, and
   procedural segment kinds.
3. Select one to three `ACCEPTED` results. Positive results may seed replay;
   negative results can only provide avoidance guidance.
4. Choose **Freeze selected**. This creates an immutable `ContextBundle` v2
   revision. Query and selection freeze after a workflow proposal exists.

Equivalent API calls:

```bash
curl -X POST http://localhost:8000/api/v2/experiences/query \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"TASK_ID","include_failures":true,"top_k":5}'

curl -X POST http://localhost:8000/api/v2/tasks/TASK_ID/experience-selections \
  -H 'Content-Type: application/json' \
  -d '{
    "query_id":"QUERY_ID",
    "match_ids":["POSITIVE_MATCH_ID","NEGATIVE_MATCH_ID"],
    "expected_context_bundle_id":"CONTEXT_BUNDLE_ID"
  }'
```

Use `GET /api/v2/tasks/{task_id}/experience-matches` to reopen the frozen
selection and `GET /api/v2/experiences/{experience_id}` for source provenance and
safe segment detail.

## Attach and execute replay

After selection, propose and validate a P5 graph. In **Attach bounded P6 search**,
choose **Fresh + verified replay**. The UI derives positive seeds and negative
guidance only from the frozen context. The API form is:

```bash
curl -X POST http://localhost:8000/api/v2/runs/RUN_ID/search \
  -H 'Content-Type: application/json' \
  -d '{
    "parent_node_id":"act",
    "mode":"REPLAY_BRANCH",
    "branch_count":2,
    "max_parallel":1,
    "per_branch_budget":{
      "schema_version":"2.0",
      "wall_time_seconds":120,
      "max_turns":4,
      "max_tool_calls":12
    },
    "total_budget":{
      "schema_version":"2.0",
      "wall_time_seconds":240,
      "max_turns":8,
      "max_tool_calls":24
    },
    "candidate_directives":[],
    "replay_seed_match_ids":["POSITIVE_MATCH_ID"],
    "negative_guidance_match_ids":["NEGATIVE_MATCH_ID"]
  }'
```

`branch_count` must equal one plus the number of positive seeds. Candidate 1 is
fresh and receives neither positive nor negative experience guidance. Replay
candidates get one frozen `TrajectorySeed` each, a new worktree, and a new
runtime session. Negative guidance is revalidated and omitted if it becomes
invalid; it can never create a candidate.

## Revalidation, invalidation, and recovery

Positive compatibility is rebuilt from current repository, commit ancestry,
manifests, architecture, policy, verifiers, prompt/context/tool shape, skills,
plugins, capabilities, provider, and freshness at planning, launch, selection,
promotion, and promotion recovery.

- Invalid evidence records `TRAJECTORY_REPLAY_REJECTED`, marks the replay
  candidate `PRUNED`, and preserves its reasons and frozen seed.
- A launch rejection happens before a worktree or runtime session is allocated.
- The fresh control continues and Accretion does not substitute another seed.
- Interrupted candidates remain `INTERRUPTED`, retain conservative budget spend,
  and are never automatically rerun.
- A replay winner that becomes invalid before promotion or recovery cannot alter
  the parent workspace and requires human review.
- Retract bad evidence with
  `POST /api/v2/experiences/{experience_id}/retract`; moderation increments its
  immutable revision and all later revalidation rejects it.

Inspect `GET /api/v2/search/{search_id}/replay-seeds` and the run's **Experience
replay lineage** panel for source provenance, compatibility, transfer risk,
reused segment IDs, controlled guidance, assumptions, and revalidation state.

## Frozen benchmark gate

<img src="assets/p7-transfer-gate.svg" alt="P7 frozen benchmark showing quality uplift, tool-call reduction, stale rejection, negative transfer, false-accept safety, and the passing preregistered gate" width="100%" />

Open **P7 Experience** in the operator UI, or run:

```bash
curl http://localhost:8000/api/v2/benchmarks/experience
curl -X POST http://localhost:8000/api/v2/benchmarks/experience/run \
  -H 'Content-Type: application/json' \
  -d '{"execution_source":"REPLAY"}'
```

The frozen suite contains 20 tasks, 50 source records, and 80 traces across
fresh, success-only, success-plus-failure, and replay treatments. It preserves
negative results and binds every report to task, source, trace, and configuration
hashes. `LIVE` is rejected by this endpoint; live provider calibration is a
separate explicit local gate.

## Verification commands

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

For PostgreSQL evidence, run the full migration upgrade/downgrade/upgrade cycle
through `0009_p7_experience_contracts` on disposable PostgreSQL 16 with pgvector,
then run the complete suite with `ACCRETION_TEST_POSTGRES_URL` set.
