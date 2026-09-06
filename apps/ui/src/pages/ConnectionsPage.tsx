import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { ConnectionSummary, ConnectorDefinition } from "../types";

/** Where the operator lands after consent; the API echoes it back through the callback. */
const REDIRECT_TARGET = "/admin/connections";

/**
 * Hand the browser to the authorization server.
 *
 * The URL is deliberately never rendered: an authorization URL carries `state` and a
 * PKCE challenge, and putting it in the DOM makes it copyable, screenshotable, and
 * replayable by anyone looking at the operator's screen (SDD 19.1).
 */
function handOffTo(authorizationUrl: string): void {
  window.location.assign(authorizationUrl);
}

function scopeList(scopes: string[] | undefined): string {
  return scopes && scopes.length ? scopes.join(" · ") : "none granted";
}

function healthLine(value: string | null | undefined): string {
  return value ? new Date(value).toISOString() : "never checked";
}

export function ConnectionsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const connectors = useQuery({ queryKey: ["connectors"], queryFn: api.connectors });
  const connections = useQuery({ queryKey: ["connections"], queryFn: api.connections });

  async function act(label: string, work: () => Promise<string>) {
    setStatus(`${label}…`);
    try {
      setStatus(await work());
      await queryClient.invalidateQueries({ queryKey: ["connections"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : `${label} failed.`);
    }
  }

  function connect(connector: ConnectorDefinition) {
    return act(`Authorizing ${connector.name}`, async () => {
      const start = await api.connect(connector.connector_id, {
        workspace_id: "workspace_local",
        redirect_target: REDIRECT_TARGET,
        scopes: null,
      });
      handOffTo(start.authorization_url);
      return `Handed off to the authorization server for ${connector.name}.`;
    });
  }

  function reauthorize(connection: ConnectionSummary) {
    return act("Re-requesting consent", async () => {
      const start = await api.reauthorize(connection.connection_id, {
        workspace_id: connection.workspace_id,
        redirect_target: REDIRECT_TARGET,
        scopes: null,
      });
      handOffTo(start.authorization_url);
      return "Handed off to the authorization server to re-consent.";
    });
  }

  function revoke(connection: ConnectionSummary) {
    return act("Revoking", async () => {
      const revoked = await api.revoke(connection.connection_id);
      return `Connection is now ${revoked.status}.`;
    });
  }

  function checkHealth(connection: ConnectionSummary) {
    return act("Checking health", async () => {
      const report = await api.connectionHealth(connection.connection_id);
      return `Token status ${String(report.token_status ?? "UNKNOWN")} · connection ${String(report.status ?? "UNKNOWN")}.`;
    });
  }

  return (
    <section className="page-panel">
      <header className="section-heading">
        <div>
          <p className="eyebrow">Authorization</p>
          <h1>Connections</h1>
        </div>
      </header>
      <p className="page-status" role="status">{status ?? "No connection action taken yet."}</p>

      <section aria-label="Connectors" tabIndex={0} className="registry-card">
        <h2>Connectors</h2>
        <ul className="registry-list">
          {(connectors.data ?? []).map((connector) => (
            <li key={connector.connector_id} aria-label={connector.connector_id}>
              <strong>{connector.name}</strong>
              <span>{connector.kind} · {connector.auth_type} · {scopeList(connector.default_scopes)}</span>
              <button type="button" onClick={() => connect(connector)}>Connect {connector.name}</button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Connections" tabIndex={0} className="registry-card">
        <h2>Established connections</h2>
        <table className="benchmark-table">
          <thead>
            <tr>
              <th>Connection</th><th>Connector</th><th>Status</th><th>Granted scopes</th>
              <th>Owner</th><th>Last health check</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {(connections.data ?? []).map((connection) => (
              <tr key={connection.connection_id} aria-label={connection.connection_id}>
                <td>{connection.connection_id}</td>
                <td>{connection.connector_id}</td>
                <td>{connection.status}</td>
                <td>{scopeList(connection.granted_scopes)}</td>
                <td>{connection.principal_id ?? `${connection.scope} · ${connection.workspace_id}`}</td>
                <td>{healthLine(connection.last_health_check)}</td>
                <td>
                  <button type="button" onClick={() => reauthorize(connection)}>Reauthorize</button>
                  <button type="button" onClick={() => revoke(connection)}>Revoke</button>
                  <button type="button" onClick={() => checkHealth(connection)}>Check health</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}
