import type { Run, RuntimeHealth } from "./types";

const API_ROOT = import.meta.env.VITE_API_URL ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export const api = {
  runtimes: () => getJson<RuntimeHealth[]>("/api/v1/runtimes"),
  runs: () => getJson<Run[]>("/api/v1/runs?limit=50"),
  eventUrl: (runId: string) => `${API_ROOT}/api/v1/runs/${runId}/events`,
};
