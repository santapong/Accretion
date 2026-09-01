import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { authModeText, discoveredLabel, healthText, patternList, targetText } from "./mcpHealth";
import { enterpriseBlockedReason } from "./enterpriseAuth";
import type { ConnectorDefinition, McpServerDefinition } from "../types";

function ServerDetail({ server }: { server: McpServerDefinition }) {
  const capabilities = useQuery({
    queryKey: ["mcp-capabilities", server.mcp_server_id],
    queryFn: () => api.mcpServerCapabilities(server.mcp_server_id),
  });
  // A server that has never completed discovery answers 404 here, which is a fact the
  // page reports rather than an error it retries into.
  const discovery = useQuery({
    queryKey: ["mcp-discovery", server.mcp_server_id],
    queryFn: () => api.mcpServerDiscovery(server.mcp_server_id),
    retry: false,
  });

  return (
    <section
      aria-label={`MCP server detail ${server.mcp_server_id}`}
      tabIndex={-1}
      className="registry-card"
    >
      <h2>{server.name}</h2>
      <p aria-label="Server target">
        {server.transport} · {targetText(server)} · protocol{" "}
        {patternList(server.protocol_versions, "unversioned")}
      </p>
      <p aria-label="Auth mode">{authModeText(server)}</p>
      <p aria-label="Tool patterns">
        allow {patternList(server.allowed_tool_patterns, "nothing")} · deny{" "}
        {patternList(server.denied_tool_patterns, "nothing")}
      </p>

      <h3>Capabilities</h3>
      <ul aria-label="Server capabilities">
        {(capabilities.data ?? []).map((capability) => (
          <li key={capability.capability_id} aria-label={`capability ${capability.capability_id}`}>
            {capability.capability_id} · {capability.backend} ·{" "}
            {capability.enabled ? "enabled" : "disabled"}
          </li>
        ))}
      </ul>

      <h3>Discovery</h3>
      {discovery.data ? (
        <>
          <p aria-label="Discovery summary">
            protocol {discovery.data.protocol_version} · {discovery.data.tools?.length ?? 0} tools ·{" "}
            {discovery.data.resources?.length ?? 0} resources ·{" "}
            {discovery.data.resource_templates?.length ?? 0} resource templates ·{" "}
            {discovery.data.prompts?.length ?? 0} prompts ·{" "}
            {discovery.data.valid ? "schemas valid" : "schema errors present"}
          </p>
          <ul aria-label="Discovered tools">
            {(discovery.data.tools ?? []).map((tool) => (
              <li key={String(tool.name)}>{String(tool.name)}</li>
            ))}
          </ul>
          <ul aria-label="Discovered resources">
            {(discovery.data.resources ?? []).map((resource) => (
              <li key={discoveredLabel(resource)}>{discoveredLabel(resource)}</li>
            ))}
            {(discovery.data.resources ?? []).length === 0 ? (
              <li>This server published no resources.</li>
            ) : null}
          </ul>
          <ul aria-label="Discovered resource templates">
            {(discovery.data.resource_templates ?? []).map((template) => (
              <li key={discoveredLabel(template)}>{discoveredLabel(template)}</li>
            ))}
            {(discovery.data.resource_templates ?? []).length === 0 ? (
              <li>This server published no resource templates.</li>
            ) : null}
          </ul>
          <ul aria-label="Discovered prompts">
            {(discovery.data.prompts ?? []).map((prompt) => (
              <li key={discoveredLabel(prompt)}>{discoveredLabel(prompt)}</li>
            ))}
            {(discovery.data.prompts ?? []).length === 0 ? (
              <li>This server published no prompts.</li>
            ) : null}
          </ul>
          <ul aria-label="Cache hints">
            {Object.entries(discovery.data.cache_hints ?? {}).map(([kind, hint]) => (
              <li key={kind} aria-label={`cache hint ${kind}`}>
                {kind} · {hint.ttl_ms} ms · {hint.scope}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p aria-label="Discovery summary">No discovery snapshot has been recorded yet.</p>
      )}
    </section>
  );
}

export function McpServersPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<McpServerDefinition>();
  const [status, setStatus] = useState<string>();
  const servers = useQuery({ queryKey: ["mcp-servers"], queryFn: () => api.mcpServers() });
  const connectors = useQuery({ queryKey: ["connectors"], queryFn: api.connectors });
  const profile = useQuery({
    queryKey: ["enterprise-auth-profile"],
    queryFn: api.enterpriseAuthProfile,
  });
  const blocked = enterpriseBlockedReason(profile.data);

  function connectorOf(server: McpServerDefinition): ConnectorDefinition | undefined {
    return (connectors.data ?? []).find(
      (connector) => connector.connector_id === server.connector_id,
    );
  }

  // No mutation API is in use in this app: a POST is an async handler that calls the
  // client and then invalidates what the POST changed. Enterprise authorization mints a
  // connection and moves the server out of AUTH_REQUIRED, so both are re-read.
  async function authorize(server: McpServerDefinition) {
    setStatus(`Authorizing ${server.name} with the enterprise authorization manager…`);
    try {
      const connection = await api.enterpriseAuthorizeMcpServer(server.mcp_server_id);
      setStatus(
        `${server.name}: connection ${connection.connection_id} is ${connection.status}.`,
      );
    } catch (error) {
      setStatus(
        error instanceof Error
          ? `${server.name}: ${error.message}`
          : `${server.name}: enterprise authorization failed.`,
      );
    }
    await queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    await queryClient.invalidateQueries({ queryKey: ["connections"] });
  }

  return (
    <section className="page-panel">
      <header className="section-heading">
        <div>
          <p className="eyebrow">Remote tools</p>
          <h1>MCP servers</h1>
        </div>
      </header>

      <section aria-label="MCP servers" tabIndex={0} className="registry-card">
        <h2>Registered servers</h2>
        <table className="benchmark-table">
          <thead>
            <tr>
              <th>Server</th><th>State</th><th>Health</th><th>Failures</th>
              <th>Trust</th><th>Transport</th><th>Authorization</th><th>Inspect</th>
            </tr>
          </thead>
          <tbody>
            {(servers.data ?? []).map((server) => (
              <tr key={server.mcp_server_id} aria-label={server.mcp_server_id}>
                <td>{server.name}</td>
                <td>{server.state}</td>
                <td>{healthText(server)}</td>
                <td>{server.consecutive_failures}</td>
                <td>{server.trust_level}</td>
                <td>{server.transport} · {targetText(server)}</td>
                <td aria-label={`authorization ${server.mcp_server_id}`}>
                  {connectorOf(server)?.auth_type === "EMA" ? (
                    <>
                      <span>EMA</span>{" "}
                      <button
                        type="button"
                        disabled={Boolean(blocked)}
                        aria-label={
                          blocked
                            ? `Authorize (enterprise) ${server.name} — unavailable: ${blocked}`
                            : `Authorize (enterprise) ${server.name}`
                        }
                        onClick={() => void authorize(server)}
                      >
                        Authorize (enterprise)
                      </button>
                    </>
                  ) : (
                    connectorOf(server)?.auth_type ?? "unknown"
                  )}
                </td>
                <td>
                  <button type="button" onClick={() => setSelected(server)}>
                    Inspect {server.name}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p aria-label="Enterprise authorization status" role="status">
        {status ?? "No enterprise authorization has been attempted in this session."}
      </p>

      {selected ? <ServerDetail server={selected} /> : null}
    </section>
  );
}
