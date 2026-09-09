"""Cross-module construction compatibility, without a real authority or simulator."""

from pathlib import Path

import pytest
from test_v05_safety import Case
from test_v05_safety import case as shared_case
from v05_sdk_fixtures import Harness

from accretion.contracts import EvidenceClass
from accretion.robotics.artifacts import ArtifactStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.observations import ObservationValidator
from accretion.robotics.sdk import ExecutionPins, validate_execution

safety_case = shared_case


def test_pure_safety_receipt_is_compatible_with_sdk_precheck(safety_case: Case) -> None:
    case = safety_case
    issuance = case.issuance()
    assert issuance.receipt.decision.decision == "ALLOW"
    pins = ExecutionPins(
        workspace_id=case.context.workspace_id,
        project_id=case.context.project_id,
        episode=case.context.episode,
        dependencies=case.context.closure,
        descriptor_hash=case.descriptor.content_hash,
        adapter_principal_id=case.prepared.created_by.principal_id,
        evaluator_principal_id=case.signer.principal.principal_id,
        policy_ref=case.context.policy_ref,
        evaluator=case.signer.evaluator,
        approval_hash=case.approval.content_hash,
        state=case.context.state,
        budget=case.context.budget,
        next_action_sequence=case.context.sequence,
    )
    validate_execution(
        case.intent,
        case.prepared,
        issuance.receipt,
        pins=pins,
        descriptor=case.descriptor,
        trusted_keys={case.signer.key_id: case.signer.trusted_key},
    )
    issuance.validate_current_context(case.context)
    changed = case.context.model_copy(update={"phase": "RELEASE"})
    assert changed.phase != case.context.phase
    with pytest.raises(RoboticsError):
        issuance.validate_current_context(changed)


def test_sdk_validates_tensor_bytes_from_bounded_filesystem_store(tmp_path: Path) -> None:
    harness = Harness()
    with ArtifactStore(tmp_path) as store:
        for sample in harness.initial.samples:
            ref = sample.artifact
            result = store.put(
                harness.artifacts.blobs[ref.digest],
                media_type=ref.media_type,
                retention_class=ref.retention_class,
                evidence_class=EvidenceClass.SIMULATION,
            )
            assert result.model_dump() == ref.model_dump()
        validator = ObservationValidator()
        checked = validator.validate(
            harness.spec,
            harness.descriptor,
            harness.initial,
            store,
            episode_id=harness.episode.episode_id,
            lease=harness.episode.lease,
            previous=harness.initial.state_binding(),
        )
        assert checked.batch == harness.initial
        ref = harness.initial.samples[0].artifact
        file = tmp_path / (ref.digest + ".blob")
        file.chmod(0o600)
        file.write_bytes(b"\x00" * (ref.size_bytes - 1) + b"\x01")
        with pytest.raises(RoboticsError):
            validator.validate(
                harness.spec,
                harness.descriptor,
                harness.initial,
                store,
                episode_id=harness.episode.episode_id,
                lease=harness.episode.lease,
                previous=harness.initial.state_binding(),
            )
