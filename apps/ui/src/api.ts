import type {
  ApprovalDecisionValue,
  ApprovalRecord,
  Capability,
  ExecutionTrace,
  Project,
  ProjectCreate,
  GraphProjection,
  LoopExecution,
  Run,
  RunAudit,
  RunCreate,
  RuntimeHealth,
  SessionRef,
  MetaPlugin,
  MetaSkill,
  StrategyOverrideCreate,
  StrategyOverrideResult,
  Task,
  TaskCreate,
  TaskPlanning,
  VerificationResult,
  WorkflowTemplateSummary,
} from "./types";

const API_ROOT = import.meta.env.VITE_API_URL ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { message?: string } | null;
    throw new Error(body?.message ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  runtimes: () => getJson<RuntimeHealth[]>("/api/v1/runtimes"),
  runtimeSessions: (runtimeId: string) =>
    getJson<SessionRef[]>(`/api/v1/runtimes/${runtimeId}/sessions`),
  runs: () => getJson<Run[]>("/api/v1/runs?limit=50"),
  run: (runId: string) => getJson<Run>(`/api/v1/runs/${runId}`),
  audit: (runId: string) => getJson<RunAudit>(`/api/v1/runs/${runId}/audit`),
  projects: () => getJson<Project[]>("/api/v1/projects"),
  createProject: (payload: ProjectCreate) => postJson<Project>("/api/v1/projects", payload),
  createTask: (payload: TaskCreate) => postJson<Task>("/api/v1/tasks", payload),
  planning: (taskId: string) => getJson<TaskPlanning>(`/api/v1/tasks/${taskId}/planning`),
  overrideStrategy: (taskId: string, payload: StrategyOverrideCreate) =>
    postJson<StrategyOverrideResult>(`/api/v1/tasks/${taskId}/strategy/override`, payload),
  startRun: (taskId: string, payload: RunCreate) =>
    postJson<Run>(`/api/v1/tasks/${taskId}/runs`, payload),
  loop: (runId: string) => getJson<LoopExecution>(`/api/v1/runs/${runId}/loop`),
  graph: (runId: string) => getJson<GraphProjection>(`/api/v1/runs/${runId}/graph`),
  trace: (runId: string) => getJson<ExecutionTrace>(`/api/v1/runs/${runId}/trace`),
  templates: () => getJson<WorkflowTemplateSummary[]>("/api/v1/templates"),
  approvals: (runId?: string, status?: string) =>
    getJson<ApprovalRecord[]>(
      `/api/v1/approvals?${runId ? `run_id=${runId}&` : ""}${status ? `status=${status}` : ""}`,
    ),
  decideApproval: (approvalId: string, decision: ApprovalDecisionValue) =>
    postJson<ApprovalRecord>(`/api/v1/approvals/${approvalId}/decision`, { decision }),
  verifications: (runId: string) =>
    getJson<VerificationResult[]>(`/api/v1/runs/${runId}/verifications`),
  verification: (verificationId: string) =>
    getJson<VerificationResult>(`/api/v1/verifications/${verificationId}`),
  capabilities: () => getJson<Capability[]>("/api/v1/capabilities"),
  skills: () => getJson<MetaSkill[]>("/api/v1/skills"),
  plugins: () => getJson<MetaPlugin[]>("/api/v1/plugins"),
  eventUrl: (runId: string, after = 0) =>
    `${API_ROOT}/api/v1/runs/${runId}/events?after=${after}`,
};
