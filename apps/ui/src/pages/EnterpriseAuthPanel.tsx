import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { assertionExpiryText, audienceEntries, readinessText } from "./enterpriseAuth";

/**
 * What enterprise-managed authorization can do for the operator reading this page.
 *
 * Closes the ADR3-M6-003 deferral of the enterprise authorization configuration panel.
 * It renders `GET /api/v1/enterprise-auth/profile` and nothing else, and it renders only
 * the five keys that response defines: the retained identity assertion, its
 * `secret_store_key`, the identity assertion grant and the enterprise-issued access
 * token are absent from the response by construction (AC3-EMA-05), so there is nothing
 * here to leak and no second request that could reintroduce one.
 *
 * The disabled case is a statement, not a blank: with the `enterprise_auth` flag off an
 * EMA connector behaves exactly like an unauthorized OAuth connector (AC3-EMA-01), and
 * an operator staring at an `AUTH_REQUIRED` server needs to be told that outright
 * rather than left to infer it from an empty panel.
 */
export function EnterpriseAuthPanel() {
  const profile = useQuery({
    queryKey: ["enterprise-auth-profile"],
    queryFn: api.enterpriseAuthProfile,
  });

  return (
    <section aria-label="Enterprise authorization" tabIndex={-1} className="registry-card">
      <h2>Enterprise authorization</h2>
      {profile.data ? (
        <>
          <p aria-label="Enterprise authorization readiness">{readinessText(profile.data)}</p>
          <dl className="registry-list">
            <dt>Enabled</dt>
            <dd aria-label="Enterprise authorization enabled">
              {profile.data.enabled ? "enabled" : "disabled"}
            </dd>
            <dt>Token exchange</dt>
            <dd aria-label="Token exchange configured">
              {profile.data.token_exchange_configured ? "configured" : "not configured"}
            </dd>
            <dt>Live assertion</dt>
            <dd aria-label="Live assertion">
              {profile.data.has_live_assertion ? "held by this session" : "none held"}
            </dd>
            <dt>Assertion expires</dt>
            <dd aria-label="Assertion expires at">{assertionExpiryText(profile.data)}</dd>
          </dl>
          <h3>Connector audiences</h3>
          <ul
            aria-label="Enterprise authorization audiences"
            role="list"
            tabIndex={0}
            className="registry-list event-list"
          >
            {audienceEntries(profile.data).map(([connectorId, audience]) => (
              <li key={connectorId} aria-label={`audience ${connectorId}`}>
                <strong>{connectorId}</strong>
                <span>{audience}</span>
              </li>
            ))}
            {audienceEntries(profile.data).length === 0 ? (
              <li>No connector is mapped to an enterprise audience.</li>
            ) : null}
          </ul>
        </>
      ) : (
        <p aria-label="Enterprise authorization readiness">
          The enterprise authorization profile is unavailable.
        </p>
      )}
    </section>
  );
}
