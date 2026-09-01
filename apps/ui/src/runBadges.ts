import type { RunAudit } from "./types";

/**
 * Capability provenance for one node of the execution graph.
 *
 * A badge is a pure projection of a persisted `CapabilityExecutionResult`: the plugin,
 * connector, connection and binding the gateway really resolved when it executed the
 * request. The plugin is read out of the synthetic connector id the plugin registration
 * layer mints, so it costs no extra request and cannot disagree with the audit. Nothing here is authoritative — the badge cannot grant, revoke or re-resolve
 * anything, and this module deliberately imports no API client, so a badge can never
 * become a control surface by accident.
 */
export interface NodeBadge {
  readonly requestId: string;
  readonly capabilityId: string;
  readonly status: string;
  readonly pluginId?: string;
  readonly connectorId?: string;
  readonly connectionId?: string;
  readonly bindingId?: string;
}

/**
 * The prefix of the synthetic, credential-free connector every plugin capability is
 * bound through (`plugin_connector_id` in `src/accretion/plugins/registration.py`).
 * A plugin-served call is therefore identifiable from the persisted audit alone, with
 * no extra request and no second source of truth.
 */
const PLUGIN_CONNECTOR_PREFIX = "conndef_plugin_";

/** The plugin that served a call, read out of the connector the gateway resolved. */
function pluginIdentity(connectorId: string | undefined): string | undefined {
  if (!connectorId || !connectorId.startsWith(PLUGIN_CONNECTOR_PREFIX)) return undefined;
  return identity(connectorId.slice(PLUGIN_CONNECTOR_PREFIX.length));
}

export type NodeBadgeIndex = ReadonlyMap<string, readonly NodeBadge[]>;

function identity(value: string | null | undefined): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : undefined;
}

/**
 * Index the audit's capability results by the graph node that issued them.
 *
 * Results arrive in the order the store recorded them and keep that order per node, so
 * a re-render with a newer audit shows the newer identities rather than a cached copy.
 * A result whose request the store never saw twice is kept once, keyed on `request_id`.
 */
export function nodeBadges(audit: Pick<RunAudit, "capability_results"> | undefined): NodeBadgeIndex {
  const index = new Map<string, NodeBadge[]>();
  const seen = new Set<string>();
  for (const result of audit?.capability_results ?? []) {
    const request = result.request;
    const nodeId = identity(request?.node_id);
    const requestId = identity(request?.request_id);
    if (!nodeId || !requestId || seen.has(requestId)) continue;
    seen.add(requestId);
    const connectorId = identity(result.connector_id);
    const pluginId = pluginIdentity(connectorId);
    const badge: NodeBadge = {
      requestId,
      capabilityId: request.capability_id,
      status: result.status,
      ...(pluginId ? { pluginId } : {}),
      ...(connectorId ? { connectorId } : {}),
      ...(identity(result.connection_id) ? { connectionId: identity(result.connection_id) } : {}),
      ...(identity(result.binding_id) ? { bindingId: identity(result.binding_id) } : {}),
    };
    const existing = index.get(nodeId);
    if (existing) existing.push(badge);
    else index.set(nodeId, [badge]);
  }
  return index;
}

/** The identity parts of a badge, in render order, skipping what the audit omits. */
export function badgeParts(badge: NodeBadge): readonly (readonly [string, string])[] {
  const parts: (readonly [string, string])[] = [];
  if (badge.pluginId) parts.push(["plugin", badge.pluginId]);
  if (badge.connectorId) parts.push(["connector", badge.connectorId]);
  if (badge.connectionId) parts.push(["connection", badge.connectionId]);
  if (badge.bindingId) parts.push(["binding", badge.bindingId]);
  return parts;
}
