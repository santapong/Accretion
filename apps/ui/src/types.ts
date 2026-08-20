import type { components } from "./api/schema";

export type Provider = components["schemas"]["Provider"];
export type RuntimeHealth = components["schemas"]["RuntimeHealth"];
export type Run = components["schemas"]["Run"];
export type Project = components["schemas"]["Project"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];
export type Task = components["schemas"]["Task"];
export type TaskCreate = components["schemas"]["TaskCreate"];
export type TaskPlanning = components["schemas"]["TaskPlanning"];
export type StrategyOverrideCreate = components["schemas"]["StrategyOverrideCreate"];
export type StrategyOverrideResult = components["schemas"]["StrategyOverrideResult"];
export type RunCreate = components["schemas"]["RunCreate"];

export interface AgentEvent {
  event_id: string;
  sequence: number;
  provider: Provider;
  normalized_type: string;
  native_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}
