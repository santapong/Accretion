import type { ResolvedCapability } from "../types";

/**
 * The prose an operator reads instead of an outcome enum.
 *
 * Every outcome is named. A resolution that failed must say *why* it failed in the
 * same words the resolver used, so the inspector never shows an empty panel next to a
 * capability the runtime is refusing to run.
 */
export function outcomeText(resolution: ResolvedCapability): string {
  switch (resolution.outcome) {
    case "OK":
      return "Resolved — the capability is bound and connected.";
    case "NO_CONNECTOR_REQUIRED":
      return "Resolved — this capability needs no connector.";
    case "NO_CONNECTION":
      return "Not connected — no usable connection for this principal.";
    case "REQUIRE_REAUTH":
      return "Re-consent required before this capability can run.";
    case "DISABLED":
      return "Disabled — the capability will not run.";
    default:
      return "Unknown resolution outcome.";
  }
}

/** Whether the outcome is one the runtime will actually execute. */
export function isUsable(resolution: ResolvedCapability): boolean {
  return resolution.outcome === "OK" || resolution.outcome === "NO_CONNECTOR_REQUIRED";
}

/** The backend the binding names, including the MCP server and tool when it has one. */
export function backendText(resolution: ResolvedCapability): string {
  const binding = resolution.binding;
  if (!binding) return "no binding — the capability is not bound to any connector";
  const backend = binding.backend;
  const parts: string[] = [backend.type];
  if (backend.server_ref) parts.push(`server ${backend.server_ref}`);
  if (backend.tool_name) parts.push(`tool ${backend.tool_name}`);
  if (backend.method) parts.push(`method ${backend.method}`);
  return parts.join(" · ");
}

/** The binding's own identity, which is what distinguishes two bindings of one id. */
export function bindingText(resolution: ResolvedCapability): string {
  const binding = resolution.binding;
  if (!binding) return "no binding";
  return `${binding.binding_id} · connector ${binding.connector_id} · ${
    binding.enabled ? "enabled" : "disabled"
  }`;
}

/**
 * The connection actually selected.
 *
 * A resolution with no connection is reported as such: showing a blank here would let
 * an operator read "no connection" as "the panel did not load".
 */
export function connectionText(resolution: ResolvedCapability): string {
  const connection = resolution.connection;
  if (!connection) return "no connection selected";
  return `${connection.connection_id} · ${connection.connector_id} · ${connection.status}`;
}

/** The policy the binding is governed by, if it names one. */
export function policyText(resolution: ResolvedCapability): string {
  return resolution.binding?.policy_ref ?? "no policy reference";
}

/** The resolver's own words. Never empty, so the denial always has a stated reason. */
export function reasonText(resolution: ResolvedCapability): string {
  return resolution.reason?.trim() || "the resolver gave no reason";
}
