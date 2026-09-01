import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import {
  grantText,
  pluginRows,
  providerProjections,
  resolutionText,
  scopeRequirements,
  scopeText,
  type PluginRow,
} from "./pluginRows";
import type { PluginDetail } from "../types";

function DetailPanel({ detail }: { detail: PluginDetail }) {
  const installation = detail.installation;
  // SDD 16.1 asks the detail screen for skills, required scopes, verifiers, policies and
  // provider projections. All five live on the manifest carried by the version record,
  // never on the mutable installation, so they are shown even for a registry entry this
  // workspace has not installed.
  const manifest = detail.version_record?.manifest ?? null;
  const scopes = scopeRequirements(manifest);
  const projections = providerProjections(manifest);
  return (
    <section aria-label={`Plugin detail ${detail.plugin_id}`} tabIndex={-1} className="registry-card">
      <h2>{detail.plugin_id}</h2>
      {installation === null || installation === undefined ? (
        <p>Not installed in this workspace, so it has no capability grants to show.</p>
      ) : (
        <>
          <p aria-label="Installation summary">
            version {installation.version} · state {installation.state} · trust{" "}
            {installation.trust_level}
          </p>

          <h3>Requested capabilities</h3>
          <ul aria-label="Requested capabilities">
            {(installation.requested_capability_ids ?? []).map((capabilityId) => (
              <li key={capabilityId}>{capabilityId}</li>
            ))}
          </ul>

          <h3>Capability grants</h3>
          <ul aria-label="Capability grants">
            {(installation.capability_grants ?? []).map((grant) => (
              <li key={grant.capability_id} aria-label={`grant ${grant.capability_id}`}>
                <strong>{grant.capability_id}</strong>
                <span>{grantText(grant)}</span>
              </li>
            ))}
          </ul>

          <h3>Connectors</h3>
          <ul aria-label="Connector resolutions">
            {(installation.connector_resolutions ?? []).map((resolution) => (
              <li
                key={resolution.connector_id}
                aria-label={`connector ${resolution.connector_id}`}
              >
                <strong>{resolution.connector_id}</strong>
                <span>{resolutionText(resolution)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Skills</h3>
      <ul aria-label="Skills">
        {(manifest?.skills ?? []).map((skill) => (
          <li key={skill.skill_id} aria-label={`skill ${skill.skill_id}`}>
            <strong>{skill.skill_id}</strong>
            <span>
              {skill.version} · {skill.description}
            </span>
          </li>
        ))}
        {manifest && (manifest.skills ?? []).length === 0 ? (
          <li>This plugin contributes no skills.</li>
        ) : null}
      </ul>

      <h3>Required scopes</h3>
      <ul aria-label="Required scopes">
        {scopes.map((requirement) => (
          <li
            key={`${requirement.connector_id}:${requirement.required}`}
            aria-label={`scopes ${requirement.connector_id}`}
          >
            <strong>{requirement.connector_id}</strong>
            <span>{scopeText(requirement)}</span>
          </li>
        ))}
        {scopes.length === 0 ? <li>This plugin requires no connector scopes.</li> : null}
      </ul>

      <h3>Verifiers</h3>
      <ul aria-label="Verifiers">
        {(manifest?.verifiers ?? []).map((verifier) => (
          <li key={verifier}>{verifier}</li>
        ))}
        {manifest && (manifest.verifiers ?? []).length === 0 ? (
          <li>This plugin declares no verifiers.</li>
        ) : null}
      </ul>

      <h3>Policies</h3>
      <ul aria-label="Policies">
        {(manifest?.policies ?? []).map((policy) => (
          <li key={policy}>{policy}</li>
        ))}
        {manifest && (manifest.policies ?? []).length === 0 ? (
          <li>This plugin declares no policies.</li>
        ) : null}
      </ul>

      <h3>Provider projections</h3>
      <ul aria-label="Provider projections">
        {projections.map(([provider, path]) => (
          <li key={provider} aria-label={`projection ${provider}`}>
            {provider} → {path}
          </li>
        ))}
        {projections.length === 0 ? <li>This plugin ships no provider projections.</li> : null}
      </ul>

      <h3>Recent events</h3>
      <ul aria-label="Recent events">
        {(detail.recent_events ?? []).map((event) => (
          <li key={event.plugin_event_id} aria-label={`event ${event.plugin_event_id}`}>
            {event.event_type} · {event.from_state ?? "—"} → {event.to_state ?? "—"}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PluginsPage() {
  const [selected, setSelected] = useState<PluginRow>();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  const installations = useQuery({
    queryKey: ["plugin-installations"],
    queryFn: () => api.pluginInstallations(),
  });
  const detail = useQuery({
    queryKey: ["plugin-detail", selected?.plugin_id, selected?.installation?.workspace_id],
    queryFn: () =>
      api.pluginDetail(selected!.plugin_id, selected?.installation?.workspace_id ?? undefined),
    enabled: Boolean(selected),
  });

  const rows = pluginRows(plugins.data ?? [], installations.data ?? []);

  return (
    <section className="page-panel">
      <header className="section-heading">
        <div>
          <p className="eyebrow">Extensions</p>
          <h1>Plugins</h1>
        </div>
      </header>

      <section aria-label="Installed plugins" tabIndex={-1} className="registry-card">
        <h2>Registry and installations</h2>
        <table className="benchmark-table">
          <thead>
            <tr>
              <th>Plugin</th><th>Version</th><th>State</th><th>Trust</th>
              <th>Requested capabilities</th><th>Inspect</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.plugin_id} aria-label={row.plugin_id}>
                <td>{row.plugin_id}</td>
                <td>{row.version}</td>
                <td>{row.state}</td>
                <td>{row.trust_level}</td>
                <td>
                  {row.installation?.requested_capability_ids?.length
                    ? row.installation.requested_capability_ids.join(" · ")
                    : "none requested"}
                </td>
                <td>
                  <button type="button" onClick={() => setSelected(row)}>
                    Inspect {row.plugin_id}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {selected && detail.data ? <DetailPanel detail={detail.data} /> : null}
    </section>
  );
}
