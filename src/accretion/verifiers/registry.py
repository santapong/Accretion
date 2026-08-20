from __future__ import annotations

from collections.abc import Iterable

from accretion.verifiers.base import Verifier


class VerifierUnavailableError(LookupError):
    pass


class VerifierRegistry:
    """Versioned registry populated only from trusted application configuration."""

    def __init__(self, verifiers: Iterable[Verifier] = ()) -> None:
        self._verifiers: dict[str, Verifier] = {}
        for verifier in verifiers:
            self.register(verifier)

    def register(self, verifier: Verifier) -> None:
        if verifier.verifier_id in self._verifiers:
            raise ValueError(f"verifier {verifier.verifier_id!r} is already registered")
        self._verifiers[verifier.verifier_id] = verifier

    def get(self, verifier_id: str) -> Verifier:
        try:
            return self._verifiers[verifier_id]
        except KeyError as exc:
            raise VerifierUnavailableError(f"verifier {verifier_id!r} is unavailable") from exc

    def resolve(self, verifier_ids: Iterable[str]) -> list[Verifier]:
        return [self.get(verifier_id) for verifier_id in verifier_ids]

    def list_ids(self) -> list[str]:
        return sorted(self._verifiers)
