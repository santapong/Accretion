import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { EnterpriseAuthPanel } from "./EnterpriseAuthPanel";
import type { AuthProviderInfo, WorkspaceEntity } from "../types";

/**
 * Who the operator is, and what that identity is allowed to be.
 *
 * Read-only by construction: it renders `GET /me`, `GET /workspaces` and
 * `GET /auth/providers` and offers no mutation, because role assignment and session
 * revocation are enterprise-authorization surfaces (M7). Identity is keyed on
 * (issuer, subject), never on email, so both are shown rather than a display name that
 * looks unique but is not.
 */

function providerText(provider: AuthProviderInfo): string {
  return provider.issuer ? `${provider.mode} · ${provider.issuer}` : provider.mode;
}

function workspaceName(
  workspaces: WorkspaceEntity[] | undefined,
  workspaceId: string,
): string {
  const match = (workspaces ?? []).find((item) => item.workspace_id === workspaceId);
  return match ? match.name : "name unavailable";
}

export function IdentityPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const providers = useQuery({ queryKey: ["auth-providers"], queryFn: api.authProviders });
  const principal = me.data?.principal;

  return (
    <section className="page-panel">
      <header className="section-heading">
        <div>
          <p className="eyebrow">Read-only identity</p>
          <h1>Identity and roles</h1>
        </div>
      </header>

      <section aria-label="Principal" tabIndex={-1} className="registry-card">
        <h2>Principal</h2>
        {principal ? (
          <dl className="registry-list">
            <dt>Principal id</dt>
            <dd aria-label="Principal id">{principal.principal_id}</dd>
            <dt>Issuer and subject</dt>
            <dd aria-label="Issuer and subject">{principal.issuer} · {principal.subject}</dd>
            <dt>Type and status</dt>
            <dd aria-label="Type and status">{principal.type} · {principal.status}</dd>
            <dt>Display name</dt>
            <dd aria-label="Display name">{principal.display_name ?? "not set"}</dd>
            <dt>Email</dt>
            <dd aria-label="Email">{principal.email ?? "not set"}</dd>
          </dl>
        ) : (
          <p aria-label="Principal unavailable">No principal is authenticated.</p>
        )}
      </section>

      <section aria-label="Current session" tabIndex={-1} className="registry-card">
        <h2>Current session</h2>
        <p aria-label="Auth mode">
          Authentication mode {me.data?.auth_mode ?? "unknown"}
        </p>
        <ul aria-label="Auth providers">
          {(providers.data ?? []).map((provider) => (
            <li key={`${provider.mode}:${provider.issuer ?? ""}`}>{providerText(provider)}</li>
          ))}
        </ul>
        <p aria-label="Session subject">
          {principal
            ? `This session authenticates ${principal.subject} at ${principal.issuer}.`
            : "This browser holds no session."}
        </p>
      </section>

      <EnterpriseAuthPanel />

      <section aria-label="Workspace roles" tabIndex={-1} className="registry-card">
        <h2>Workspace roles</h2>
        <table className="benchmark-table">
          <thead>
            <tr><th>Workspace</th><th>Name</th><th>Role</th><th>Membership</th></tr>
          </thead>
          <tbody>
            {(me.data?.memberships ?? []).map((membership) => (
              <tr key={membership.membership_id} aria-label={membership.workspace_id}>
                <td>{membership.workspace_id}</td>
                <td>{workspaceName(workspaces.data, membership.workspace_id)}</td>
                <td>{membership.role}</td>
                <td>{membership.membership_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!(me.data?.memberships ?? []).length ? (
          <p className="empty">This principal is a member of no workspace.</p>
        ) : null}
      </section>
    </section>
  );
}
