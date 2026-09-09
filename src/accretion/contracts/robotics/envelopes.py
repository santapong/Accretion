"""Preserve verified writer JSON independently of a lossy reader projection.

New robotics writers emit canonical JSON with their complete field set. This
boundary verifies that exact writer seal before any model projection. Historical
v0.4 non-canonical wire spellings keep their existing read boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from accretion.contracts.canonical import CanonicalContract, canonical_json, content_hash
from accretion.contracts.upcast import UPCAST_DROPPED_KEYS_LABEL, upcast


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("writer envelope contains a duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class CanonicalWriterEnvelope:
    """Immutable original JSON. Returning a dict always returns a fresh copy."""

    original_json: str

    def __post_init__(self) -> None:
        payload = self.payload()
        required = {
            "contract_type",
            "schema_version",
            "contract_id",
            "content_hash",
            "created_at",
            "created_by",
            "workspace_id",
            "project_id",
        }
        if not required <= payload.keys():
            raise ValueError("writer envelope requires the complete canonical header")
        version = payload["schema_version"]
        if (
            not isinstance(version, str)
            or len(version.split(".")) != 3
            or any(not part.isascii() or not part.isdigit() for part in version.split("."))
            or int(version.split(".")[0]) != 1
        ):
            raise ValueError("writer envelope rejects an unknown or malformed major version")
        digest = payload["content_hash"]
        if not isinstance(digest, str) or len(digest) != 64 or digest != content_hash(payload):
            raise ValueError("writer envelope requires the original verified content seal")

    @classmethod
    def from_contract(cls, contract: CanonicalContract) -> CanonicalWriterEnvelope:
        if UPCAST_DROPPED_KEYS_LABEL in contract.labels:
            raise ValueError("a lossy reader projection cannot become a writer envelope")
        # Revalidate a dict: Pydantic may trust an existing model instance, and a
        # caller may have mutated its nested values after the initial sealing.
        checked = type(contract).model_validate(contract.model_dump(mode="python"))
        return cls(canonical_json(checked).decode("utf-8"))

    def payload(self) -> dict[str, Any]:
        value: Any = json.loads(self.original_json, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("writer envelope must contain a JSON object")
        return value

    @property
    def writer_content_hash(self) -> str:
        value: str = self.payload()["content_hash"]
        return value

    def forward_json(self) -> str:
        """Forward original unknown fields and seal; never forward the projection."""
        return self.original_json

    def read_projection[C: CanonicalContract](self, model: type[C]) -> C:
        payload = self.payload()
        if payload["contract_type"] != model.CONTRACT_TYPE:
            raise ValueError("writer contract type does not match the requested reader")
        return upcast(payload, model)

    def for_execution[C: CanonicalContract](self, model: type[C]) -> C:
        payload = self.payload()
        if payload["schema_version"] != "1.0.0":
            raise ValueError("execution requires the fully understood robotics writer version")
        if UPCAST_DROPPED_KEYS_LABEL in payload.get("labels", {}):
            raise ValueError("a lossy reader projection cannot authorize execution")
        # Strict nested parsing and the original seal remain mandatory; this path
        # never calls upcast or re-seals a lossy projection into authority.
        return model.model_validate(payload)
