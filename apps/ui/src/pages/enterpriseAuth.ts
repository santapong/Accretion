import type { EnterpriseAuthProfileResponse } from "../types";

/**
 * The sentences an operator reads to know whether an enterprise authorization can
 * succeed right now.
 *
 * Derived from `GET /api/v1/enterprise-auth/profile` and from nothing else. The profile
 * describes configuration and state only — the retained identity assertion, its
 * `secret_store_key`, the identity assertion grant and the enterprise-issued access
 * token are absent from the response by construction (AC3-EMA-05) — so these helpers
 * can say why a mint would fail without naming any material.
 */
export function readinessText(profile: EnterpriseAuthProfileResponse): string {
  if (!profile.enabled) {
    return "Enterprise-managed authorization is disabled. EMA connectors behave as unauthorized connectors and require standard OAuth.";
  }
  if (!profile.token_exchange_configured) {
    return "Enabled, but no token exchange endpoint is configured, so no assertion can be exchanged.";
  }
  if (!profile.has_live_assertion) {
    return "Enabled and configured, but this session holds no live identity assertion. Sign in again to obtain one.";
  }
  return "Enabled, configured, and this session holds a live identity assertion.";
}

/** When the retained assertion stops being usable, or that there is none to expire. */
export function assertionExpiryText(profile: EnterpriseAuthProfileResponse): string {
  if (!profile.has_live_assertion) {
    return "no live assertion";
  }
  return profile.assertion_expires_at
    ? new Date(profile.assertion_expires_at).toISOString()
    : "no expiry recorded";
}

/**
 * Why an enterprise authorization cannot be attempted, or the empty string when it can.
 *
 * Enterprise-managed authorization is optional and flag-gated: with the flag off an EMA
 * connector is exactly an unauthorized connector (AC3-EMA-01), so the action is disabled
 * and the reason is written on the control itself rather than discovered by clicking it.
 */
export function enterpriseBlockedReason(
  profile: EnterpriseAuthProfileResponse | undefined,
): string {
  if (!profile) {
    return "the enterprise authorization profile is unavailable";
  }
  if (!profile.enabled) {
    return "enterprise-managed authorization is disabled";
  }
  if (!profile.token_exchange_configured) {
    return "no token exchange endpoint is configured";
  }
  if (!profile.has_live_assertion) {
    return "this session holds no live identity assertion";
  }
  return "";
}

/** The connector-to-audience mapping as sorted pairs, so the list order is stable. */
export function audienceEntries(
  profile: EnterpriseAuthProfileResponse,
): [string, string][] {
  return Object.entries(profile.audiences ?? {}).sort((left, right) =>
    left[0].localeCompare(right[0]),
  );
}
