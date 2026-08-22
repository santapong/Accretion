import type {
  Approval,
  ApprovalDecision,
  ProviderHealth,
  ProviderName,
  PublicConfig,
  Session,
  SessionDetail,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  config: () => request<PublicConfig>("/api/v1/config"),
  providers: () => request<ProviderHealth[]>("/api/v1/providers"),
  sessions: () => request<Session[]>("/api/v1/sessions?limit=500"),
  session: (id: string) => request<SessionDetail>(`/api/v1/sessions/${id}`),
  start: (body: { provider: ProviderName; cwd: string; prompt: string; title?: string }) =>
    request<Session>("/api/v1/sessions", { method: "POST", body: JSON.stringify(body) }),
  message: (id: string, prompt: string) =>
    request<Session>(`/api/v1/sessions/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  resume: (id: string, prompt?: string) =>
    request<Session>(`/api/v1/sessions/${id}/resume`, {
      method: "POST",
      body: JSON.stringify({ prompt: prompt || null }),
    }),
  interrupt: (id: string) =>
    request<Session>(`/api/v1/sessions/${id}/interrupt`, { method: "POST" }),
  decide: (id: string, decision: ApprovalDecision) =>
    request<Approval>(`/api/v1/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  remove: (id: string) =>
    request<void>(`/api/v1/sessions/${id}`, { method: "DELETE" }),
  clear: () => request<{ deleted: number }>("/api/v1/history", { method: "DELETE" }),
  importHistory: () =>
    request<{ imported: number }>("/api/v1/history/import", { method: "POST" }),
};
