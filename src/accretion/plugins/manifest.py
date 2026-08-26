from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from accretion.contracts import MetaPluginManifest, PluginConnectorRequirement
from accretion.experience.embedding import canonical_digest
from accretion.plugins.errors import PluginManifestError

CANONICAL_CAPABILITY_ID = r"^[a-z][a-z0-9]*([._-][a-z0-9]+)*$"
"""Canonical capability identifiers (SDD 9.4). Stable across provider projections."""

_CAPABILITY_ID_MAX_LENGTH = 255

# The digest covers what the package *declares*, never when the objects were built.
# ``Capability`` and ``MetaSkill`` stamp ``created_at`` at construction time, and the
# detached ``signature`` is a claim about this digest, so all three are excluded.
_DIGEST_EXCLUDE: dict[str, Any] = {
    "signature": True,
    "capabilities": {"__all__": {"created_at"}},
    "skills": {"__all__": {"created_at"}},
}


def parse_manifest(payload: Mapping[str, Any] | str | bytes) -> MetaPluginManifest:
    """Parse and fully validate a package manifest.

    Accepts a mapping or raw JSON. YAML is deliberately unsupported: JSON keeps the
    manifest digest identical to the canonical-JSON convention used everywhere else.
    """

    if isinstance(payload, str | bytes):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise PluginManifestError(f"manifest is not valid JSON: {error}") from error
    else:
        decoded = dict(payload)
    if not isinstance(decoded, dict):
        raise PluginManifestError("manifest must be a JSON object")
    try:
        manifest = MetaPluginManifest.model_validate(decoded)
    except ValidationError as error:
        raise PluginManifestError(f"manifest failed schema validation: {error}") from error
    validate_manifest(manifest)
    return manifest


def canonical_manifest_digest(manifest: MetaPluginManifest) -> str:
    """Return the sha256 of the canonical JSON form of the manifest.

    Stable across processes: the detached ``signature`` (a claim about this very
    digest, so including it would be circular) and the construction-time
    ``created_at`` stamps on nested capabilities and skills are excluded.
    """

    return canonical_digest(manifest.model_dump(mode="json", exclude=_DIGEST_EXCLUDE))


def validate_manifest(manifest: MetaPluginManifest) -> None:
    """Apply the package shape rules the pydantic schema cannot express."""

    _reject_duplicates("capability", (item.capability_id for item in manifest.capabilities))
    _reject_duplicates("skill", (item.skill_id for item in manifest.skills))
    _reject_duplicates("verifier", manifest.verifiers)
    _reject_duplicates("policy", manifest.policies)
    _reject_duplicate_connectors(manifest.required_connectors)
    _reject_duplicate_connectors(manifest.optional_connectors)

    required = {item.connector_id for item in manifest.required_connectors}
    optional = {item.connector_id for item in manifest.optional_connectors}
    overlap = sorted(required & optional)
    if overlap:
        raise PluginManifestError(
            f"connector(s) declared both required and optional: {', '.join(overlap)}"
        )

    pattern = re.compile(CANONICAL_CAPABILITY_ID)
    for capability in manifest.capabilities:
        capability_id = capability.capability_id
        if not pattern.match(capability_id) or len(capability_id) > _CAPABILITY_ID_MAX_LENGTH:
            raise PluginManifestError(
                f"capability id {capability_id!r} is not a canonical capability id"
            )

    for provider, path in manifest.provider_projections.items():
        _validate_projection_path(provider, path)


def _reject_duplicates(kind: str, values: Iterable[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise PluginManifestError(f"duplicate {kind} id in manifest: {value}")
        seen.add(value)


def _reject_duplicate_connectors(requirements: list[PluginConnectorRequirement]) -> None:
    _reject_duplicates("connector", (item.connector_id for item in requirements))


def _validate_projection_path(provider: str, path: str) -> None:
    """Reject any provider projection path that could escape the package root."""

    if not path or path.strip() != path:
        raise PluginManifestError(f"provider projection for {provider!r} has an empty path")
    if "\\" in path or ":" in path:
        raise PluginManifestError(
            f"provider projection for {provider!r} must use posix separators: {path!r}"
        )
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or path.startswith("~"):
        raise PluginManifestError(
            f"provider projection for {provider!r} must be package-relative: {path!r}"
        )
    if ".." in candidate.parts:
        raise PluginManifestError(
            f"provider projection for {provider!r} escapes the package root: {path!r}"
        )
