import type { McpServerDefinition } from "../types";

function instant(value: string | null | undefined, fallback: string): string {
  return value ? new Date(value).toISOString() : fallback;
}

/**
 * The one sentence an operator reads to decide whether this server is the problem.
 *
 * Derived, never stored: the API sends `state`, `consecutive_failures` and
 * `circuit_open_until` and says nothing about how they combine, so a server whose
 * breaker has opened must not read like one that merely failed once, and a server that
 * was switched off must not read like one that is serving. Deliberately independent of
 * the wall clock: "is the cooldown over yet" is the manager's decision, not the page's,
 * and guessing at it here would make the page disagree with the next request.
 */
export function healthText(server: McpServerDefinition): string {
  const failures = server.consecutive_failures ?? 0;
  if (!server.enabled || server.state === "DISABLED") {
    return "Disabled — not serving, and no health is being collected";
  }
  if (server.circuit_open_until) {
    return `Circuit open until ${instant(server.circuit_open_until, "—")} after ${failures} consecutive failures`;
  }
  if (failures > 0) {
    return `Circuit closed, but ${failures} consecutive failures since the last success`;
  }
  if (server.state === "READY") {
    return `Healthy — last checked ${instant(server.last_health_check, "never")}`;
  }
  return `${server.state} — last checked ${instant(server.last_health_check, "never")}`;
}

/** Where the server is reached: an HTTP endpoint or a stdio command line. */
export function targetText(server: McpServerDefinition): string {
  if (server.transport === "STDIO") {
    return server.command?.length ? server.command.join(" ") : "no command configured";
  }
  return server.endpoint ?? "no endpoint configured";
}

/** An allow/deny pattern list, or a word saying it is empty. */
export function patternList(patterns: string[] | undefined, empty: string): string {
  return patterns && patterns.length ? patterns.join(" · ") : empty;
}

/**
 * The server's auth mode, which SDD 16.3 requires on the detail screen.
 *
 * `auth_profile_ref` is a pointer, not a secret, and its absence is itself a diagnosis:
 * an unauthenticated server reached over HTTP is a different posture from one bound to
 * a stored auth profile, so the empty case is named rather than blank.
 */
export function authModeText(server: McpServerDefinition): string {
  return server.auth_profile_ref
    ? `auth profile ${server.auth_profile_ref}`
    : "no auth profile — requests are sent unauthenticated";
}

/**
 * A human label for one discovered MCP item.
 *
 * Discovery payloads are `dict[str, Any]` by contract, so the page picks the first
 * identifying key MCP defines for that kind and falls back to a stated unnamed case
 * rather than rendering `undefined`.
 */
export function discoveredLabel(item: Record<string, unknown>): string {
  for (const key of ["name", "uri", "uriTemplate", "title"]) {
    const value = item[key];
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
  }
  return "unnamed item";
}
