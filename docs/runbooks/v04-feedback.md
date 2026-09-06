# v0.4 feedback, recovery and experience

The M3 pipeline turns a finished node into evidence: an independent §7.9 verdict, a typed §7.11
failure, a §9.7 recovery decision and — once the run has been graded — a §7.10 experience record
a future router may retrieve. It is attached to the scheduler in the API process and in the MCP
gateway alike, and it does nothing at all unless node routing is enabled: every hook in
`RunManager` requires both a routing service and a feedback pipeline, so a deployment that has
not turned routing on executes exactly as it did before v0.4.

## What runs when

| Moment | What happens | Where it lands |
|---|---|---|
| a routed AGENT node is dispatched | the producer's execution instance and session are remembered | scheduler state only |
| a routed VERIFIER node finishes | one `IndependentVerificationResult` per verifier, against the **producer's** execution instance | `verification_results`; `VERIFICATION_RESULT_RECORDED` event |
| a node fails, or a dispatch raises | the failure is typed and the §9.7 guard is asked | `failure_events` |
| §9.7 says reselect | the node is re-frozen under the next attempt with the failed configuration excluded | a new node contract, a new receipt |
| the run reaches a terminal state | one experience record per routed node | `experience_records`; `EXPERIENCE_CREATED` event |

## A paused run and a material conflict

When two independent verifiers disagree about a REQUIRED claim — one PASS, one FAIL — the second
record carries a `conflict_ref` to the first and is `INCONCLUSIVE` rather than `FAIL`: what is
known is that the evidence contradicts itself. The node is left `WAITING`, the run is `PAUSED`,
and resuming it pauses it again, because the block is the state of the evidence and not a flag
the first pause consumed.

To clear it, an operator adjudicates and then resumes:

```python
await manager.resolve_verification_contradiction(
    run_id, execution_instance_id, resolution="why this side is right"
)
await manager.resume(run_id)
```

The resolution is a control event (`accretion/verification-contradiction-resolved`) naming the
execution instance and the records it settles. Nothing is deleted or rewritten: both verdicts
stay in `list_verification_results` afterwards, which is what makes the decision auditable.

An empty resolution is refused, and so is a resolution for an instance with no conflicted record
— an event claiming a decision nobody made is worse than no event.

## Recovery, and what it may not do

The taxonomy assigns an owner and the guard assigns an authority scope; the scheduler acts on
exactly one of them. A `CONFIGURATION` failure whose decision allows a retry and names a next
configuration re-enters the node: the attempt's frozen contract, receipt and claimed
configuration are dropped, the attempt number is incremented and a new execution instance is
frozen. Nothing is ever re-dispatched — the failed attempt's claimed decision stays claimed and
stays in the trace — and the failed configuration signature is passed to the next routing call,
where it is refused during candidate construction as `ATTEMPTED_WITHOUT_NEW_EVIDENCE`.

Every other decision leaves the node's outcome alone. A `STRUCTURAL` failure — findings against
required output claims — belongs to the planner, so it takes the template's own repair or replan
edge and, when none is eligible, the existing `REQUIRES_HUMAN` terminal. The router never
replans, never widens a capability set and never changes the policy snapshot.

Two independent stops bound recovery: the node contract's `resource_cap.maximum_attempts`
(`min(task.budgets.max_loop_iterations, template.max_node_retries + 1)` — note that
`max_loop_iterations` defaults to **one**, so with default budgets no automatic retry is
authorised at all) and §9.7's EVI gate. Under `BASELINE_ONLY` the only selectable configuration
is the audited baseline, so a workspace that has not enabled learned routing will usually see one
refusal and then an escalation rather than a second attempt. That is the intended fail-closed
behaviour.

## Retrieval, and why a record may be invisible

`StoreEvidenceRetriever` hands the router the heads of every projection line that: carries this
node's §7.10 contract signature; is `eligible_for_learning`; has `contradiction_status = NONE`;
is visible at the scope being asked from (a `PROJECT` record is invisible to a workspace-wide
retrieval); projects an experience that has not been retracted; and was sealed before the instant
being decided at. A principal with no membership in the workspace retrieves nothing.

If a receipt cites no `experience_refs` on a project that clearly has history, check those six in
order. The retrieval key is the *whole* `ContractSignature`, so a changed objective contract, a
changed verification spec, a changed risk class, a changed node kind or a changed capability set
each make previous outcomes a different subject — correctly, and silently.

## §11.2 endpoints

All five use the request's authenticated principal, and a record in another workspace answers
`404` rather than `403`: "you may not read this" and "this does not exist" leak the same fact.
Every route answers `409 FEEDBACK_PIPELINE_UNAVAILABLE` when the deployment wired no pipeline.

- `POST /api/v1/node-executions/{execution_instance_id}/verification-results` ingests one
  out-of-band verdict. It is idempotent by the v0.1 `verification_result_id`: a retried
  submission returns the record the first one made rather than a conflict. The run must name an
  acceptance policy — a verdict recorded against an invented policy would cite a verifier
  contract nobody froze.
- `POST /api/v1/runs/{run_id}/final-verification` projects the run's experience records under
  the caller's authority and the stated `status`, `source` and `visibility`. ADR-048: only after
  the run has been graded.
- `GET /api/v1/experiences/search` filters by workspace, project, any part of the contract
  signature and `eligible_only`. An unstated filter constrains nothing.
- `GET /api/v1/experiences/{experience_record_id}` reads one projection.
- `POST /api/v1/experiences/{experience_record_id}/resolve-contradiction` appends the `RESOLVED`
  revision that adjudicates an open contradiction on a projection, and answers `409
  CONTRADICTION_NOT_OPEN` for one that is already settled. This is the *experience* record's
  adjudication; a verification conflict inside a running run is adjudicated by
  `resolve_verification_contradiction` above.
