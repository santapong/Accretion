"""The cross-release typed references (registry §4; ADR-054).

Registry §3.1 requires that "frames, clocks, environments, artifacts, runtimes, models,
tools, skills, capabilities, adapters, verifiers, and policies use immutable typed
references". A typed reference is not a convenience wrapper around a string id: it is the
statement that a contract committed to *one specific thing*, which is why every reference
here pins a digest or a version alongside the id. An id alone is a mutable pointer — the
skill behind ``skl_...`` can be republished, the environment image behind an environment id
can be rebuilt — and a routing decision, an approval or a verification receipt that pointed
only at the id would silently start meaning something else. Pinning the digest is what makes
a receipt replayable years later, and it is why aliases (registry §4's closing line) must be
resolved to these types *before* routing, planning, approval or execution rather than after.

**Four references already exist and are reused, never redefined** (ADR-054 f). They live in
the package root, :mod:`accretion.contracts`, and this module deliberately does not restate
them:

* ``PrincipalRef`` — issuer/subject/workspace identity (v0.3 M1).
* ``PluginRef`` — the locked ``(plugin_id, version, manifest_digest)`` triple (v0.3 M4).
* ``ConnectionRef`` — the opaque connection handle that never carries a token (v0.3 M0).
* ``ArtifactRef`` — execution artifacts, whose ``run_id`` stays **required** because the
  reference is persisted inside execution traces and an artifact with no run is not a thing
  this system can produce. That is exactly why approval receipts, which are not run-scoped,
  get the separate :class:`ApprovalArtifactRef` below instead of a loosened ``ArtifactRef``.

Everything defined here is new. Nothing here is re-exported from the package root (ADR-053):
callers import from :mod:`accretion.contracts.refs` explicitly, so that a v0.4 name can never
be mistaken for one of the v0.1-v0.3 names that the root re-exports across the whole codebase.

Two conventions run through the module. Digest fields are lowercase hexadecimal SHA-256 —
the same 64-character pattern ``PluginRef.manifest_digest`` and ``EvidenceCandidate``
already use, so a digest computed anywhere in the repository is comparable everywhere.
Identity strings carry ``min_length=1``, because an empty id is not an identity and a
reference that cannot say what it points at should fail at construction rather than at the
first join. The models are not frozen: immutability here is a property of the *referent*,
established by the pinned digest, not of the Python object.
"""

from __future__ import annotations

from pydantic import Field

from accretion.contracts import EvidenceClass, Provider, StrictModel

# One place for the digest shape, so a reference cannot drift from the digests the rest of
# the repository writes. Lowercase because that is what `hashlib.hexdigest()` produces, and
# accepting both cases would let two spellings of the same digest compare unequal.
_DIGEST = r"^[0-9a-f]{64}$"


class RuntimeRef(StrictModel):
    """A specific agent runtime, pinned well enough to explain a past decision.

    Registry §4 requires "runtime ID, adapter version, provider/model capability profile".
    The adapter version matters because the same provider behind a newer adapter is a
    different execution surface, and a router that learned on one must not silently claim
    its evidence transfers to the other.

    ``model`` is optional and defaults to ``None``: a subscription-mode CLI runtime does not
    always pin a model, and forcing a placeholder there would put a lie in the receipt. It is
    ``None`` or a real name and never the empty string, so that "unpinned" has one spelling.
    ``capability_profile_digest`` is the digest over the runtime's declared capability
    profile, and it is what makes this reference immutable — provider and model are names,
    the profile digest is the thing that actually changed when behaviour changed.
    """

    runtime_id: str = Field(min_length=1, max_length=255)
    adapter_version: str = Field(min_length=1, max_length=64)
    provider: Provider
    model: str | None = Field(default=None, min_length=1, max_length=255)
    capability_profile_digest: str = Field(pattern=_DIGEST)


class CapabilityRef(StrictModel):
    """A capability at one schema version.

    Registry §4 asks for "canonical capability ID and schema version".

    The version field is spelled ``capability_schema_version`` rather than
    ``schema_version`` on purpose. Every persisted aggregate in this repository opens with a
    ``schema_version`` describing *its own* contract, and a reference whose ``schema_version``
    described something else would be a trap for every reader and every hash. This field is
    the version of the capability's declared input/output schema — the value the repository
    already carries as ``Capability.version`` and ``CapabilityRequest.capability_version``.
    """

    capability_id: str = Field(min_length=1, max_length=255)
    capability_schema_version: str = Field(min_length=1, max_length=64)


class ToolRef(StrictModel):
    """A tool by normalized id plus the digest of the implementation that ran.

    "Normalized" is the load-bearing word: the same underlying tool reached through two
    connectors, or spelled differently by two providers, must arrive here as one id, or
    experience gathered under one spelling will never be found under the other.
    """

    tool_id: str = Field(min_length=1, max_length=255)
    implementation_digest: str = Field(pattern=_DIGEST)


class SkillRef(StrictModel):
    """A skill by id, version and package digest.

    Version and digest are both required and are not redundant: the version is what a human
    asked for and what a plan cites, the digest is what actually shipped. A republished
    package under an unchanged version is precisely the case this pair exists to catch.
    """

    skill_id: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    package_digest: str = Field(pattern=_DIGEST)


class EnvironmentRef(StrictModel):
    """An execution environment: id, image digest, policy profile (registry §4).

    The policy profile travels *with* the environment rather than beside it because an
    environment is only half an answer — the same image under a permissive profile and under
    a restricted one are different places to run, and a safety argument about one says
    nothing about the other.
    """

    environment_id: str = Field(min_length=1, max_length=255)
    image_digest: str = Field(pattern=_DIGEST)
    policy_profile: str = Field(min_length=1, max_length=255)


class VerifierRef(StrictModel):
    """A verifier's contract plus the digest of the implementation that produced a result.

    ``verifier_contract_id`` names the verifier *contract* — what is checked and what
    counts as passing — and not a single v0.1 verification run. Registry §19 requires that
    verification stay reproducible, which needs both halves: the contract explains what the
    verdict claimed, the implementation digest explains what code made the claim.
    """

    verifier_contract_id: str = Field(min_length=1, max_length=255)
    implementation_digest: str = Field(pattern=_DIGEST)


class EvidenceRef(StrictModel):
    """A piece of evidence by id, class and content digest (registry §4).

    ``evidence_class`` reuses the existing :class:`~accretion.contracts.EvidenceClass` enum
    (ADR-054 e): the v0.3 M5 taxonomy already equals registry §5.2, so there is exactly one
    definition of what ``SIMULATION`` means. The class is carried *in the reference* rather
    than looked up behind it because registry §19 requires simulation and physical evidence to
    stay type-distinct at every boundary — a consumer holding this reference can refuse to
    treat simulated evidence as physical without dereferencing anything.

    The class is required and has no default. The stored ``EvidenceRecord`` defaults to
    ``EXTERNAL_SOURCE`` for candidates arriving from a connector; a reference is written by
    something that already knows, and defaulting here would let an unstated class quietly
    become the weakest one.
    """

    evidence_id: str = Field(min_length=1, max_length=255)
    evidence_class: EvidenceClass
    content_digest: str = Field(pattern=_DIGEST)


class PolicyRef(StrictModel):
    """A policy by id, version and content digest (registry §4).

    Authority decisions are audited against the exact policy text that produced them, so the
    content digest is required: "policy v3" is a label, and labels are editable.
    """

    policy_id: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    content_digest: str = Field(pattern=_DIGEST)


class ApprovalArtifactRef(StrictModel):
    """A content-addressed artifact attached to an approval receipt (ADR-054 f).

    Distinct from ``ArtifactRef`` and deliberately not a relaxation of it. ``ArtifactRef`` is
    run-scoped — it carries a required ``run_id`` and a filesystem path because it names
    something a run produced — and approvals are not run-scoped: a human approves against a
    document, a screenshot or a signed statement that may predate any run. Widening
    ``ArtifactRef`` to fit would have made ``run_id`` optional and quietly weakened every
    execution trace that depends on it, which registry §3.2 classifies as a Major change to
    identity semantics. Two types, each honest about its own scope, is the cheaper answer.

    ``uri`` must carry a scheme so that the value is dereferenceable rather than a bare path
    whose meaning depends on where it is read. ``digest`` is what makes the reference
    content-addressed: the receipt commits to the bytes, not to the location, and a substituted
    document at the same URI fails the comparison. ``media_type`` is a bare ``type/subtype``
    with no parameters, because ``text/plain`` and ``text/plain; charset=utf-8`` would
    otherwise be two spellings of one value inside a hash input.

    ``retention_class`` is a constrained string rather than an enum, and that is a considered
    gap. Registry §3 lists ``retention_class: canonical-enum`` among the optional header
    fields, but registry §5 — which is where stable enums live — defines no retention
    vocabulary, and §20 schedules none. Freezing a guessed set of values here would create
    exactly the duplicate source of truth registry §21 forbids, to be reconciled against
    whatever the v0.6 approval and safety work actually needs. Until the registry names the
    values, this field records the class the caller declares.
    """

    uri: str = Field(min_length=1, max_length=2_048, pattern=r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
    digest: str = Field(pattern=_DIGEST)
    media_type: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$",
    )
    retention_class: str = Field(min_length=1, max_length=64)
