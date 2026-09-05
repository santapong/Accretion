"""The one surviving copy of the pre-v0.4 JSON digest expression (M0 debt, M8).

ADR-056 says canonical serialization is implemented once, in
:mod:`accretion.contracts.canonical`, and from M0 every *new* contract obeys that. Seven
older call sites predate the rule and hand-rolled their own
``json.dumps(payload, sort_keys=True, separators=(",", ":"))``. M8 converged the three
whose bytes are provably unchanged and left the other four exactly where they were — but
with one copy of the old expression instead of four, which is what this module is.

**Converged in M8** (they no longer import anything from here):

* ``experience/embedding.py`` ``canonical_digest`` — it already passed
  ``ensure_ascii=False``, so it agrees with :func:`~accretion.contracts.canonical.canonical_json`
  byte for byte on *every* payload `json.dumps` would accept, not merely on the committed
  ones. Nothing about its persisted digests (`ExperienceEmbedding.input_digest`,
  segment ``content_digest``, the bundled plugin manifest digests) can move.
* ``governance.py`` ``seed_governance`` — the built-in
  ``accretion-core-governance@1.0.0`` manifest checksum. Its payload is four code
  literals: the plugin id, the version, the two built-in capability ids and the one
  built-in skill id. The domain is closed and entirely ASCII, so the digest is a constant
  and it is the same constant either way. That matters more here than anywhere else,
  because ``upsert_plugin`` refuses any drift for an existing ``(plugin_id, version)``
  and a moved checksum would make the next ``seed_governance`` fail on every deployment
  that already ran.
* ``live_sample.py`` — not a digest at all. It serializes the expected artifact into the
  prompt text that asks a provider to write ``result.json``, and ``verify_artifact``
  compares *parsed* objects, so the escaping of that prompt cannot change any verdict or
  any recorded ``artifact_sha256``.

**Still legacy, and routed through** :func:`legacy_json_digest` **here.** Each one hashes a
payload whose text is open at runtime — user arguments, planner prose, or a remote
server's own metadata — so a non-ASCII value would hash differently under
``ensure_ascii=False``, and in every case that digest is already persisted and already
compared against values earlier releases wrote:

* ``governance.py`` ``approval_binding`` — ``CapabilityRequest.arguments`` is arbitrary
  caller-supplied JSON, and the digest becomes the ``native_request_id`` an approval is
  matched by.
* ``templates.py`` ``compute_template_checksum`` — a template body carries free text, and
  ``orchestration/materialize.py`` builds one from a planner proposal. The checksum is
  persisted and re-verified at load and at run start.
* ``mcp/manager.py`` — the discovery snapshot digest covers ``server_info``, tool
  descriptions, resource names and prompt descriptions supplied by a *remote* server,
  which is the most likely non-ASCII payload in the repository. It is persisted as
  ``McpDiscoverySnapshot.content_sha256``.
* ``orchestration/validator.py`` ``normalized_hash`` — the normalized graph digest covers
  ``DynamicWorkflowNodeSpec.objective``, four thousand characters of free planner text.
  It is persisted as ``GraphValidationResult.normalized_graph_hash``.

Converging those four is not a refactor but a rehash-and-migrate story, and it stays
scheduled with the read-boundary upcaster (ADR-057). Until then the rule is the narrow one
M0 recorded: nothing compares a digest produced here against a digest produced by
:mod:`accretion.contracts.canonical`.

The separators are named in :data:`_LEGACY_SEPARATORS` for the same reason
``canonical.py`` names its own: it is the rule a mutation breaks most quietly, and the
pinned digests in ``tests/test_v04_m8_digests.py`` exist to make that break loud.
"""

from __future__ import annotations

import hashlib
import json

# The pre-v0.4 spelling, kept deliberately: no space after either delimiter. Identical to
# the canonical module's constant, and separate from it on purpose — these bytes are
# frozen by persisted checksums, so they must not follow a future canonical edit.
_LEGACY_SEPARATORS = (",", ":")


def legacy_json_bytes(payload: object) -> bytes:
    """Serialize ``payload`` exactly as the pre-v0.4 digest sites did.

    ``ensure_ascii`` is left at its `json` default of ``True``, which is the whole
    difference from :func:`~accretion.contracts.canonical.canonical_json`: a non-ASCII
    character is emitted as a ``\\uXXXX`` escape rather than as itself. That is not a
    preference, it is the byte shape the persisted digests listed in this module's
    docstring were computed over.

    Exposed beside :func:`legacy_json_digest` so that the byte-equality tests can compare
    these bytes to the canonical ones without writing a second copy of the expression —
    which is the duplication this module exists to remove.
    """

    return json.dumps(payload, sort_keys=True, separators=_LEGACY_SEPARATORS).encode()


def legacy_json_digest(payload: object) -> str:
    """Return the SHA-256 hex digest of ``payload`` in the pre-v0.4 byte form.

    The single entry point for the four sites that stay byte-frozen. Callers that need a
    digest over a *new* v0.4 contract must use
    :func:`accretion.contracts.canonical.content_hash` instead; the two are not
    interchangeable and no code may compare their outputs.
    """

    return hashlib.sha256(legacy_json_bytes(payload)).hexdigest()
