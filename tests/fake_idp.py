"""In-process OIDC identity provider for M1 tests.

Serves discovery, JWKS, and a token endpoint that signs RS256 id_tokens. Since M7 it
also serves the RFC 8693 ``/token-exchange`` endpoint that mints identity assertion
grants (ID-JAGs), with knobs to mint one that is wrong on exactly one axis.
Wire into ``OidcClient`` with ``httpx.AsyncClient(transport=ASGITransport(app))``.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

ISSUER = "https://idp.test"
CLIENT_ID = "accretion-test-client"
TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-key-1"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwk() -> dict[str, str]:
    numbers = _PRIVATE_KEY.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": _KID,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


def jwks_document() -> dict[str, list[dict[str, str]]]:
    """The public JWKS, for a fake resource or authorization server to verify with."""

    return {"keys": [_jwk()]}


@dataclass
class FakeUser:
    subject: str
    email: str | None = None
    name: str | None = None


@dataclass
class FakeIdp:
    issuer: str = ISSUER
    claim_issuer: str | None = None  # override to mint a wrong-iss token
    audience: str = CLIENT_ID
    nonce_override: str | None = None
    users: dict[str, FakeUser] = field(default_factory=dict)
    codes: dict[str, tuple[str, str]] = field(default_factory=dict)  # code -> (sub, nonce)
    # RFC 8693 token exchange (M7). The three knobs mirror ``claim_issuer`` and
    # ``nonce_override`` above: each mints an ID-JAG that is well-formed but wrong
    # on exactly one axis, so a refusal cannot be mistaken for a parse failure.
    jag_issuer: str | None = None  # override to mint a wrong-iss grant
    jag_audience: str | None = None  # override to mint a wrong-aud grant
    jag_lifetime: int = 300  # seconds; zero or negative mints an expired grant
    #: Fault injection: the exchange endpoint refuses outright, so a caller's
    #: handling of an unreachable or unhappy identity provider can be exercised.
    exchange_should_reject: bool = False
    #: Mints an id_token whose ``exp`` is a numeric *string*. PyJWT accepts it, so
    #: the login succeeds, while a strict reader cannot bound the token — the shape
    #: of a real provider quirk reaching an optional subsystem.
    exp_as_string: bool = False
    exchange_calls: int = 0
    #: Requests that reached the end-user authorization endpoint. Enterprise-managed
    #: authorization exists to remove that step, so this counter staying at zero is
    #: the evidence, and the endpoint below is real so that zero is not vacuous.
    authorize_calls: int = 0

    def mint_id_token(
        self,
        subject: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        lifetime: int = 300,
    ) -> str:
        """Sign an id_token directly, bypassing the code flow.

        The code flow refuses to produce a token that is wrong on the issuer or
        audience axis — ``OidcClient`` validates both — so a caller that needs a
        *retained* assertion which is wrong on exactly one axis mints it here. The
        signature and key id are the real ones, so the only thing that differs from
        a genuine sign-in is the claim under test.
        """

        now = int(time.time())
        return jwt.encode(
            {
                "iss": issuer or self.claim_issuer or self.issuer,
                "aud": audience or self.audience,
                "sub": subject,
                "iat": now,
                "exp": now + lifetime,
            },
            _PRIVATE_KEY,
            algorithm="RS256",
            headers={"kid": _KID},
        )

    def issue_code(self, user: FakeUser, nonce: str) -> str:
        code = uuid.uuid4().hex
        self.users[user.subject] = user
        self.codes[code] = (user.subject, nonce)
        return code

    def app(self) -> FastAPI:
        idp = FastAPI()

        @idp.get("/.well-known/openid-configuration")
        async def discovery() -> JSONResponse:
            return JSONResponse(
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                }
            )

        @idp.get("/authorize")
        async def authorize(request: Request) -> Response:
            """The interactive step: consent, then a redirect carrying a code."""

            self.authorize_calls += 1
            params = dict(request.query_params)
            subject = params.get("sub", "alice")
            code = self.issue_code(
                FakeUser(subject, email=f"{subject}@test"), params.get("nonce", "")
            )
            location = (
                f"{params.get('redirect_uri', '')}?code={code}"
                f"&state={params.get('state', '')}"
            )
            return RedirectResponse(location, status_code=302)

        @idp.get("/jwks")
        async def jwks() -> JSONResponse:
            return JSONResponse({"keys": [_jwk()]})

        @idp.post("/token")
        async def token(request: Request) -> JSONResponse:
            form = parse_qs((await request.body()).decode())
            code = form.get("code", [""])[0]
            entry = self.codes.pop(code, None)
            if entry is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            subject, nonce = entry
            user = self.users[subject]
            now = int(time.time())
            claims: dict[str, Any] = {
                "iss": self.claim_issuer or self.issuer,
                "aud": self.audience,
                "sub": user.subject,
                "iat": now,
                "exp": str(now + 300) if self.exp_as_string else now + 300,
                "nonce": self.nonce_override or nonce,
            }
            if user.email:
                claims["email"] = user.email
            if user.name:
                claims["name"] = user.name
            id_token = jwt.encode(
                claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID}
            )
            return JSONResponse({"id_token": id_token, "token_type": "Bearer"})

        @idp.post("/token-exchange")
        async def token_exchange(request: Request) -> JSONResponse:
            self.exchange_calls += 1
            if self.exchange_should_reject:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            form = parse_qs((await request.body()).decode())
            if form.get("grant_type", [""])[0] != TOKEN_EXCHANGE_GRANT_TYPE:
                return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
            if form.get("requested_token_type", [""])[0] != ID_JAG_TOKEN_TYPE:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            subject_token = form.get("subject_token", [""])[0]
            try:
                subject_claims = jwt.decode(
                    subject_token,
                    _PRIVATE_KEY.public_key(),
                    algorithms=["RS256"],
                    audience=self.audience,
                    options={"verify_exp": True},
                )
            except jwt.PyJWTError:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            audience = self.jag_audience or form.get("audience", [""])[0]
            now = int(time.time())
            grant = jwt.encode(
                {
                    "iss": self.jag_issuer or self.issuer,
                    "aud": audience,
                    "sub": subject_claims["sub"],
                    "iat": now,
                    "exp": now + self.jag_lifetime,
                    "jti": uuid.uuid4().hex,
                },
                _PRIVATE_KEY,
                algorithm="RS256",
                headers={"kid": _KID, "typ": "oauth-id-jag+jwt"},
            )
            return JSONResponse(
                {
                    "access_token": grant,
                    "issued_token_type": ID_JAG_TOKEN_TYPE,
                    "token_type": "N_A",
                    "expires_in": self.jag_lifetime,
                }
            )

        return idp
