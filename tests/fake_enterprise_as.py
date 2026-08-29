"""In-process enterprise authorization server for M7 tests.

Accepts the RFC 7523 ``jwt-bearer`` grant only: an identity assertion grant (ID-JAG)
minted by ``fake_idp`` is presented, verified against that provider's JWKS, and
answered with an access token. Wire into ``JwtBearerClient`` with
``httpx.AsyncClient(transport=ASGITransport(app))``.

Distinct from ``fake_authorization_server``: that one runs the end-user
authorization-code flow, which is exactly the step enterprise-managed authorization
is supposed to remove. ``grant_calls`` is the counter that proves it — a refusal that
never reaches this server leaves it at zero.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

ISSUER = "https://enterprise-as.test"
JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"


@dataclass
class FakeEnterpriseAuthorizationServer:
    """Mints access tokens against verified ID-JAGs and records what it was sent."""

    #: The identity provider's public JWKS, as ``fake_idp.jwks_document()`` returns it.
    jwks: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    #: The issuer an acceptable assertion must carry.
    expected_issuer: str = "https://idp.test"
    #: The audience an acceptable assertion must carry.
    expected_audience: str = ""
    expires_in: int = 3600
    #: Grant fewer scopes than requested, as real servers do.
    downgrade_scopes_to: list[str] | None = None
    should_reject: bool = False
    #: Requests that reached the token endpoint, refused ones included.
    grant_calls: int = 0
    #: Requests that reached the end-user authorization endpoint. The whole point of
    #: enterprise-managed authorization is that this stays at zero.
    authorize_calls: int = 0
    #: The Authorization header of the most recent request to this server.
    last_authorization_header: str | None = None
    #: Assertions the token endpoint has been handed, newest last.
    assertions: list[str] = field(default_factory=list)
    issued: list[str] = field(default_factory=list)

    def _verify(self, assertion: str) -> dict[str, Any] | None:
        try:
            header = jwt.get_unverified_header(assertion)
        except jwt.PyJWTError:
            return None
        for key_data in self.jwks.get("keys", []):
            if key_data.get("kid") != header.get("kid"):
                continue
            try:
                claims: dict[str, Any] = jwt.decode(
                    assertion,
                    jwt.PyJWK(key_data).key,
                    algorithms=["RS256"],
                    audience=self.expected_audience,
                    issuer=self.expected_issuer,
                )
            except jwt.PyJWTError:
                return None
            return claims
        return None

    def app(self) -> FastAPI:
        server = FastAPI()

        @server.middleware("http")
        async def record_authorization(request: Request, call_next: Any) -> Any:
            self.last_authorization_header = request.headers.get("authorization")
            return await call_next(request)

        @server.get("/authorize")
        async def authorize(request: Request) -> Response:
            """The end-user consent step enterprise authorization is meant to remove."""

            self.authorize_calls += 1
            params = dict(request.query_params)
            location = (
                f"{params.get('redirect_uri', '')}?code={secrets.token_urlsafe(8)}"
                f"&state={params.get('state', '')}"
            )
            return RedirectResponse(location, status_code=302)

        @server.post("/token")
        async def token(request: Request) -> JSONResponse:
            self.grant_calls += 1
            form = parse_qs((await request.body()).decode())
            if form.get("grant_type", [""])[0] != JWT_BEARER_GRANT_TYPE:
                return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
            assertion = form.get("assertion", [""])[0]
            self.assertions.append(assertion)
            if self.should_reject or self._verify(assertion) is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            requested = form.get("scope", [""])[0].split()
            granted = (
                self.downgrade_scopes_to
                if self.downgrade_scopes_to is not None
                else requested
            )
            access = f"ema_{secrets.token_urlsafe(16)}"
            self.issued.append(access)
            return JSONResponse(
                {
                    "access_token": access,
                    "token_type": "bearer",
                    "scope": " ".join(granted),
                    "expires_in": self.expires_in,
                }
            )

        @server.get("/resource")
        async def resource(request: Request) -> JSONResponse:
            """A protected resource, so a test can show which token was presented."""

            header = request.headers.get("authorization", "")
            _, _, presented = header.partition(" ")
            if presented not in self.issued:
                return JSONResponse({"error": "invalid_token"}, status_code=401)
            return JSONResponse({"ok": True})

        return server
