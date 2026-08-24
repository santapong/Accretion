"""Encrypted secret store for broker-held credentials (v0.3 M2, SDD §13.3).

SDD §13.3 prefers an OS keyring and names application-level envelope encryption as
the acceptable development fallback. This module ships the fallback behind the
``SecretStore`` protocol OQ3-02 requires, so a keyring or KMS backend can replace it
without touching callers or stored rows.

The master key lives outside PostgreSQL, per §13.3. Nothing here logs, formats, or
returns plaintext, and there is deliberately no plaintext code path when the key is
missing or malformed — an unusable key fails closed.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from accretion.ids import new_id

_KEY_BYTES = 32
_NONCE_BYTES = 12
_ENV_KEY = "ACCRETION_TOKEN_ENCRYPTION_KEY"


class SecretStoreError(RuntimeError):
    """Secret storage failed; never carries key or plaintext material."""


class KeyProvider(Protocol):
    """Supplies the master key. The seam for an OS keyring or KMS backend."""

    @property
    def key_id(self) -> str: ...

    def material(self) -> bytes: ...


@dataclass(frozen=True)
class EnvironmentKeyProvider:
    """Reads a base64 32-byte master key from the environment.

    The development fallback of SDD §13.3: outside PostgreSQL, but only as private
    as the process environment.
    """

    variable: str = _ENV_KEY
    key_id: str = "env-1"

    def material(self) -> bytes:
        raw = os.environ.get(self.variable, "")
        if not raw:
            raise SecretStoreError(
                f"{self.variable} is not set; the token broker cannot store credentials"
            )
        try:
            material = base64.urlsafe_b64decode(raw)
        except (ValueError, TypeError) as exc:
            raise SecretStoreError(f"{self.variable} is not valid base64") from exc
        if len(material) != _KEY_BYTES:
            raise SecretStoreError(
                f"{self.variable} must decode to {_KEY_BYTES} bytes, got {len(material)}"
            )
        return material


@dataclass(frozen=True)
class SecretRecord:
    """Ciphertext plus the metadata needed to open it. Never holds plaintext."""

    secret_store_key: str
    key_id: str
    nonce: str
    ciphertext: str


class SecretStore(Protocol):
    async def seal(self, plaintext: str, *, associated_id: str) -> SecretRecord: ...

    async def open(self, record: SecretRecord, *, associated_id: str) -> str: ...


class EnvelopeSecretStore:
    """AES-256-GCM envelope encryption.

    ``associated_id`` is bound as additional authenticated data, so a ciphertext
    lifted from one token handle cannot be opened under another.
    """

    def __init__(self, keys: KeyProvider | None = None) -> None:
        self.keys: KeyProvider = keys or EnvironmentKeyProvider()

    async def seal(self, plaintext: str, *, associated_id: str) -> SecretRecord:
        nonce = os.urandom(_NONCE_BYTES)
        cipher = AESGCM(self.keys.material())
        sealed = cipher.encrypt(nonce, plaintext.encode(), associated_id.encode())
        return SecretRecord(
            secret_store_key=new_id("secret_record"),
            key_id=self.keys.key_id,
            nonce=base64.urlsafe_b64encode(nonce).decode(),
            ciphertext=base64.urlsafe_b64encode(sealed).decode(),
        )

    async def open(self, record: SecretRecord, *, associated_id: str) -> str:
        if record.key_id != self.keys.key_id:
            raise SecretStoreError(
                f"secret was sealed with key {record.key_id!r}, which is not available"
            )
        cipher = AESGCM(self.keys.material())
        try:
            opened = cipher.decrypt(
                base64.urlsafe_b64decode(record.nonce),
                base64.urlsafe_b64decode(record.ciphertext),
                associated_id.encode(),
            )
        except (InvalidTag, ValueError) as exc:
            # Wrong key, tampered ciphertext, or a transplanted record.
            raise SecretStoreError("secret could not be opened") from exc
        return opened.decode()


def generate_master_key() -> str:
    """Mint a base64 master key for ACCRETION_TOKEN_ENCRYPTION_KEY."""

    return base64.urlsafe_b64encode(os.urandom(_KEY_BYTES)).decode()
