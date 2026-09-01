"""Connector OAuth client (v0.3 M2, SDD §6.1).

Deliberately separate from ``identity.OidcClient``. That client answers "who are you
in Accretion" and its ``exchange_code -> str`` signature is what mechanically proves
it discards the OAuth access token; this one answers "what may Accretion do on your
behalf" and keeps access and refresh material (ADR3-003).

The PKCE, discovery, and authorization-URL mechanics are shared with the login flow
rather than reimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from accretion.identity import code_challenge_s256


class OAuthError(Exception):
    """Connector authorization failed; never carries token material."""


@dataclass(frozen=True)
class OAuthEndpoints:
    """Where a connector's authorization server lives."""

    authorization_url: str
    token_url: str
    revocation_url: str | None = None
    audience: tuple[str, ...] = ()


@dataclass
class OAuthTokenResponse:
    """A token endpoint's answer. Never persisted or serialized as-is."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    refresh_token_expires_in: int | None = None
    granted_scopes: list[str] = field(default_factory=list)
    token_type: str = "bearer"

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"OAuthTokenResponse(scopes={self.granted_scopes!r}, redacted)"

    __str__ = __repr__


@dataclass
class OAuthClient:
    """Authorization Code + PKCE against one connector's authorization server."""

    client_id: str
    client_secret: str
    redirect_url: str
    endpoints: OAuthEndpoints
    http: httpx.AsyncClient

    def authorization_url(self, *, state: str, scopes: list[str], code_verifier: str) -> str:
        query = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_url,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge_s256(code_verifier),
            "code_challenge_method": "S256",
        }
        return f"{self.endpoints.authorization_url}?{urlencode(query)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> OAuthTokenResponse:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_url,
                "code_verifier": code_verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> OAuthTokenResponse:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def revoke(self, token: str) -> None:
        if self.endpoints.revocation_url is None:
            return
        try:
            response = await self.http.post(
                self.endpoints.revocation_url,
                data={"token": token, "client_id": self.client_id},
                auth=(self.client_id, self.client_secret),
            )
        except httpx.HTTPError as exc:
            raise OAuthError("token revocation request failed") from exc
        if response.status_code >= 400 and response.status_code != 404:
            raise OAuthError(f"token revocation rejected with {response.status_code}")

    async def _token_request(self, form: dict[str, str]) -> OAuthTokenResponse:
        try:
            response = await self.http.post(
                self.endpoints.token_url,
                data={
                    **form,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                # Several providers, GitHub among them, return form-encoded unless
                # JSON is requested explicitly.
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthError("token endpoint request failed") from exc
        if response.status_code != 200:
            # The body can echo the submitted secret, so it is never surfaced.
            raise OAuthError(f"token endpoint rejected the request with {response.status_code}")
        return self.parse_token_response(response.json())

    @staticmethod
    def parse_token_response(payload: dict[str, Any]) -> OAuthTokenResponse:
        if payload.get("error"):
            raise OAuthError("token endpoint returned an error")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("token endpoint returned no access_token")
        raw_scope = payload.get("scope") or ""
        granted = [item for item in str(raw_scope).replace(",", " ").split(" ") if item]
        refresh_token = payload.get("refresh_token")
        return OAuthTokenResponse(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_in=_as_int(payload.get("expires_in")),
            refresh_token_expires_in=_as_int(payload.get("refresh_token_expires_in")),
            granted_scopes=granted,
            token_type=str(payload.get("token_type", "bearer")),
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
