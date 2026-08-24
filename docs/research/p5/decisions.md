# P5 design decisions

These decisions close the P5-blocking questions in the v0.2 SDD. They apply to
P5 only and do not pre-authorize P6 or P7.

| Decision | P5 ruling | Reason |
|---|---|---|
| Graph DSL | Restricted typed JSON AST; executable P5 conditions compare `node.outcome` with a known outcome. | No arbitrary code and deterministic replay. |
| Planner | Reviewed fragment composition with deterministic selection; `AUTO` resolves to deterministic. Live Claude/Codex topology glue is rejected until separately evidenced. | Honest provenance and repeatable proposals. |
| Bounds | 32 nodes, 64 edges, depth 8, fan-out 4, traversal 3. | Conservative first dynamic release. |
| Repair | One proposal repair, then validated static fallback or human. | Prevent unbounded “ask until accepted” behavior. |
| Parallelism | P5 serializes the dual-analysis fragment. `FANOUT`/`MERGE` execution is deferred to P6. | The current scheduler owns one mutable worktree. |
| Replan trigger | Operator/evidence/budget/runtime/node-failure reasons only; activation requires a paused safe boundary. | Variety is not a valid trigger. |
| Protected history | Completed/failed/cancelled node definitions are immutable across replan; node and side-effect references are carried into the new revision. | Past effects remain facts. |
| Runtime evidence | Version-keyed, interpretable scoring records; no learned routing. | Prevent silent aggregation across incompatible runtime versions. |
| Feature rollout | Global flag plus per-project optimistic setting, both default off. | P5 is additive and does not change v0.1 behavior by default. |

The deterministic `GraphValidator`, not the planner or UI, is the final graph
authority. Accepted semantic graphs compile to the existing P3 executor rather
than creating a second execution/security path.
