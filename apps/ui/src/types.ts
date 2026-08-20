import type { components } from "./api/schema";

export type Provider = components["schemas"]["Provider"];
export type RuntimeHealth = components["schemas"]["RuntimeHealth"];
export type Run = components["schemas"]["Run"];
export type Project = components["schemas"]["Project"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];
export type Task = components["schemas"]["Task"];
export type TaskBudgets = components["schemas"]["TaskBudgets"];
export type TaskCreate = components["schemas"]["TaskCreate"];
export type TaskPlanning = components["schemas"]["TaskPlanning"];
export type StrategyOverrideCreate = components["schemas"]["StrategyOverrideCreate"];
export type StrategyOverrideResult = components["schemas"]["StrategyOverrideResult"];
export type RunCreate = components["schemas"]["RunCreate"];

export type GraphNodeKind = components["schemas"]["GraphNodeKind"];
export type GraphNodeStatus = components["schemas"]["GraphNodeStatus"];
export type GraphEdgeKind = components["schemas"]["GraphEdgeKind"];
export type VerificationStatus = components["schemas"]["VerificationStatus"];
export type GraphProjectionNode = components["schemas"]["GraphProjectionNode"];
export type GraphProjectionEdge = components["schemas"]["GraphProjectionEdge"];
export type GraphProjection = components["schemas"]["GraphProjection"];
export type LoopSpec = components["schemas"]["LoopSpec"];
export type LoopBudgetRemaining = components["schemas"]["LoopBudgetRemaining"];
export type LoopState = components["schemas"]["LoopState"];
export type LoopExecution = components["schemas"]["LoopExecution"];
export type Finding = components["schemas"]["Finding"];
export type VerificationResult = components["schemas"]["VerificationResult"];

export interface AgentEvent {
  event_id: string;
  sequence: number;
  provider: Provider;
  normalized_type: string;
  native_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}
