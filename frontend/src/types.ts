export type ProviderName = "codex" | "claude";
export type SessionStatus =
  | "running"
  | "waiting_approval"
  | "completed"
  | "interrupted"
  | "failed"
  | "offline";

export type ApprovalStatus = "pending" | "approved" | "denied" | "cancelled" | "expired";
export type ApprovalDecision = "approve" | "approve_session" | "deny" | "cancel";

export interface ProviderCapabilities {
  history: boolean;
  start: boolean;
  resume: boolean;
  steer: boolean;
  interrupt: boolean;
  approvals: boolean;
}

export interface ProviderHealth {
  name: ProviderName;
  available: boolean;
  authenticated: boolean | null;
  version: string | null;
  detail: string | null;
  capabilities: ProviderCapabilities;
}

export interface Session {
  id: string;
  provider: ProviderName;
  provider_session_id: string | null;
  title: string;
  cwd: string;
  status: SessionStatus;
  managed: boolean;
  created_at: string;
  updated_at: string;
  last_error: string | null;
}

export interface TimelineEvent {
  id: number | null;
  session_id: string;
  kind: string;
  payload: Record<string, unknown>;
  provider_event_id: string | null;
  created_at: string;
}

export interface Approval {
  id: string;
  session_id: string;
  provider_request_id: string;
  kind: string;
  payload: Record<string, unknown>;
  status: ApprovalStatus;
  decision: ApprovalDecision | null;
  created_at: string;
  resolved_at: string | null;
}

export interface SessionDetail extends Session {
  events: TimelineEvent[];
  approvals: Approval[];
}

export interface PublicConfig {
  workspace_roots: string[];
  history_storage: string;
}

export interface EventEnvelope {
  sequence: number;
  type: string;
  data: Record<string, unknown>;
}
