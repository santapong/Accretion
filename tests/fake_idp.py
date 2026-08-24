"""In-process OIDC identity provider for M1 tests.

Serves discovery, JWKS, and a token endpoint that signs RS256 id_tokens.
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
from fastapi.responses import JSONResponse

ISSUER = "https://idp.test"
CLIENT_ID = "accretion-test-client"

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
                "exp": now + 300,
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

        return idp
