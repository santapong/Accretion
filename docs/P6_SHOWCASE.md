# P6 developer showcase: compare two approaches safely

This example adds bounded candidate comparison to an accepted P5 code-review
node. It is intended for a disposable local repository and the deterministic
fake runtime; it does not consume a signed-in provider session.

<img src="assets/p6-search-lifecycle.svg" alt="P6 developer flow from an accepted P5 node to two isolated candidates, independent verification, unique selection, and policy-checked promotion" width="100%" />

## Scenario

Create a `REVIEW` task for a small patch, propose the P5 `single-act-verify`
workflow, and attach `BEST_OF_N` with two candidates to its pending agent node.
Each candidate receives an isolated workspace and the same frozen task, policy,
and verifier contract. A failure in candidate 1 cannot alter candidate 2 or the
parent run.

Use the Planning Review UI for the shortest walkthrough:

1. Start the local stack from `develop` with both P5 and P6 deployment flags on.
2. Register a disposable Git repository and create a bounded `REVIEW` task.
3. Select **Propose P5 graph**, wait for `ACCEPT`, and inspect the inert proposal.
4. In **Attach bounded P6 search**, choose **Best of N**, two branches, and a
   shared budget no larger than the task budget.
5. Attach the plan, then activate the graph.
6. Open the run and expand **Candidate search tree** to compare provenance,
   spend, independent score evidence, terminal reasons, and the selected branch.

The exact API plan body and inspection endpoints are in the
[P6 runbook](P6_RUNBOOK.md). Start with `FAKE`; live provider execution is a
separate, explicit opt-in.

## Read the result

The useful question is not just “which candidate won?” Verify all four layers:

| Layer | What to inspect |
|---|---|
| Isolation | Different workspace lease, session, and trajectory references |
| Provenance | Provider, runtime ID, model, version, reviewer, and ordinal |
| Evidence | Verifier status, eligibility, quality, cost/latency proxies, and explanation |
| Authority | Shared spend, stop reason, promotion intent, policy re-evaluation, and parent digests |

A tie or uncertain verifier result is a successful safety outcome: the search
stops for a human and preserves both candidates instead of inventing a winner.

## Reproduce the research view

<img src="assets/p6-quality-compute.svg" alt="P6 frozen replay result showing quality and verified acceptance at candidate counts one, two, and four" width="100%" />

Open `http://localhost:5173/benchmarks/search` and choose **Run frozen replay**,
or call:

```bash
curl -X POST http://localhost:8000/api/v2/benchmarks/search/run \
  -H 'Content-Type: application/json' \
  -d '{"execution_source":"REPLAY"}'
```

The response binds the N=1/2/4 curve, provider comparison, null task, and fixture
hashes into one deterministic report. Re-running it produces the same report ID
and metrics. Use this showcase to understand the evidence path; do not generalize
the fixture values into a live-provider performance claim.

Continue with the [P6 acceptance report](P6_ACCEPTANCE_REPORT.md) for the exact
criteria and the [P6 decision record](P6_DECISIONS.md) for frozen safety choices.
