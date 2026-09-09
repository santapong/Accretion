"""Synthetic negative witnesses for the SDK; real host cases stay pending."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Any

import pytest
from v05_sdk_fixtures import Harness, replace_contract

from accretion.contracts.robotics import PreparedCommand, SafetyDecisionReceipt
from accretion.contracts.robotics.values import ContentAddressedArtifactRef
from accretion.robotics.conformance import ConformanceRunner
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import (
    TENSOR_MEDIA_TYPE,
    ObservationBatch,
    ObservationLimits,
    ObservationValidator,
    validate_artifact,
)
from accretion.robotics.protocol import (
    CommandScope,
    ErrorOutcome,
    ExecuteRequest,
    ExecuteResult,
    IntentRecord,
    PreparedRecord,
    PrepareRequest,
    ProtocolRequest,
    ResetRequest,
    RestoreRequest,
    SafetyRecord,
    SnapshotRequest,
    SnapshotResult,
    SuccessOutcome,
    TerminateRequest,
)
from accretion.robotics.testing.fault_adapter import Fault


def refusal(response: Any, code: Code) -> None:
    assert isinstance(response.outcome, ErrorOutcome), response
    assert response.outcome.code is code


def test_valid_signed_candidate_executes_once_with_correlated_result() -> None:
    harness = Harness()
    payload = harness.execute_payload()
    assert harness.adapter.advance_count == 0
    response = harness.session.dispatch(harness.request(payload))
    assert isinstance(response.outcome, SuccessOutcome)
    result = response.outcome.payload
    assert isinstance(result, ExecuteResult)
    assert (
        result.prepared_command_hash == payload.prepared.for_execution(PreparedCommand).content_hash
    )
    assert (
        result.safety_decision_hash
        == payload.safety.for_execution(SafetyDecisionReceipt).content_hash
    )
    assert harness.adapter.advance_count == harness.adapter.execute_calls == 1
    assert harness.session.pins.budget.actions == 1
    refusal(harness.session.dispatch(harness.request(payload)), Code.EPISODE_STATE_CONFLICT)
    assert harness.adapter.execute_calls == 1


@pytest.mark.parametrize(
    "pin",
    [
        "workspace_id",
        "project_id",
        "action_intent_hash",
        "prepared_command_hash",
        "safety_envelope_hash",
        "episode_approval_hash",
        "state",
        "lease",
        "policy_ref",
        "evaluator",
        "budget_before",
        "expires_at_sim_time_ns",
        "decision",
    ],
)
def test_wrong_signed_authority_pin_never_invokes_adapter(pin: str) -> None:
    harness = Harness()
    intent, prepared = harness.prepared()
    valid = harness.sign(prepared)
    values = valid.decision.model_dump(mode="python")
    if pin == "state":
        values[pin]["observation_digest"] = "a" * 64
    elif pin == "lease":
        values[pin]["generation"] += 1
    elif pin == "policy_ref":
        values[pin]["content_digest"] = "a" * 64
    elif pin == "evaluator":
        values[pin]["implementation_digest"] = "a" * 64
    elif pin == "budget_before":
        values[pin]["actions"] += 1
        values["budget_after"]["actions"] += 1
    elif pin == "expires_at_sim_time_ns":
        values[pin] = prepared.expires_at_sim_time_ns + 1
    elif pin == "decision":
        values[pin] = "DENY"
        values["budget_after"] = values["budget_before"]
    elif pin in ("workspace_id", "project_id"):
        # The header follows the signed scope for a structurally valid foreign receipt.
        values[pin] = "other-scope"
    else:
        values[pin] = "a" * 64
    if pin in ("workspace_id", "project_id"):
        from accretion.contracts.robotics import SafetyDecisionPayload, sign_safety_payload

        decision = SafetyDecisionPayload.model_validate(values)
        receipt = replace_contract(
            valid,
            **{
                pin: values[pin],
                "decision": decision,
                "signature": sign_safety_payload(
                    decision, key_id="test-only", private_key=harness.key
                ),
            },
        )
    else:
        receipt = harness.sign(prepared, **values)
    payload = ExecuteRequest(
        intent=IntentRecord.from_contract(intent),
        prepared=PreparedRecord.from_contract(prepared),
        safety=SafetyRecord.from_contract(receipt),
    )
    refusal(harness.session.dispatch(harness.request(payload)), Code.SAFETY_DENIED)
    assert harness.adapter.execute_calls == harness.adapter.advance_count == 0


@pytest.mark.parametrize(
    "field",
    [
        "command_schema_hash",
        "controller_digest",
        "adapter_artifact_digest",
        "action_intent_hash",
        "lease",
        "state",
        "sequence",
    ],
)
def test_changed_prepared_candidate_cannot_use_original_authority(field: str) -> None:
    harness = Harness()
    intent, prepared = harness.prepared()
    values = prepared.model_dump(mode="python")
    if field == "lease":
        values[field]["generation"] += 1
    elif field == "state":
        values[field]["observation_digest"] = "a" * 64
    elif field == "sequence":
        values[field] += 1
    else:
        values[field] = "a" * 64
    changed = replace_contract(prepared, **{field: values[field]})
    payload = ExecuteRequest(
        intent=IntentRecord.from_contract(intent),
        prepared=PreparedRecord.from_contract(changed),
        safety=SafetyRecord.from_contract(harness.sign(prepared)),
    )
    refusal(harness.session.dispatch(harness.request(payload)), Code.SAFETY_DENIED)
    assert harness.adapter.execute_calls == harness.adapter.advance_count == 0


@pytest.mark.parametrize("kind", ["revoked", "wrong_key", "wrong_principal", "bad_signature"])
def test_untrusted_signature_does_not_advance(kind: str) -> None:
    from accretion.contracts.robotics import TrustedSafetyKey

    harness = Harness()
    payload = harness.execute_payload()
    if kind == "revoked":
        harness.trusted_keys.clear()
    elif kind == "wrong_key":
        harness.trusted_keys["test-only"] = TrustedSafetyKey(
            harness.pins.evaluator_principal_id, b"\0" * 32
        )
    elif kind == "wrong_principal":
        harness.trusted_keys["test-only"] = TrustedSafetyKey(
            "other", harness.key.public_key().public_bytes_raw()
        )
    else:
        receipt = payload.safety.for_execution(SafetyDecisionReceipt)
        values = receipt.signature.model_dump(mode="python")
        values["signature_base64"] = "A" * 86 + "=="
        changed = replace_contract(receipt, signature=values)
        payload = ExecuteRequest(
            intent=payload.intent,
            prepared=payload.prepared,
            safety=SafetyRecord.from_contract(changed),
        )
    refusal(harness.session.dispatch(harness.request(payload)), Code.SAFETY_DENIED)
    assert harness.adapter.execute_calls == harness.adapter.advance_count == 0


def test_live_authority_denial_and_missing_command_bytes_are_fail_closed() -> None:
    harness = Harness()
    payload = harness.execute_payload()
    harness.guard.allowed = False
    refusal(harness.session.dispatch(harness.request(payload)), Code.APPROVAL_INVALID)
    assert harness.adapter.execute_calls == 0
    harness.guard.allowed = True
    prepared = payload.prepared.for_execution(PreparedCommand)
    harness.artifacts.blobs.pop(prepared.command_ref.digest)
    refusal(harness.session.dispatch(harness.request(payload)), Code.ARTIFACT_UNAVAILABLE)
    assert harness.adapter.execute_calls == harness.adapter.advance_count == 0


@pytest.mark.parametrize(
    "scope_field", ["episode_id", "lease", "expected_state", "dependency_closure_hash"]
)
def test_scope_pins_reject_reset_before_any_adapter_call(scope_field: str) -> None:
    harness = Harness()
    scope = harness.session.pins.command_scope().model_dump(mode="python")
    if scope_field == "episode_id":
        scope[scope_field] = "sep_" + "0" * 26
    elif scope_field == "lease":
        scope[scope_field]["generation"] += 1
    elif scope_field == "expected_state":
        scope[scope_field]["observation_digest"] = "a" * 64
    else:
        scope[scope_field] = "a" * 64
    request = ProtocolRequest.create(
        request_id="reset",
        sequence=0,
        scope=CommandScope.model_validate(scope),
        payload=ResetRequest(
            seed=harness.episode.seed,
            randomization_sample_hash=harness.episode.randomization_sample_hash,
        ),
    )
    refusal(harness.session.dispatch(request), Code.LEASE_INVALID)
    assert harness.adapter.reset_calls == harness.adapter.advance_count == 0


def test_reset_is_bounded_initial_transition_not_original_episode_recovery() -> None:
    harness = Harness()
    payload = ResetRequest(
        seed=harness.episode.seed,
        randomization_sample_hash=harness.episode.randomization_sample_hash,
    )
    assert isinstance(harness.session.dispatch(harness.request(payload)).outcome, SuccessOutcome)
    refusal(harness.session.dispatch(harness.request(payload)), Code.EPISODE_STATE_CONFLICT)
    assert harness.adapter.reset_calls == 1


def test_preparation_advancement_aborts_and_snapshot_cannot_hide_it() -> None:
    harness = Harness(Fault.PREPARE_ADVANCES)
    intent = harness.make_intent()
    response = harness.session.dispatch(
        harness.request(PrepareRequest(intent=IntentRecord.from_contract(intent)))
    )
    refusal(response, Code.EPISODE_STATE_CONFLICT)
    assert harness.session.aborted and harness.adapter.advance_count == 1
    with pytest.raises(RoboticsError) as error:
        harness.session.dispatch(harness.request(SnapshotRequest()))
    assert error.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN


@pytest.mark.parametrize(
    "fault",
    [
        Fault.APPLY_THEN_LOST_ACK,
        Fault.CRASH_BEFORE_APPLY,
        Fault.CLOCK_REGRESSION,
        Fault.MISSING_OBSERVATION_BYTES,
    ],
)
def test_unknown_execution_outcome_never_retries_or_restores_original(fault: Fault) -> None:
    harness = Harness(fault)
    payload = harness.execute_payload()
    refusal(harness.session.dispatch(harness.request(payload)), Code.ACKNOWLEDGEMENT_UNCERTAIN)
    calls = harness.adapter.execute_calls
    for operation in (
        payload,
        ResetRequest(
            seed=harness.episode.seed,
            randomization_sample_hash=harness.episode.randomization_sample_hash,
        ),
    ):
        with pytest.raises(RoboticsError) as error:
            harness.session.dispatch(harness.request(operation))
        assert error.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN
    assert harness.adapter.execute_calls == calls == 1
    assert isinstance(
        harness.session.dispatch(harness.request(TerminateRequest())).outcome, SuccessOutcome
    )
    assert harness.adapter.terminated


def test_restore_requires_separate_pinned_fresh_replay_episode_and_verified_bytes() -> None:
    source = Harness()
    response = source.session.dispatch(source.request(SnapshotRequest()))
    assert isinstance(response.outcome, SuccessOutcome)
    assert isinstance(response.outcome.payload, SnapshotResult)
    snapshot = response.outcome.payload.snapshot
    refusal(
        source.session.dispatch(source.request(RestoreRequest(snapshot=snapshot))),
        Code.REPLAY_FAILED,
    )
    replay = Harness(
        episode_id="sep_" + "0" * 26,
        replay_source=source.episode.episode_id,
        artifacts=source.artifacts,
    )
    restored = replay.session.dispatch(replay.request(RestoreRequest(snapshot=snapshot)))
    assert isinstance(restored.outcome, SuccessOutcome)
    assert replay.adapter.observe().episode_id == replay.episode.episode_id
    refusal(
        replay.session.dispatch(replay.request(RestoreRequest(snapshot=snapshot))),
        Code.REPLAY_FAILED,
    )
    assert source.adapter.restore_calls == 0 and replay.adapter.restore_calls == 1


def test_immutable_pins_are_copied_and_reentrant_dispatch_is_denied() -> None:
    harness = Harness()
    exposed = harness.session.pins
    exposed.episode.lease.generation += 1
    harness.pins.episode.lease.generation += 1
    assert harness.session.pins.episode.lease.generation == 1
    assert harness.session._dispatch_lock.acquire(blocking=False)
    try:
        with pytest.raises(RoboticsError) as error:
            harness.session.dispatch(harness.request(SnapshotRequest()))
        assert error.value.code is Code.EPISODE_STATE_CONFLICT
    finally:
        harness.session._dispatch_lock.release()


def test_candidate_prepared_in_other_session_is_not_executable() -> None:
    harness = Harness()
    intent = harness.make_intent()
    prepared = harness.adapter.prepare(intent)
    payload = ExecuteRequest(
        intent=IntentRecord.from_contract(intent),
        prepared=PreparedRecord.from_contract(prepared),
        safety=SafetyRecord.from_contract(harness.sign(prepared)),
    )
    refusal(harness.session.dispatch(harness.request(payload)), Code.SAFETY_DENIED)
    assert harness.adapter.execute_calls == 0


def test_construction_runner_never_emits_activation_ready_report() -> None:
    harness = Harness()
    result = ConformanceRunner().run(
        harness.adapter,
        harness.dependencies,
        artifact_reader=harness.artifacts,
        episode_id=harness.episode.episode_id,
        lease=harness.episode.lease,
    )
    assert result.construction_checks_passed
    assert result.scope == "SDK_CONSTRUCTION_ONLY" and result.activation_eligible is False
    assert "ur5e_robotiq_and_independent_panda_same_black_box_suite" in result.pending_host_cases
    assert harness.adapter.advance_count == harness.adapter.reset_calls == 0
    changed = harness.dependencies.model_dump(mode="python")
    changed["physics_parameters_hash"] = "a" * 64
    result = ConformanceRunner().run(
        harness.adapter,
        type(harness.dependencies).model_validate(changed),
        artifact_reader=harness.artifacts,
        episode_id=harness.episode.episode_id,
        lease=harness.episode.lease,
    )
    assert not result.construction_checks_passed and not result.activation_eligible


def validate(harness: Harness, batch: ObservationBatch, **kwargs: Any) -> Any:
    return ObservationValidator().validate(
        harness.spec,
        harness.descriptor,
        batch,
        harness.artifacts,
        episode_id=harness.episode.episode_id,
        lease=harness.episode.lease,
        **kwargs,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "shape",
        "unit",
        "sensor",
        "frame",
        "sample_sequence",
        "skew",
        "future",
        "lease",
        "missing_field",
        "length",
        "media",
    ],
)
def test_observation_metadata_is_checked_before_reader_allocation(fault: str) -> None:
    harness = Harness()
    values = harness.initial.model_dump(mode="python")
    sample = values["samples"][0]
    if fault == "shape":
        sample["field"]["shape"] = [5]
    elif fault == "unit":
        sample["field"]["unit"] = "rad/s"
    elif fault == "sensor":
        sample["field"]["sensor_id"] = "other"
    elif fault == "frame":
        sample["field"]["frame_id"] = "other"
    elif fault == "sample_sequence":
        sample["sequence"] += 1
    elif fault == "skew":
        values["sim_time_ns"] = 20_000_001
    elif fault == "future":
        sample["sim_time_ns"] = 1
    elif fault == "lease":
        values["lease"]["generation"] += 1
    elif fault == "missing_field":
        sample["field"]["field"] = "other"
    elif fault == "length":
        sample["artifact"]["size_bytes"] += 1
    elif fault == "media":
        sample["artifact"]["media_type"] = "application/x-pickle"
    before = harness.artifacts.read_calls
    with pytest.raises(RoboticsError):
        validate(harness, ObservationBatch.model_validate(values))
    assert harness.artifacts.read_calls == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_valid_hash_and_length_do_not_make_nonfinite_samples_valid(value: float) -> None:
    harness = Harness()
    fields = harness.initial.model_dump(mode="python")
    fields["samples"][0]["artifact"] = harness.artifacts.put(
        struct.pack("<6d", value, 0, 0, 0, 0, 0), media_type=TENSOR_MEDIA_TYPE
    )
    with pytest.raises(RoboticsError) as error:
        validate(harness, ObservationBatch.model_validate(fields))
    assert error.value.code is Code.OBSERVATION_INVALID


@pytest.mark.parametrize("kind", ["missing", "wrong_digest", "truncated", "overlong"])
def test_actual_observation_bytes_must_match_declared_ref(kind: str) -> None:
    harness = Harness()
    digest = harness.initial.samples[0].artifact.digest
    if kind == "missing":
        harness.artifacts.blobs.pop(digest)
    elif kind == "wrong_digest":
        harness.artifacts.blobs[digest] = b"x" * 48
    elif kind == "truncated":
        harness.artifacts.blobs[digest] = b"x" * 40
    else:
        harness.artifacts.blobs[digest] = b"x" * 56
    with pytest.raises(RoboticsError):
        validate(harness, harness.initial)


def test_declared_huge_tensor_refuses_before_any_read() -> None:
    harness = Harness()
    spec = replace_contract(
        harness.spec,
        required=[
            dict(
                field="depth",
                sensor_id="depth",
                modality="DEPTH",
                dtype="float32",
                shape=[2**60, 2**60],
                unit="m",
                frame_id="base",
            )
        ],
    )
    descriptor = replace_contract(
        harness.descriptor, sensors=[dict(sensor_id="depth", modality="DEPTH", frame_id="base")]
    )
    values = harness.initial.model_dump(mode="python")
    values["samples"][0]["field"] = spec.required[0]
    before = harness.artifacts.read_calls
    with pytest.raises(RoboticsError) as error:
        ObservationValidator().validate(
            spec,
            descriptor,
            ObservationBatch.model_validate(values),
            harness.artifacts,
            episode_id=harness.episode.episode_id,
            lease=harness.episode.lease,
        )
    assert error.value.code is Code.PAYLOAD_TOO_LARGE and harness.artifacts.read_calls == before


def test_clock_and_sequence_regressions_and_reused_sequence_changed_data_fail() -> None:
    harness = Harness()
    previous = harness.initial.state_binding()
    previous.sim_time_ns = 1
    with pytest.raises(RoboticsError) as error:
        validate(harness, harness.initial, previous=previous)
    assert error.value.code is Code.CLOCK_REGRESSION
    previous = harness.initial.state_binding()
    previous.observation_sequence = 1
    with pytest.raises(RoboticsError):
        validate(harness, harness.initial, previous=previous)
    previous = harness.initial.state_binding()
    previous.observation_digest = "a" * 64
    with pytest.raises(RoboticsError):
        validate(harness, harness.initial, previous=previous)


def test_small_chunks_validate_tensor_across_element_boundaries_without_numpy() -> None:
    harness = Harness()
    result = ObservationValidator(ObservationLimits(chunk_bytes=3)).validate(
        harness.spec,
        harness.descriptor,
        harness.initial,
        harness.artifacts,
        episode_id=harness.episode.episode_id,
        lease=harness.episode.lease,
    )
    assert result.state == harness.initial.state_binding()
    exposed = result.batch
    exposed.lease.generation += 1
    assert result.batch.lease.generation == 1


def test_bad_reader_chunk_cannot_overallocate_consumer() -> None:
    harness = Harness()

    class BadReader:
        def iter_bytes(
            self, ref: ContentAddressedArtifactRef, *, max_bytes: int, chunk_size: int
        ) -> Iterator[bytes]:
            yield b"x" * (chunk_size + 1)

    with pytest.raises(RoboticsError) as error:
        validate_artifact(BadReader(), harness.initial.samples[0].artifact, max_bytes=48)
    assert error.value.code is Code.ARTIFACT_INVALID


def test_signed_budget_and_expiry_bound_actual_elapsed_observation() -> None:
    harness = Harness()
    intent, prepared = harness.prepared()
    receipt = harness.sign(
        prepared,
        expires_at_sim_time_ns=1,
        budget_after=dict(actions=1, cumulative_joint_motion_rad=0.0, elapsed_sim_seconds=0.0),
    )
    payload = ExecuteRequest(
        intent=IntentRecord.from_contract(intent),
        prepared=PreparedRecord.from_contract(prepared),
        safety=SafetyRecord.from_contract(receipt),
    )
    refusal(harness.session.dispatch(harness.request(payload)), Code.ACKNOWLEDGEMENT_UNCERTAIN)
    assert harness.session.aborted and harness.adapter.execute_calls == 1


def test_unexpected_authority_failure_aborts_without_adapter_invocation() -> None:
    harness = Harness()
    payload = harness.execute_payload()

    class UncertainGuard:
        def authorize(self, request: ProtocolRequest, *, pins: Any) -> None:
            raise OSError("synthetic authority reservation outcome unavailable")

    harness.session.guard = UncertainGuard()
    refusal(harness.session.dispatch(harness.request(payload)), Code.ACKNOWLEDGEMENT_UNCERTAIN)
    assert harness.session.aborted and harness.adapter.execute_calls == 0


@pytest.mark.parametrize(
    "dtype,modality,shape,unit,values",
    [
        ("uint8", "RGB", [1, 1, 3], "pixel", bytes([1, 2, 255])),
        ("float32", "DEPTH", [1, 2], "m", struct.pack("<2f", 0.2, 1.5)),
        ("bool", "CONTACT", [2], "1", bytes([0, 1])),
        ("float64", "POSE", [4], "1", struct.pack("<4d", 0, 0, 0, 1)),
    ],
)
def test_typed_sensor_bytes_validate_for_independent_modalities(
    dtype: str,
    modality: str,
    shape: list[int],
    unit: str,
    values: bytes,
) -> None:
    harness = Harness()
    field = dict(
        field="test",
        sensor_id="test",
        modality=modality,
        dtype=dtype,
        shape=shape,
        unit=unit,
        frame_id="base",
    )
    spec = replace_contract(harness.spec, required=[field])
    desc = replace_contract(
        harness.descriptor, sensors=[dict(sensor_id="test", modality=modality, frame_id="base")]
    )
    batch = harness.initial.model_dump(mode="python")
    batch["samples"][0].update(
        field=field, artifact=harness.artifacts.put(values, media_type=TENSOR_MEDIA_TYPE)
    )
    result = ObservationValidator().validate(
        spec,
        desc,
        ObservationBatch.model_validate(batch),
        harness.artifacts,
        episode_id=harness.episode.episode_id,
        lease=harness.episode.lease,
    )
    assert result.state.observation_sequence == 0
    if dtype in ("bool", "float64"):
        invalid = bytes([0, 2]) if dtype == "bool" else struct.pack("<4d", 0, 0, 0, 0)
        batch["samples"][0]["artifact"] = harness.artifacts.put(
            invalid, media_type=TENSOR_MEDIA_TYPE
        )
        with pytest.raises(RoboticsError) as error:
            ObservationValidator().validate(
                spec,
                desc,
                ObservationBatch.model_validate(batch),
                harness.artifacts,
                episode_id=harness.episode.episode_id,
                lease=harness.episode.lease,
            )
        assert error.value.code is Code.OBSERVATION_INVALID


def test_lowered_batch_budget_checks_entire_manifest_before_io() -> None:
    harness = Harness()
    before = harness.artifacts.read_calls
    with pytest.raises(RoboticsError) as error:
        ObservationValidator(ObservationLimits(batch_bytes=47)).validate(
            harness.spec,
            harness.descriptor,
            harness.initial,
            harness.artifacts,
            episode_id=harness.episode.episode_id,
            lease=harness.episode.lease,
        )
    assert error.value.code is Code.PAYLOAD_TOO_LARGE and harness.artifacts.read_calls == before


def test_explicit_observe_cannot_advance_after_valid_bootstrap() -> None:
    from accretion.robotics.protocol import ObserveRequest

    harness = Harness()
    assert harness.adapter.advance_count == 0
    initial_state = harness.session.pins.state
    harness.adapter.fault = Fault.OBSERVE_ADVANCES
    response = harness.session.dispatch(harness.request(ObserveRequest()))
    refusal(response, Code.EPISODE_STATE_CONFLICT)
    assert harness.session.aborted and harness.adapter.advance_count == 1
    assert harness.session.pins.state == initial_state
    assert harness.session.pins.budget.actions == harness.adapter.execute_calls == 0
    with pytest.raises(RoboticsError):
        harness.session.dispatch(harness.request(ObserveRequest()))


def test_pure_observation_preserves_current_candidate_and_state() -> None:
    from accretion.robotics.protocol import ObserveRequest

    harness = Harness()
    payload = harness.execute_payload()
    state = harness.session.pins.state
    response = harness.session.dispatch(harness.request(ObserveRequest()))
    assert isinstance(response.outcome, SuccessOutcome)
    assert harness.session.pins.state == state and harness.adapter.advance_count == 0
    assert isinstance(harness.session.dispatch(harness.request(payload)).outcome, SuccessOutcome)


def test_adapter_cannot_alter_exact_candidate_and_still_claim_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    payload = harness.execute_payload()
    execute = harness.adapter.execute

    def altering_execute(
        prepared: PreparedCommand, safety: SafetyDecisionReceipt
    ) -> ObservationBatch:
        outcome = execute(prepared, safety)
        prepared.controller_digest = "a" * 64
        return outcome

    monkeypatch.setattr(harness.adapter, "execute", altering_execute)
    refusal(harness.session.dispatch(harness.request(payload)), Code.ACKNOWLEDGEMENT_UNCERTAIN)
    assert harness.session.aborted
