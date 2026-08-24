"""Session middleware and auth runtime for the control-plane API (v0.3 M1).

``LOCAL_PRINCIPAL`` mode (the default) attaches a deterministic local
principal to every request so single-user local operation needs no identity
provider and existing behavior is unchanged. ``OIDC`` mode requires a valid
session cookie on every non-exempt ``/api/v1`` route. In both modes a
DISABLED principal is refused before any capability-invoking route runs
(AC3-ID-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import httpx
from fastapi import FastAPI, Request

from accretion.config import Settings, get_settings
from accretion.contracts import Principal, PrincipalStatus
from accretion.identity import (
    AuthenticationError,
    AuthorizationError,
    IdentityService,
    OidcClient,
    OidcProviderConfig,
)
from accretion.persistence.store import StateStore

_EXEMPT_PREFIXES = ("/api/v1/auth/",)
_EXEMPT_PATHS = {"/healthz", "/docs", "/openapi.json", "/redoc"}


@dataclass
class AuthRuntime:
    mode: str
    identity: IdentityService
    cookie_name: str
    cookie_secure: bool
    session_ttl_seconds: int
    local_principal_cache: Principal | None = None


def build_auth_runtime(store: StateStore, settings: Settings) -> AuthRuntime:
    oidc: OidcClient | None = None
    if settings.auth_mode == "OIDC":
        oidc = OidcClient(
            config=OidcProviderConfig(
                issuer=settings.oidc_issuer,
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret,
                redirect_url=settings.oidc_redirect_url,
                scopes=settings.oidc_scopes,
            ),
            http=httpx.AsyncClient(),
        )
    identity = IdentityService(
        store,
        oidc,
        session_ttl_seconds=settings.session_ttl_seconds,
        local_subject=settings.operator_identity,
    )
    return AuthRuntime(
        mode=settings.auth_mode,
        identity=identity,
        cookie_name=settings.session_cookie_name,
        cookie_secure=settings.environment not in {"development", "local", "test"},
        session_ttl_seconds=settings.session_ttl_seconds,
    )


def auth_runtime(app: FastAPI) -> AuthRuntime:
    runtime = getattr(app.state, "auth", None)
    if runtime is None:
        # Tests construct the app without running the lifespan; default to
        # LOCAL_PRINCIPAL mode over whatever store the manager carries.
        store = app.state.manager.store
        runtime = build_auth_runtime(store, get_settings())
        app.state.auth = runtime
    return cast(AuthRuntime, runtime)


def principal(request: Request) -> Principal:
    return cast(Principal, request.state.principal)


def is_exempt(path: str) -> bool:
    return path in _EXEMPT_PATHS or any(path.startswith(p) for p in _EXEMPT_PREFIXES)


async def authenticate_request(request: Request) -> Principal:
    runtime = auth_runtime(request.app)
    if runtime.mode == "LOCAL_PRINCIPAL":
        if runtime.local_principal_cache is None:
            runtime.local_principal_cache = await runtime.identity.local_principal()
        current = await runtime.identity.store.get_principal(
            runtime.local_principal_cache.principal_id
        )
        resolved = current or runtime.local_principal_cache
    else:
        auth_session_id = request.cookies.get(runtime.cookie_name)
        if not auth_session_id:
            raise AuthenticationError("authentication required")
        session_principal = await runtime.identity.resolve_session(auth_session_id)
        if session_principal is None:
            raise AuthenticationError("session is invalid or expired")
        resolved = session_principal
    if resolved.status is PrincipalStatus.DISABLED:
        raise AuthorizationError("principal is disabled")
    return resolved
