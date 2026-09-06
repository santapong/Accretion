"""Stable failures exposed by the v0.4 node-routing boundary."""

from __future__ import annotations


class RoutingError(RuntimeError):
    """A routing refusal with an API-safe code, message and HTTP status.

    Services raise this only after replacing persistence details and inaccessible
    resource identities with a public message.  Keeping the status on the failure
    lets the HTTP adapter remain a thin projection of the routing service rather
    than duplicating its authorization and conflict rules.
    """

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


__all__ = ["RoutingError"]
