"""M0 API syntax witnesses, not claims of durable API or simulator acceptance."""

from __future__ import annotations

import pytest

from accretion.api.robotics_preconditions import (
    MAX_REVISION,
    parse_if_match,
    require_idempotency_key,
    revision_etag,
)
from accretion.robotics.errors import RoboticsError, RoboticsErrorCode


@pytest.mark.parametrize("revision", [0, 1, 42, MAX_REVISION])
def test_exact_resource_revision_is_preserved(revision: int) -> None:
    assert parse_if_match(revision_etag(revision)) == revision


@pytest.mark.parametrize(
    "value",
    [
        "*",
        'W/"1"',
        '"1", "2"',
        "1",
        '"01"',
        '"-1"',
        '"1.0"',
        '"+1"',
        '" 1"',
        '"1" ',
        '"1"\r\n',
        '"١"',
        '"9223372036854775808"',
        '"9999999999999999999999999999999999999999999999999999999"',
        "",
    ],
)
def test_ambiguous_or_unbounded_revision_never_disables_concurrency(value: str) -> None:
    with pytest.raises(RoboticsError) as caught:
        parse_if_match(value)
    assert caught.value.code is RoboticsErrorCode.INVALID_REQUEST


def test_new_resource_and_missing_existing_resource_revision_are_distinct() -> None:
    assert parse_if_match(None, required=False) is None
    with pytest.raises(RoboticsError) as caught:
        parse_if_match(None)
    assert caught.value.status_code == 428
    assert caught.value.code is RoboticsErrorCode.REVISION_REQUIRED
    with pytest.raises(RoboticsError):
        parse_if_match("*", required=False)


@pytest.mark.parametrize("revision", [-1, True, 1.0, MAX_REVISION + 1])
def test_revision_output_refuses_bool_coercion_and_overflow(revision: object) -> None:
    with pytest.raises(ValueError):
        revision_etag(revision)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", " ", "x y", "x\t", "x\r\ny", "x" * 129, "🔑"])
def test_request_keys_cannot_smuggle_headers_or_unbounded_identity(value: str) -> None:
    with pytest.raises(RoboticsError) as caught:
        require_idempotency_key(value)
    assert caught.value.code is RoboticsErrorCode.INVALID_REQUEST


def test_idempotency_identity_is_not_trimmed_or_case_folded() -> None:
    assert require_idempotency_key("Episode:AbC-001") == "Episode:AbC-001"
    assert require_idempotency_key("x" * 128) == "x" * 128
    with pytest.raises(RoboticsError) as caught:
        require_idempotency_key(None)
    assert caught.value.code is RoboticsErrorCode.IDEMPOTENCY_REQUIRED


def test_uncertain_acknowledgement_public_error_does_not_expose_internal_cause() -> None:
    try:
        try:
            raise OSError("private-endpoint/internal-path?token=not-a-real-token")
        except OSError as cause:
            raise RoboticsError(RoboticsErrorCode.ACKNOWLEDGEMENT_UNCERTAIN) from cause
    except RoboticsError as error:
        assert error.__cause__ is not None
        detail = error.public_detail()
        assert "private-endpoint" not in str(detail)
        assert "token=" not in str(detail)
        assert "will not be resent" in detail["message"]
        assert "new episode" in detail["recovery_action"]


@pytest.mark.parametrize("code", list(RoboticsErrorCode))
def test_every_domain_failure_has_safe_stable_public_disposition(code: RoboticsErrorCode) -> None:
    error = RoboticsError(code)
    assert error.public_detail()["code"] == code.value
    assert error.message and error.recovery_action
    assert 400 <= error.status_code < 500
