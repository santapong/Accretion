"""In-process OAuth 2.0 authorization server for M2 tests.

Serves the token and revocation endpoints a connector needs, so the whole
authorize -> refresh -> revoke lifecycle runs with no network. Wire into
``OAuthClient`` with ``httpx.AsyncClient(transport=ASGITransport(app))``.

Distinct from ``fake_idp``: that one signs id_tokens for SSO login and issues no
access token at all, which is precisely the separation ADR3-003 requires.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ISSUER = "https://authorization.test"
CLIENT_ID = "accretion-connector-client"
CLIENT_SECRET = "connector-secret"


@dataclass
class FakeAuthorizationServer:
    """Mints authorization codes and tokens, and tracks what has been revoked."""

    #: code -> granted scopes
    codes: dict[str, list[str]] = field(default_factory=dict)
    #: refresh token -> granted scopes
    refresh_tokens: dict[str, list[str]] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)
    issued: list[str] = field(default_factory=list)
    #: Grant fewer scopes than requested, as real servers do.
    downgrade_scopes_to: list[str] | None = None
    #: Emit no refresh token, like a provider without expiring tokens.
    emit_refresh_token: bool = True
    expires_in: int = 28_800
    refresh_should_fail: bool = False

    def issue_code(self, scopes: list[str]) -> str:
        code = f"code_{secrets.token_urlsafe(8)}"
        self.codes[code] = scopes
        return code

    def _grant(self, scopes: list[str]) -> JSONResponse:
        granted = self.downgrade_scopes_to if self.downgrade_scopes_to is not None else scopes
        access = f"gho_{secrets.token_urlsafe(16)}"
        self.issued.append(access)
        payload = {
            "access_token": access,
            "token_type": "bearer",
            "scope": " ".join(granted),
            "expires_in": self.expires_in,
        }
        if self.emit_refresh_token:
            refresh = f"ghr_{secrets.token_urlsafe(16)}"
            self.refresh_tokens[refresh] = list(granted)
            payload["refresh_token"] = refresh
            payload["refresh_token_expires_in"] = 15_897_600
        return JSONResponse(payload)

    def app(self) -> FastAPI:
        api = FastAPI()

        @api.post("/login/oauth/access_token")
        async def token(request: Request) -> JSONResponse:
            # Parsed by hand rather than with Form(...), which would drag
            # python-multipart in as a dependency for a test double.
            form = _form(await request.body())
            grant_type = form.get("grant_type", "")
            code = form.get("code")
            refresh_token = form.get("refresh_token")
            code_verifier = form.get("code_verifier")
            if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            if grant_type == "authorization_code":
                if code is None or code not in self.codes:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                if not code_verifier:
                    return JSONResponse({"error": "invalid_request"}, status_code=400)
                # Codes are single use, so a replayed code fails closed.
                return self._grant(self.codes.pop(code))
            if grant_type == "refresh_token":
                if self.refresh_should_fail:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                if refresh_token is None or refresh_token not in self.refresh_tokens:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                if refresh_token in self.revoked:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                return self._grant(self.refresh_tokens[refresh_token])
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        @api.post("/login/oauth/revoke")
        async def revoke(request: Request) -> JSONResponse:
            value = _form(await request.body()).get("token", "")
            self.revoked.add(value)
            self.refresh_tokens.pop(value, None)
            return JSONResponse({"revoked": True})

        return api


def _form(body: bytes) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(body.decode()).items() if values}
