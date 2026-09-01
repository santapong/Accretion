import type {
  MetaPlugin,
  MetaPluginManifest,
  PluginCapabilityGrant,
  PluginConnectorResolution,
  PluginInstallation,
} from "../types";

/** One registry entry next to the installation, if this workspace has one. */
export interface PluginRow {
  plugin_id: string;
  version: string;
  state: string;
  trust_level: string;
  installation: PluginInstallation | null;
  registry: MetaPlugin | null;
}

/**
 * Join the registry listing to the installation listing.
 *
 * A registry entry with no installation is a package that is merely *available*, and
 * saying so is the whole point of the page: an operator diagnosing "why can't the agent
 * do this" needs "not installed here" to read differently from "installed and disabled".
 */
export function pluginRows(
  plugins: MetaPlugin[],
  installations: PluginInstallation[],
): PluginRow[] {
  const byId = new Map(installations.map((item) => [item.plugin_id, item]));
  return plugins.map((entry) => {
    const installation = byId.get(entry.plugin_id) ?? null;
    return {
      plugin_id: entry.plugin_id,
      version: installation?.version ?? entry.version,
      state: installation?.state ?? "NOT_INSTALLED",
      trust_level: installation?.trust_level ?? "unknown",
      installation,
      registry: entry,
    };
  });
}

/** What the manifest asked for next to what policy actually allowed. */
export function grantText(grant: PluginCapabilityGrant): string {
  const granted = grant.granted_permissions?.length
    ? grant.granted_permissions.join(" ")
    : "no permissions";
  const requested = grant.requested_permissions?.length
    ? grant.requested_permissions.join(" ")
    : "no permissions";
  return `${grant.decision} · requested ${requested} · granted ${granted}`;
}

/**
 * Whether a connector requirement is met, said in words rather than a colour.
 *
 * Optional-and-unsatisfied is not a fault, and an operator must not have to guess
 * which of the two an unticked row is.
 */
export function resolutionText(resolution: PluginConnectorResolution): string {
  if (resolution.satisfied) {
    return `satisfied by ${resolution.connection_id ?? "an existing connection"}`;
  }
  const missing = resolution.missing_scopes?.length
    ? ` · missing scopes ${resolution.missing_scopes.join(" ")}`
    : "";
  return resolution.required
    ? `not satisfied — required${missing}`
    : `not satisfied — optional${missing}`;
}

/** One connector requirement and the OAuth scopes the manifest asks it for (SDD 16.1). */
export interface ScopeRequirement {
  connector_id: string;
  required: boolean;
  scopes: string[];
}

/**
 * The manifest's required scopes, flattened across required and optional connectors.
 *
 * SDD 16.1 lists "required scopes" separately from "connectors" because a scope is what
 * the operator has to grant in the provider's console; a connector whose requirement
 * asks for no scopes is a different situation from one whose scopes are simply not yet
 * granted, so the empty case is stated rather than dropped.
 */
export function scopeRequirements(manifest: MetaPluginManifest | null | undefined): ScopeRequirement[] {
  if (!manifest) {
    return [];
  }
  const required = (manifest.required_connectors ?? []).map((requirement) => ({
    connector_id: requirement.connector_id,
    required: true,
    scopes: requirement.scopes ?? [],
  }));
  const optional = (manifest.optional_connectors ?? []).map((requirement) => ({
    connector_id: requirement.connector_id,
    required: false,
    scopes: requirement.scopes ?? [],
  }));
  return [...required, ...optional];
}

/** A scope requirement said in words, including the "asks for none" case. */
export function scopeText(requirement: ScopeRequirement): string {
  const need = requirement.required ? "required" : "optional";
  return requirement.scopes.length
    ? `${need} · scopes ${requirement.scopes.join(" ")}`
    : `${need} · no scopes requested`;
}

/** Provider projection paths, as `provider → package-relative path` pairs. */
export function providerProjections(
  manifest: MetaPluginManifest | null | undefined,
): Array<[string, string]> {
  return Object.entries(manifest?.provider_projections ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );
}
