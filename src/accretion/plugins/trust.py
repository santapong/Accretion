from __future__ import annotations

import base64
import binascii
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from accretion.contracts import (
    MetaPluginManifest,
    PluginSignatureAlgorithm,
    PluginTrustLevel,
    RiskLevel,
)
from accretion.plugins.errors import PluginSignatureError, PluginTrustError
from accretion.plugins.manifest import canonical_manifest_digest

TRUST_RANK: dict[PluginTrustLevel, int] = {
    PluginTrustLevel.BLOCKED: 0,
    PluginTrustLevel.UNVERIFIED_DEV: 1,
    PluginTrustLevel.WORKSPACE_APPROVED: 2,
    PluginTrustLevel.SIGNED_THIRD_PARTY: 3,
    PluginTrustLevel.BUILTIN: 4,
}

_RISK_MIN_TRUST: dict[RiskLevel, PluginTrustLevel] = {
    RiskLevel.LOW: PluginTrustLevel.UNVERIFIED_DEV,
    RiskLevel.MEDIUM: PluginTrustLevel.UNVERIFIED_DEV,
    RiskLevel.HIGH: PluginTrustLevel.WORKSPACE_APPROVED,
    RiskLevel.CRITICAL: PluginTrustLevel.SIGNED_THIRD_PARTY,
}

_SIGNING_LEVELS = frozenset(
    {PluginTrustLevel.WORKSPACE_APPROVED, PluginTrustLevel.SIGNED_THIRD_PARTY}
)


def min_trust_for_risk(risk: RiskLevel) -> PluginTrustLevel:
    """Return the lowest trust level allowed to carry a capability at ``risk`` (SDD 19.3)."""

    return _RISK_MIN_TRUST[risk]


def manifest_min_trust(manifest: MetaPluginManifest) -> PluginTrustLevel:
    """The strongest requirement across every capability the manifest declares."""

    required = PluginTrustLevel.UNVERIFIED_DEV
    for capability in manifest.capabilities:
        candidate = min_trust_for_risk(capability.risk)
        if TRUST_RANK[candidate] > TRUST_RANK[required]:
            required = candidate
    return required


def satisfies(actual: PluginTrustLevel, required: PluginTrustLevel) -> bool:
    if actual is PluginTrustLevel.BLOCKED:
        return False
    return TRUST_RANK[actual] >= TRUST_RANK[required]


@dataclass(frozen=True, slots=True)
class PluginTrustedKey:
    """An operator-configured Ed25519 verification key and the level it confers."""

    key_id: str
    public_key: bytes
    trust_level: PluginTrustLevel = PluginTrustLevel.SIGNED_THIRD_PARTY


def load_trusted_keys(raw: Mapping[str, str]) -> dict[str, PluginTrustedKey]:
    """Parse ``{key_id: "[<TRUST_LEVEL>:]<base64 32-byte public key>"}`` from settings."""

    keys: dict[str, PluginTrustedKey] = {}
    for key_id, value in raw.items():
        level = PluginTrustLevel.SIGNED_THIRD_PARTY
        encoded = value.strip()
        if ":" in encoded:
            prefix, _, remainder = encoded.partition(":")
            try:
                level = PluginTrustLevel(prefix.strip().upper())
            except ValueError as error:
                raise PluginTrustError(
                    f"trusted key {key_id!r} names an unknown trust level {prefix!r}"
                ) from error
            encoded = remainder.strip()
        if level not in _SIGNING_LEVELS:
            raise PluginTrustError(
                f"trusted key {key_id!r} may only confer WORKSPACE_APPROVED "
                f"or SIGNED_THIRD_PARTY, not {level.value}"
            )
        keys[key_id] = PluginTrustedKey(key_id, _decode_key(key_id, encoded), level)
    return keys


def _decode_key(key_id: str, encoded: str) -> bytes:
    try:
        material = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise PluginTrustError(f"trusted key {key_id!r} is not valid base64") from error
    if len(material) != 32:
        raise PluginTrustError(
            f"trusted key {key_id!r} must be a 32-byte Ed25519 public key, got {len(material)}"
        )
    return material


class PluginTrustVerifier:
    """Establishes the trust level a package has actually earned.

    Bundled builtins and unsigned development packages are pinned by digest; anything
    claiming WORKSPACE_APPROVED or SIGNED_THIRD_PARTY must carry a detached Ed25519
    signature over that same digest, made by an operator-configured key.
    """

    def __init__(
        self,
        *,
        trusted_keys: Mapping[str, PluginTrustedKey] | None = None,
        allow_unverified_dev: bool = False,
        builtin_ids: Sequence[str] = (),
    ) -> None:
        self.trusted_keys = dict(trusted_keys or {})
        self.allow_unverified_dev = allow_unverified_dev
        self.builtin_ids = frozenset(builtin_ids)

    def verify(
        self,
        manifest: MetaPluginManifest,
        *,
        expected_digest: str | None = None,
    ) -> PluginTrustLevel:
        """Return the earned trust level, or raise. Never returns ``BLOCKED``."""

        digest = canonical_manifest_digest(manifest)
        if expected_digest is not None and not hmac.compare_digest(digest, expected_digest):
            raise PluginSignatureError(
                f"manifest digest {digest} does not match the pinned {expected_digest}"
            )

        signature = manifest.signature
        if manifest.id in self.builtin_ids:
            if signature is not None:
                self._verify_pin(digest, signature.algorithm, signature.value)
            return PluginTrustLevel.BUILTIN

        if signature is None:
            return self._unverified(manifest, reason="the manifest carries no signature")

        if signature.algorithm is PluginSignatureAlgorithm.SHA256_PIN:
            self._verify_pin(digest, signature.algorithm, signature.value)
            return self._unverified(
                manifest, reason="a SHA256_PIN only pins content, it does not attest authorship"
            )

        key = self.trusted_keys.get(signature.key_id)
        if key is None:
            raise PluginTrustError(
                f"plugin {manifest.id} is signed by unknown key {signature.key_id!r}"
            )
        try:
            raw = base64.b64decode(signature.value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise PluginSignatureError(
                f"plugin {manifest.id} signature is not valid base64"
            ) from error
        try:
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(raw, digest.encode())
        except InvalidSignature as error:
            raise PluginSignatureError(
                f"plugin {manifest.id} signature does not verify against key {key.key_id!r}"
            ) from error
        return key.trust_level

    def verify_for_install(
        self,
        manifest: MetaPluginManifest,
        *,
        expected_digest: str | None = None,
    ) -> PluginTrustLevel:
        """Verify, then enforce the risk-to-trust floor the manifest's capabilities imply."""

        actual = self.verify(manifest, expected_digest=expected_digest)
        required = manifest_min_trust(manifest)
        if not satisfies(actual, required):
            raise PluginTrustError(
                f"plugin {manifest.id} needs {required.value} for its declared capability "
                f"risk but only reached {actual.value}"
            )
        return actual

    def _verify_pin(
        self, digest: str, algorithm: PluginSignatureAlgorithm, value: str
    ) -> None:
        if algorithm is not PluginSignatureAlgorithm.SHA256_PIN:
            raise PluginSignatureError(f"expected a SHA256_PIN, got {algorithm.value}")
        if not hmac.compare_digest(digest, value.strip().lower()):
            raise PluginSignatureError(f"pinned digest {value} does not match manifest {digest}")

    def _unverified(self, manifest: MetaPluginManifest, *, reason: str) -> PluginTrustLevel:
        if not self.allow_unverified_dev:
            raise PluginTrustError(
                f"plugin {manifest.id} would install as UNVERIFIED_DEV because {reason}; "
                "set ACCRETION_PLUGIN_ALLOW_UNVERIFIED_DEV=true to permit that"
            )
        return PluginTrustLevel.UNVERIFIED_DEV
