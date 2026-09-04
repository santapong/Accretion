"""The cross-release typed references (registry §4; ADR-053, ADR-054).

Two things are under test. First, that each new reference actually requires the identity
the registry names — a reference that can be built without its digest is a mutable pointer
wearing a type. Second, that the package conversion (``contracts.py`` →
``contracts/__init__.py``) left every existing dotted import working, and that the four
reused references were not quietly twinned inside :mod:`accretion.contracts.refs`.

The ``VALID`` table below drives the missing-field and unknown-field cases for every
reference, and a completeness test asserts the table covers every model the module defines,
so adding a reference without adding it here is a red test rather than an untested class.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from accretion.contracts import (
    ArtifactRef,
    ConnectionRef,
    EvidenceClass,
    PluginRef,
    PrincipalRef,
    Provider,
    RiskLevel,
    StrictModel,
    VerificationResult,
    VerificationStatus,
)
from accretion.contracts import refs as refs_module
from accretion.contracts.canonical import content_hash
from accretion.contracts.refs import (
    ApprovalArtifactRef,
    CapabilityRef,
    EnvironmentRef,
    EvidenceRef,
    PolicyRef,
    RuntimeRef,
    SkillRef,
    ToolRef,
    VerifierRef,
)

DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
OTHER_DIGEST = "5" * 64

VALID: dict[type[StrictModel], dict[str, Any]] = {
    RuntimeRef: {
        "runtime_id": "claude-cli",
        "adapter_version": "accretion-claude-v1",
        "provider": Provider.CLAUDE,
        "model": "claude-opus-4",
        "capability_profile_digest": DIGEST,
    },
    CapabilityRef: {
        "capability_id": "accretion.sample.echo",
        "capability_version": "0.1.0",
    },
    ToolRef: {"tool_id": "shell.command", "implementation_digest": DIGEST},
    SkillRef: {"skill_id": "skl_review", "version": "1.2.0", "package_digest": DIGEST},
    EnvironmentRef: {
        "environment_id": "sandbox-linux",
        "image_digest": DIGEST,
        "policy_profile": "restricted",
    },
    VerifierRef: {"verifier_contract_id": "tests.pass", "implementation_digest": DIGEST},
    EvidenceRef: {
        "evidence_id": "evd_1",
        "evidence_class": EvidenceClass.SIMULATION,
        "content_digest": DIGEST,
    },
    PolicyRef: {"policy_id": "local-capability-policy", "version": "1", "content_digest": DIGEST},
    ApprovalArtifactRef: {
        "uri": f"sha256://{DIGEST}",
        "digest": DIGEST,
        "media_type": "application/pdf",
        "retention_class": "STANDARD",
    },
}

CASES = list(VALID.items())
IDS = [model.__name__ for model in VALID]


def defined_reference_models() -> list[type[BaseModel]]:
    """Every reference model the module defines, ignoring anything merely imported."""

    return [
        member
        for _, member in inspect.getmembers(refs_module, inspect.isclass)
        if issubclass(member, BaseModel)
        and member is not BaseModel
        and member.__module__ == refs_module.__name__
    ]


def test_the_valid_table_covers_every_reference_the_module_defines() -> None:
    assert set(defined_reference_models()) == set(VALID)


@pytest.mark.parametrize(("model", "payload"), CASES, ids=IDS)
def test_every_reference_builds_from_its_identity_fields(
    model: type[StrictModel], payload: dict[str, Any]
) -> None:
    instance = model(**payload)
    for field, value in payload.items():
        assert getattr(instance, field) == value


@pytest.mark.parametrize(("model", "payload"), CASES, ids=IDS)
def test_every_reference_rejects_a_missing_identity_field(
    model: type[StrictModel], payload: dict[str, Any]
) -> None:
    required = [name for name, field in model.model_fields.items() if field.is_required()]
    assert required, f"{model.__name__} requires nothing, so it pins no identity"
    for name in required:
        reduced = {key: value for key, value in payload.items() if key != name}
        with pytest.raises(ValidationError):
            model(**reduced)


@pytest.mark.parametrize(("model", "payload"), CASES, ids=IDS)
def test_every_reference_rejects_an_unknown_field(
    model: type[StrictModel], payload: dict[str, Any]
) -> None:
    """``StrictModel`` is ``extra="forbid"``: a typo is a rejection, not a silent drop."""

    with pytest.raises(ValidationError):
        model(**payload, definitely_not_a_field="x")


@pytest.mark.parametrize(("model", "payload"), CASES, ids=IDS)
def test_every_reference_rejects_an_empty_identity_string(
    model: type[StrictModel], payload: dict[str, Any]
) -> None:
    for name, value in payload.items():
        if not isinstance(value, str) or value is None:
            continue
        with pytest.raises(ValidationError):
            model(**{**payload, name: ""})


@pytest.mark.parametrize(("model", "payload"), CASES, ids=IDS)
def test_every_reference_rejects_a_malformed_digest(
    model: type[StrictModel], payload: dict[str, Any]
) -> None:
    digest_fields = [name for name in payload if name.endswith("digest")]
    for name in digest_fields:
        for bad in ("", "not-a-digest", DIGEST.upper(), DIGEST[:-1], DIGEST + "0"):
            with pytest.raises(ValidationError):
                model(**{**payload, name: bad})


@pytest.mark.parametrize(("model", "payload"), CASES, ids=IDS)
def test_every_reference_hashes_deterministically_and_moves_when_a_field_moves(
    model: type[StrictModel], payload: dict[str, Any]
) -> None:
    baseline = content_hash(model(**payload))
    assert baseline == content_hash(model(**payload))
    for name in (name for name in payload if name.endswith("digest")):
        assert content_hash(model(**{**payload, name: OTHER_DIGEST})) != baseline


def test_the_runtime_model_is_the_one_optional_field_and_defaults_to_none() -> None:
    """A subscription CLI runtime need not pin a model; everything else is identity."""

    payload = {key: value for key, value in VALID[RuntimeRef].items() if key != "model"}
    assert RuntimeRef(**payload).model is None


def test_the_capability_reference_does_not_spell_its_version_schema_version() -> None:
    """Reserved for the header a persisted aggregate carries about itself (registry §3)."""

    assert "schema_version" not in CapabilityRef.model_fields
    assert "capability_version" in CapabilityRef.model_fields


def test_the_evidence_reference_reuses_the_existing_evidence_class_enum() -> None:
    """ADR-054 (e): the v0.3 M5 taxonomy already equals registry §5.2 — one definition."""

    assert EvidenceRef.model_fields["evidence_class"].annotation is EvidenceClass
    assert EvidenceRef(
        evidence_id="evd_1", evidence_class=EvidenceClass.PHYSICAL, content_digest=DIGEST
    ).evidence_class is EvidenceClass.PHYSICAL
    with pytest.raises(ValidationError):
        EvidenceRef(evidence_id="evd_1", evidence_class="MADE_UP", content_digest=DIGEST)


def test_the_evidence_reference_states_its_class_rather_than_defaulting_to_one() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(evidence_id="evd_1", content_digest=DIGEST)


def test_an_approval_artifact_uri_must_carry_a_scheme() -> None:
    payload = dict(VALID[ApprovalArtifactRef])
    for bad in ("/var/tmp/receipt.pdf", "receipt.pdf", "://x"):
        with pytest.raises(ValidationError):
            ApprovalArtifactRef(**{**payload, "uri": bad})


def test_an_approval_artifact_media_type_is_a_bare_type_subtype() -> None:
    payload = dict(VALID[ApprovalArtifactRef])
    assert ApprovalArtifactRef(**{**payload, "media_type": "image/png"}).media_type == "image/png"
    for bad in ("application", "text/plain; charset=utf-8", "/pdf", "application/"):
        with pytest.raises(ValidationError):
            ApprovalArtifactRef(**{**payload, "media_type": bad})


def test_the_approval_artifact_reference_is_not_a_run_scoped_artifact() -> None:
    """ADR-054 (f): two types, each honest about its scope — not one widened type."""

    assert "run_id" not in ApprovalArtifactRef.model_fields
    assert ApprovalArtifactRef is not ArtifactRef


def test_the_artifact_reference_still_requires_its_run_id() -> None:
    """The pin. ``ArtifactRef`` is persisted inside execution traces (ADR-054 f)."""

    assert ArtifactRef.model_fields["run_id"].is_required()
    with pytest.raises(ValidationError):
        ArtifactRef(artifact_id="art_1", kind="LOG", path=Path("artifacts/x.log"))
    artifact = ArtifactRef(
        artifact_id="art_1", run_id="run_1", kind="LOG", path=Path("artifacts/x.log")
    )
    assert artifact.run_id == "run_1"


def test_the_four_reused_references_are_not_redefined_in_the_refs_module() -> None:
    """Registry §21: one concept, one owner. These four keep theirs."""

    defined = {model.__name__ for model in defined_reference_models()}
    assert defined.isdisjoint({"PrincipalRef", "PluginRef", "ConnectionRef", "ArtifactRef"})


def test_the_new_references_are_not_re_exported_from_the_package_root() -> None:
    """ADR-053: v0.4 names are imported explicitly, never through the root."""

    import accretion.contracts as root

    for name in (model.__name__ for model in defined_reference_models()):
        assert not hasattr(root, name), f"{name} leaked into the package root"


def test_ten_well_known_names_still_import_from_the_package_root() -> None:
    """The package conversion was a rename: every existing dotted import still resolves."""

    for name, imported in {
        "StrictModel": StrictModel,
        "VerificationResult": VerificationResult,
        "PrincipalRef": PrincipalRef,
        "PluginRef": PluginRef,
        "ConnectionRef": ConnectionRef,
        "ArtifactRef": ArtifactRef,
        "EvidenceClass": EvidenceClass,
        "RiskLevel": RiskLevel,
        "Provider": Provider,
        "VerificationStatus": VerificationStatus,
    }.items():
        assert imported.__name__ == name

    from accretion.contracts import Capability, MetaPlugin, Task, TaskEnvelope, WorkflowTemplate

    for late in (Capability, MetaPlugin, Task, TaskEnvelope, WorkflowTemplate):
        assert issubclass(late, StrictModel)
    assert issubclass(RuntimeRef, StrictModel)


@pytest.mark.parametrize("value", ["standard", "Standard", "STANDARD-7", "7DAYS", ""])
def test_retention_class_must_be_an_upper_case_token(value: str) -> None:
    """Case or punctuation drift would fork a digest before the registry names the values."""

    with pytest.raises(ValidationError):
        ApprovalArtifactRef(
            uri="s3://approvals/receipt.pdf",
            digest="a" * 64,
            media_type="application/pdf",
            retention_class=value,
        )
