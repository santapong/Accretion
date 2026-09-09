"""Wire/fault witnesses only; no AC5 host or simulator conformance claims."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from v05_sdk_fixtures import Harness, fixture

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import ActionIntent
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import (
    MAX_FRAME_BYTES,
    ErrorOutcome,
    FrameCodec,
    HelloRequest,
    HelloResult,
    IntentRecord,
    ProtocolRequest,
    ProtocolResponse,
    ResetRequest,
    ResponseCorrelator,
    SuccessOutcome,
    bounded_json,
    parse_message,
)


def hello() -> ProtocolRequest:
    return ProtocolRequest.create(
        request_id="hello-1", sequence=0, scope=None, payload=HelloRequest()
    )


def test_fragmented_and_coalesced_frames_preserve_exact_messages() -> None:
    request = hello()
    response = ProtocolResponse.create(request, SuccessOutcome(payload=HelloResult()))
    codec = FrameCodec()
    frames = codec.encode(request) + codec.encode(response)
    output = []
    for start in range(0, len(frames), 7):
        output.extend(codec.feed(frames[start : start + 7]))
    codec.finish()
    assert output == [request, response]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1e999}',
        b'{"a":123456789012345678901234567890123}',
        b"\xff",
        b'{"a":',
    ],
)
def test_json_parser_rejects_ambiguous_or_nonfinite_input(raw: bytes) -> None:
    with pytest.raises(RoboticsError):
        bounded_json(raw)


@pytest.mark.parametrize("raw", [b"[" * 33 + b"0" + b"]" * 33, b"x" * (MAX_FRAME_BYTES + 1)])
def test_budgets_apply_before_json_decode(raw: bytes) -> None:
    with pytest.raises(RoboticsError) as error:
        bounded_json(raw)
    assert error.value.code is Code.PAYLOAD_TOO_LARGE


def test_byte_limit_not_unicode_character_limit() -> None:
    with pytest.raises(RoboticsError):
        bounded_json(('"' + "é" * 50 + '"').encode(), max_bytes=60)


def test_oversize_partial_frame_poisoning_and_truncated_eof() -> None:
    codec = FrameCodec(64)
    assert list(codec.feed(b"x" * 64)) == []
    with pytest.raises(RoboticsError) as error:
        list(codec.feed(b"x"))
    assert error.value.code is Code.PAYLOAD_TOO_LARGE
    with pytest.raises(RoboticsError):
        list(codec.feed(b"\n"))
    other = FrameCodec()
    list(other.feed(b'{"kind":'))
    with pytest.raises(RoboticsError):
        other.finish()


@pytest.mark.parametrize("limit", [True, 0, -1, MAX_FRAME_BYTES + 1])
def test_frame_ceiling_can_only_be_lowered(limit: int) -> None:
    with pytest.raises(ValueError):
        FrameCodec(limit)


@pytest.mark.parametrize(
    "key,value",
    [
        ("wire_version", "1.1.0"),
        ("wire_version", "2.0.0"),
        ("sequence", True),
        ("sequence", "0"),
        ("request_digest", "a" * 64),
        ("endpoint", "http://localhost:8000"),
        ("payload", {"op": "TORQUE"}),
    ],
)
def test_wire_rejects_unknown_versions_fields_operations_and_coercion(
    key: str, value: object
) -> None:
    body = hello().model_dump(mode="json")
    body[key] = value
    with pytest.raises(RoboticsError):
        parse_message(json.dumps(body).encode())


def test_sealed_writer_keeps_original_spelling_and_cannot_execute_upcast() -> None:
    original = json.dumps(fixture("ActionIntent"), indent=4)
    record = IntentRecord(original_json=original)
    assert record.original_json == original
    assert (
        record.for_execution(ActionIntent).content_hash == fixture("ActionIntent")["content_hash"]
    )
    body = fixture("ActionIntent")
    body.update(schema_version="1.1.0", unknown_future=[1, 2])
    body["content_hash"] = content_hash(body)
    with pytest.raises(RoboticsError) as error:
        IntentRecord(original_json=json.dumps(body))
    assert error.value.code is Code.UNKNOWN_CONTRACT_VERSION


@pytest.mark.parametrize("alteration", ["nested", "same_version_extra", "duplicate"])
def test_writer_rejects_altered_seals_or_same_version_unknowns(alteration: str) -> None:
    body = fixture("ActionIntent")
    if alteration == "nested":
        body["state"]["observation_sequence"] += 1
    else:
        body["unknown_authority"] = True
        body["content_hash"] = content_hash(body)
    raw = json.dumps(body)
    if alteration == "duplicate":
        raw = raw[:-1] + ', "schema_version": "1.0.0"}'
    with pytest.raises(RoboticsError):
        IntentRecord(original_json=raw)


def test_correlator_accepts_once_and_rejects_unsolicited_response() -> None:
    request = hello()
    response = ProtocolResponse.create(request, SuccessOutcome(payload=HelloResult()))
    tracker = ResponseCorrelator()
    tracker.begin(request)
    assert tracker.accept(response) == response
    with pytest.raises(RoboticsError):
        tracker.accept(response)
    with pytest.raises(RoboticsError):
        tracker.begin(request)


@pytest.mark.parametrize(
    "field", ["request_id", "request_digest", "sequence", "scope", "response_digest"]
)
def test_correlation_failure_after_mutation_burns_session(field: str) -> None:
    harness = Harness()
    request = harness.request(
        ResetRequest(
            seed=harness.episode.seed,
            randomization_sample_hash=harness.episode.randomization_sample_hash,
        )
    )
    response = ProtocolResponse.create(request, ErrorOutcome(code=Code.SAFETY_DENIED))
    values = response.model_dump(mode="python")
    values[field] = {
        "request_id": "other",
        "request_digest": "a" * 64,
        "sequence": 999,
        "scope": None,
        "response_digest": "b" * 64,
    }[field]
    if field != "response_digest":
        values["response_digest"] = content_hash(values, exclude=("response_digest",))
    bad = response.model_copy(
        update={field: values[field], "response_digest": values["response_digest"]}
    )
    tracker = ResponseCorrelator()
    tracker.begin(request)
    with pytest.raises(RoboticsError) as error:
        tracker.accept(bad)
    assert error.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN
    with pytest.raises(RoboticsError):
        tracker.begin(hello())


def test_timeout_and_single_inflight_rule() -> None:
    tracker = ResponseCorrelator()
    tracker.begin(hello())
    with pytest.raises(RoboticsError):
        tracker.begin(hello())
    with pytest.raises(RoboticsError) as error:
        tracker.fail_pending()
    assert error.value.code is Code.ADAPTER_CRASH


def test_mutating_scope_is_required_and_nested_mutation_cannot_keep_old_seal() -> None:
    harness = Harness()
    request = harness.request(
        ResetRequest(
            seed=harness.episode.seed,
            randomization_sample_hash=harness.episode.randomization_sample_hash,
        )
    )
    assert request.scope is not None
    request.scope.lease.generation += 1
    with pytest.raises((RoboticsError, ValidationError)):
        FrameCodec().encode(request)
    assert harness.session.pins.episode.lease.generation == 1
    with pytest.raises(ValueError):
        ProtocolRequest.create(request_id="bad", sequence=1, scope=None, payload=request.payload)


def test_response_error_contains_only_safe_error_code() -> None:
    response = ProtocolResponse.create(hello(), ErrorOutcome(code=Code.ARTIFACT_UNAVAILABLE))
    assert b"ARTIFACT_UNAVAILABLE" in canonical_json(response)
    with pytest.raises(ValidationError):
        ErrorOutcome.model_validate(
            {"status": "ERROR", "code": "ADAPTER_CRASH", "message": "/private/path"}
        )


@pytest.mark.parametrize(
    "changed", ["prepared_command_hash", "safety_decision_hash", "episode", "clock"]
)
def test_correlated_execute_response_cannot_substitute_result_claims(changed: str) -> None:
    from accretion.robotics.protocol import ExecuteResult

    harness = Harness()
    payload = harness.execute_payload()
    request = harness.request(payload)
    response = harness.session.dispatch(request)
    assert isinstance(response.outcome, SuccessOutcome)
    assert isinstance(response.outcome.payload, ExecuteResult)
    values = response.outcome.payload.model_dump(mode="python")
    if changed == "episode":
        values["observation"]["episode_id"] = "sep_" + "0" * 26
    elif changed == "clock":
        values["observation"] = harness.initial
    else:
        values[changed] = "a" * 64
    substituted = ProtocolResponse.create(
        request, SuccessOutcome(payload=ExecuteResult.model_validate(values))
    )
    tracker = ResponseCorrelator()
    tracker.begin(request)
    with pytest.raises(RoboticsError) as error:
        tracker.accept(substituted)
    assert error.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN


def test_client_detects_bad_prepare_claim_before_consuming_authority() -> None:
    from v05_sdk_fixtures import replace_contract

    from accretion.contracts.robotics import PreparedCommand
    from accretion.robotics.protocol import PreparedRecord, PrepareRequest, PrepareResult

    harness = Harness()
    intent = harness.make_intent()
    request = harness.request(PrepareRequest(intent=IntentRecord.from_contract(intent)))
    response = harness.session.dispatch(request)
    assert isinstance(response.outcome, SuccessOutcome)
    assert isinstance(response.outcome.payload, PrepareResult)
    prepared = response.outcome.payload.prepared.for_execution(PreparedCommand)
    changed = replace_contract(prepared, action_intent_hash="a" * 64)
    substituted = ProtocolResponse.create(
        request,
        SuccessOutcome(payload=PrepareResult(prepared=PreparedRecord.from_contract(changed))),
    )
    tracker = ResponseCorrelator()
    tracker.begin(request)
    with pytest.raises(RoboticsError):
        tracker.accept(substituted)
    assert harness.adapter.execute_calls == harness.adapter.advance_count == 0


def test_client_rejects_unauthorized_observe_advance_and_closes_connection() -> None:
    from accretion.robotics.protocol import ObservationResult, ObserveRequest
    from accretion.robotics.testing.fault_adapter import Fault

    harness = Harness()
    request = harness.request(ObserveRequest())
    harness.adapter.fault = Fault.OBSERVE_ADVANCES
    response = ProtocolResponse.create(
        request,
        SuccessOutcome(
            payload=ObservationResult(op="OBSERVE", observation=harness.adapter.observe())
        ),
    )
    tracker = ResponseCorrelator()
    tracker.begin(request)
    with pytest.raises(RoboticsError):
        tracker.accept(response)
    with pytest.raises(RoboticsError):
        tracker.begin(hello())
