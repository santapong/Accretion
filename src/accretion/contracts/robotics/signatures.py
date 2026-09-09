"""Detached safety signatures over a typed unsigned body, without a hash cycle.

Callers supply trusted key material explicitly. This module provisions no keys
and grants no policy, adapter, episode or physical authority.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from accretion.contracts.canonical import content_hash

from .models import SafetyDecisionPayload, SafetyDecisionReceipt
from .values import DetachedSignature

SAFETY_SIGNATURE_DOMAIN = "accretion.simulation.safety-decision.v1"


@dataclass(frozen=True, slots=True)
class TrustedSafetyKey:
    principal_id: str
    public_key: bytes


def safety_signing_bytes(payload: SafetyDecisionPayload) -> bytes:
    checked = SafetyDecisionPayload.model_validate(payload.model_dump(mode="python"))
    digest = content_hash(checked, exclude=())
    return SAFETY_SIGNATURE_DOMAIN.encode("ascii") + b"\0" + digest.encode("ascii")


def sign_safety_payload(
    payload: SafetyDecisionPayload, *, key_id: str, private_key: Ed25519PrivateKey
) -> DetachedSignature:
    return DetachedSignature(
        key_id=key_id,
        signer_principal_id=payload.evaluator_principal_id,
        unsigned_payload_digest=content_hash(payload, exclude=()),
        signature_base64=base64.b64encode(private_key.sign(safety_signing_bytes(payload))).decode(),
    )


def verify_safety_signature(
    receipt: SafetyDecisionReceipt, *, trusted_keys: Mapping[str, TrustedSafetyKey]
) -> None:
    """Raise on untrusted signer, altered sealed receipt, or invalid signature.

    A successful signature check authenticates this safety statement; the
    execution boundary must still validate expiry, lease, policy and exact pins.
    """
    checked = SafetyDecisionReceipt.model_validate(receipt.model_dump(mode="python"))
    signature = checked.signature
    key = trusted_keys.get(signature.key_id)
    if key is None or key.principal_id != signature.signer_principal_id:
        raise ValueError("safety signer is not trusted for this principal")
    try:
        raw = base64.b64decode(signature.signature_base64, validate=True)
        Ed25519PublicKey.from_public_bytes(key.public_key).verify(
            raw, safety_signing_bytes(checked.decision)
        )
    except (binascii.Error, ValueError, InvalidSignature) as exc:
        raise ValueError("safety signature verification failed") from exc
