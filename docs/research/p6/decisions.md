# P6 bounded-search decisions

Status: implemented and frozen for the opt-in P6 candidate executor.

These decisions constrain P6 only. They preserve the
[frozen v0.1 baseline](../../releases/v0.1/baseline.md), inherit the P5 graph authority
boundary, and do not authorize P7 experience retrieval.

| Area | Decision | Safety consequence |
|---|---|---|
| Rollout | Candidate search requires global and per-project opt-in; both default off. | P5 and v0.1 behavior remain unchanged by default. |
| Attachment | A plan attaches to one pending `AGENT` node in an accepted P5 graph revision. | Search cannot replace completed history or protected capability nodes. |
| Branch ceiling | Support 1–4 branches and cap parallelism by task, project, provider, and global limits. | N=1/2/4 is benchmarkable without unbounded fan-out. |
| Shared budget | Persist and reserve wall-time, turn, and tool-call allowances before execution; charge actual spend to the parent run. | Concurrent branches cannot independently observe and overspend the same remainder. |
| Cancellation | Persist cancellation before interrupting active calls; never promote a candidate after cancellation. | Restart cannot mistake cancelled speculation for accepted work. |
| Speculative authority | Candidates may mutate isolated local workspaces but cannot execute protected external side effects or expand permissions. | Search multiplies computation, not authority. |
| Ranking | Only independently verified candidates are eligible. A unique highest rounded score may win; ties or inconclusive evidence require human review. | Provider order and completion timing cannot silently break ties. |
| Promotion | Persist intent and parent-before digest, apply only the selected patch after policy re-evaluation, then persist the parent-after digest. | Promotion is idempotent and crash-reconcilable. |
| Recovery | Interrupted candidates retain evidence and conservatively consume their reserved budget; they are not automatically rerun. | Recovery cannot duplicate unknown compute or hide candidate loss. |
| Replay | `REPLAY_BRANCH` is reserved in the contract but execution returns `REPLAY_BRANCH_REQUIRES_P7`. | Unverified trajectory references cannot bypass P7 compatibility checks. |
| Calibration | Deterministic replay is the required CI gate; signed-in Claude/Codex calibration remains separate and opt-in. | CI stays reproducible while live evidence remains attributable. |

See the [P6 runbook](../../runbooks/p6-candidate-search.md) for operations and the
[P6 acceptance report](acceptance.md) for criterion-level evidence,
fixture hashes, and the frozen N=1/2/4 result.
