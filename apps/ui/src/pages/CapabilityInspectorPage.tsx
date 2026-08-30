import { FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import {
  backendText,
  bindingText,
  connectionText,
  isUsable,
  outcomeText,
  policyText,
  reasonText,
} from "./capabilityResolution";
import type { ResolvedCapability } from "../types";

/**
 * Diagnose one canonical capability id: which binding and which connection the runtime
 * would actually use, and — when it would use none — why.
 *
 * The page never invokes anything. Resolution is a read of authorization state, so a
 * denial is a *result* to be rendered in full, not an error to be swallowed: a blank
 * panel is indistinguishable from a broken page, which is the failure this surface
 * exists to prevent (SDD 24.7 AC3-UI-04).
 */
export function CapabilityInspectorPage() {
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [resolution, setResolution] = useState<ResolvedCapability>();
  const [failure, setFailure] = useState<string>();
  const [status, setStatus] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const capabilityId = String(data.get("capability_id") ?? "").trim();
    const version = String(data.get("version") ?? "").trim();
    const workspaceId = String(data.get("workspace_id") ?? "").trim();
    setStatus(`Resolving ${capabilityId}…`);
    setResolution(undefined);
    setFailure(undefined);
    try {
      const resolved = await api.resolveCapability({
        capability_id: capabilityId,
        version: version || null,
        principal_id: null,
        workspace_id: workspaceId || null,
      });
      setResolution(resolved);
      setStatus(`Resolved ${capabilityId}.`);
    } catch (error) {
      // The API's own message — "no usable connection for connector …", "principal is
      // not a member of the requested workspace" — is the diagnosis. Replacing it with
      // a generic string would hide the one fact the operator came here for.
      setFailure(error instanceof Error ? error.message : "Resolution failed.");
      setStatus(`Could not resolve ${capabilityId}.`);
    }
  }

  const workspaces = (me.data?.memberships ?? []).map((item) => item.workspace_id);

  return (
    <section className="page-panel">
      <header className="section-heading">
        <div>
          <p className="eyebrow">Authorization diagnosis</p>
          <h1>Capability inspector</h1>
        </div>
      </header>
      <p className="page-status" role="status">{status ?? "No capability resolved yet."}</p>

      <form className="task-form" onSubmit={submit} aria-label="Resolve a capability">
        <label>
          Capability id
          <input name="capability_id" required list="inspector-capability-ids" />
        </label>
        <datalist id="inspector-capability-ids">
          {(capabilities.data ?? []).map((item) => (
            <option key={`${item.capability_id}:${item.version}`} value={item.capability_id} />
          ))}
        </datalist>
        <label>
          Version <small>optional</small>
          <input name="version" placeholder="latest" />
        </label>
        <label>
          Workspace
          <select name="workspace_id" defaultValue="">
            <option value="">Any workspace</option>
            {workspaces.map((workspaceId) => (
              <option key={workspaceId} value={workspaceId}>{workspaceId}</option>
            ))}
          </select>
        </label>
        <div className="form-actions field-wide">
          <button className="primary-button" type="submit">Resolve capability</button>
        </div>
      </form>

      {failure ? (
        <section
          aria-label="Resolution failure"
          tabIndex={-1}
          className="registry-card"
          role="alert"
        >
          <h2>Refused</h2>
          <p aria-label="Failure reason">{failure}</p>
        </section>
      ) : null}

      {resolution ? (
        <section aria-label="Resolution" tabIndex={-1} className="registry-card">
          <h2>{resolution.capability.capability_id}@{resolution.capability.version}</h2>
          <dl className="registry-list">
            <dt>Outcome</dt>
            <dd aria-label="Outcome">
              {resolution.outcome} · {outcomeText(resolution)}
            </dd>
            <dt>Reason</dt>
            <dd aria-label="Reason">{reasonText(resolution)}</dd>
            <dt>Risk</dt>
            <dd aria-label="Risk">
              {resolution.capability.risk} · {resolution.capability.kind} ·{" "}
              {resolution.capability.backend}
            </dd>
            <dt>Binding</dt>
            <dd aria-label="Binding">{bindingText(resolution)}</dd>
            <dt>Backend</dt>
            <dd aria-label="Backend">{backendText(resolution)}</dd>
            <dt>Connection</dt>
            <dd aria-label="Connection">{connectionText(resolution)}</dd>
            <dt>Policy</dt>
            <dd aria-label="Policy">{policyText(resolution)}</dd>
          </dl>
          <p aria-label="Usability">
            {isUsable(resolution)
              ? "A run may use this capability now."
              : "A run requesting this capability will be refused."}
          </p>
        </section>
      ) : null}
    </section>
  );
}
