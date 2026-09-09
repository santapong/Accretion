# M4 ownership and normal termination boundary

This is a bounded construction slice of the
[approved completion plan](completion-plan-2026-09-09.md), implementing the
ownership and termination direction in the [M0 API contract](m0-api-contract.md).
It does not complete M4 or establish a simulator, conformance or AC5 result.

## Generic Run ownership

The Run manager resolves the persisted `SimulationRunBinding` before its
start-from-task, pause, resume, cancel, dynamic activation/replan, recovery and
coding-runtime entry points. A simulation-owned run returns
`EPISODE_STATE_CONFLICT`; generic reconciliation leaves it for the episode
service. The decision follows the original binding, not the provider label.
The run and its task are inserted atomically by the authority service; that
service cannot attach ownership to an already-existing generic run.
The guard reads the resource before looking up its ownership, then checks the
binding before using it. A new atomic binding published during the resource
read is therefore observed; an earlier negative binding lookup is never reused.

`RuntimeStore.lookup_run_owner(run_id, actor_id=...)` checks current persisted
workspace/project membership before returning ownership or corruption details.
`lookup_task_owner` applies the same boundary when starting another run from an
episode-owned task. Omission of `actor_id` is for trusted internal dispatch only.
The API authenticates the current actor before these lookups. Authorized run
control conflicts name the episode detail path; foreign resources return a
scoped 404 without episode metadata. Generic approval decisions also consult
ownership and cannot stand in for a simulation episode approval.

Missing or invalid original binding evidence fails closed. The generic startup
reconciler can therefore stop on corrupted ownership evidence; corruption is
never permission to resume a simulator through a coding-agent CLI. Ordinary
unbound runs retain the established manager and API paths.

## Normal termination

The supervised host uses the existing `reserve_dispatch` with an exact protocol
`TERMINATE` request and current `ExecutionPins`. Admission requires a running,
reset-completed episode, no pending dispatch, the current fenced lease and
consumed approval, plus current configured policy/host/conformance checks.
The reservation advances the durable request sequence without spending an
action budget or changing physics state. Identical idempotent retries return
historical admission with `fresh=False`.

After receiving the matching successful acknowledgement, the host calls:

```python
await authority.finish_termination(
    scope,  # expected_revision is the current RuntimeEpisode revision
    episode_id=episode_id,
    reservation_id=reservation_id,
    response=response,
)
```

The transaction rechecks the pending reservation, exact request/response
correlation, current ownership, lease, approval and preflight. It records a
completed dispatch and `simulation_episode.terminated`, retaining canonical
request and acknowledgement JSON plus both digests for the recorder. Only then
does the episode enter `VERIFYING`. Wall deadlines are checked again after all
awaited collaborators and event writes; refusal rolls back the transition and
its evidence together. Concurrent identical acknowledgements record one event.

The `Run` is not marked successful. Independent task, safety and completeness
verification, evidence sealing, replay and final acceptance remain separate
work. A normal termination acknowledgement is not a producer PASS or evidence
that the requested task succeeded.

`authorize_read(TERMINATE)` remains a cleanup-only permission and creates no
normal-termination reservation. Expired/revoked authority, a pending uncertain
action, an error acknowledgement or a mismatched acknowledgement cannot enter
`VERIFYING`. A committed fence wins over a late acknowledgement. Releasing an
already-verifying lease preserves that state; release alone cannot establish
it. Exact completed-ack retries remain historical receipts after cleanup.

## Validation scope

`tests/test_v05_lifecycle.py` uses synthetic authority fixtures on Memory and
PostgreSQL. It covers normal termination, zero budget spend, duplicate calls,
bad acknowledgements, release/uncertainty/approval fences, expiry after the final
event write, rollback, generic controls/workers/replanning and scoped HTTP
refusals. The existing authority, migration, Run/API and dynamic-workflow tests
check compatibility. No simulator, paid provider, physical device, producer
approval shortcut or independent acceptance claim is part of these witnesses.

The host must still correlate its supervised in-flight request, preserve raw
transport evidence and enforce deadlines before live invocation. A database
commit cannot make subsequent IPC delivery instantaneous.
