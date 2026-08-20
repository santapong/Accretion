import type { components } from "./api/schema";

export type Provider = components["schemas"]["Provider"];
export type RuntimeHealth = components["schemas"]["RuntimeHealth"];
export type Run = components["schemas"]["Run"];

export interface AgentEvent {
  event_id: string;
  sequence: number;
  provider: Provider;
  normalized_type: string;
  native_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}
