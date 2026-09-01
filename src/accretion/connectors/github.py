"""GitHub OAuth connector (v0.3 M2).

The SDD names no connector for M2's "one OAuth connector works end to end" exit
criterion, so GitHub is a recorded choice rather than a specification. Capability IDs
stay provider-neutral (INV3-010); nothing here leaks into workflow logic.

Two GitHub behaviours shape this module and are worth stating rather than discovering:

* **Refresh tokens exist only when the app opts into expiring user tokens.** Access
  tokens then last 8 hours and refresh tokens 6 months. Without that setting GitHub
  returns no refresh token at all and the lifecycle cannot be exercised.
* **The token endpoint answers form-encoded unless JSON is requested**, which is why
  ``OAuthClient`` sends ``Accept: application/json``.
"""

from __future__ import annotations

from accretion.contracts import (
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
)
from accretion.oauth import OAuthEndpoints

GITHUB_CONNECTOR_ID = "conndef_github"

_AUTHORIZATION_SERVER = "https://github.com"
_RESOURCE_SERVER = "https://api.github.com"


def github_endpoints(authorization_server: str = _AUTHORIZATION_SERVER) -> OAuthEndpoints:
    """Endpoints for GitHub's OAuth flow.

    ``authorization_server`` is a parameter so tests can point at an in-process fake
    and so GitHub Enterprise Server keeps working.
    """

    return OAuthEndpoints(
        authorization_url=f"{authorization_server}/login/oauth/authorize",
        token_url=f"{authorization_server}/login/oauth/access_token",
        # Revocation is an API-server route and needs client basic auth.
        revocation_url=None,
        audience=(_RESOURCE_SERVER,),
    )


def github_connector(
    *,
    authorization_server: str = _AUTHORIZATION_SERVER,
    resource_server: str = _RESOURCE_SERVER,
) -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id=GITHUB_CONNECTOR_ID,
        name="GitHub",
        kind=ConnectorKind.REST,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=authorization_server,
        resource_server=resource_server,
        # Least privilege: read-only by default, write available only by explicit
        # re-consent (INV3-007, and the no-silent-scope-broadening rule of SDD 6.3).
        default_scopes=["read:user", "repo:status"],
        optional_scopes=["repo", "workflow", "read:org"],
    )
