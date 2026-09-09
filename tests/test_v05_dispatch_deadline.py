"""Synthetic dispatch expiry witnesses on Memory/PG; no host or simulator calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from test_v05_authority import Lab, dispatch_candidate
from test_v05_authority import lab as authority_lab

from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import ProtocolRequest, TerminateRequest
from accretion.robotics.runtime_store import DispatchReservation, RuntimeTransaction

lab = authority_lab


async def candidate(lab: Lab, operation: str):
    if operation != "TERMINATE":
        return await dispatch_candidate(lab, operation)
    ep = await lab.running()
    assert lab.approval
    pins = lab.authority.execution_pins(ep, lab.approval)
    request = ProtocolRequest.create(
        request_id="synthetic-deadline-termination",
        sequence=ep.last_request_sequence + 1,
        scope=pins.command_scope(),
        payload=TerminateRequest(),
    )
    return ep, request, pins


def provider_cap(lab: Lab, deadline: datetime) -> None:
    """Explicit test-only current provider cap, never installed operationally."""
    original = lab.trust.check

    async def check(tx, request):
        await original(tx, request)
        tx.require_valid_interval(deadline - timedelta(hours=2), deadline, Code.CAPABILITY_DENIED)

    lab.trust.check = check


@pytest.mark.parametrize("operation", ["RESET", "EXECUTE", "TERMINATE"])
@pytest.mark.parametrize("shortest", ["lease", "heartbeat", "preflight", "approval"])
@pytest.mark.parametrize("provider", ["none", "later", "earlier"])
async def test_new_dispatch_always_carries_minimum_actual_authority(
    lab: Lab, operation: str, shortest: str, provider: str
):
    if shortest == "lease":
        lab.lease_lifetime_seconds = lab.heartbeat_timeout_seconds = 100
    elif shortest == "heartbeat":
        lab.heartbeat_timeout_seconds = 100
    elif shortest == "preflight":
        lab.preflight_lifetime_seconds = 100
    else:
        lab.approval_lifetime_seconds = 100
    ep, request, pins = await candidate(lab, operation)
    assert lab.lease and lab.approval
    async with lab.authority.store.transaction(lab.project) as tx:
        preflight = await lab.authority._preflight(tx, lab.scope(), ep, lab.lease)
    mandatory = min(
        lab.lease.expires_at,
        lab.lease.heartbeat_deadline,
        preflight.valid_until,
        lab.approval.expires_at,
    )
    expected = mandatory
    if provider != "none":
        configured = datetime.now(UTC) + timedelta(seconds=30 if provider == "earlier" else 3600)
        provider_cap(lab, configured)
        expected = min(mandatory, configured)
    admission = await lab.authority.reserve_dispatch(
        lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
    )
    assert admission.fresh and admission.reservation
    assert admission.reservation.authority_valid_until == expected
    async with lab.authority.store.transaction(lab.project) as tx:
        stored = await tx.get(DispatchReservation, request.request_digest)
    assert stored == admission.reservation


async def test_signed_execution_includes_key_cap_loaded_after_initial_policy(lab: Lab):
    ep, request, pins = await candidate(lab, "EXECUTE")
    cap = datetime.now(UTC) + timedelta(seconds=30)
    original = lab.trust.keys

    async def keys(tx, check):
        result = await original(tx, check)
        tx.require_valid_interval(cap - timedelta(minutes=1), cap, Code.SAFETY_DENIED)
        return result

    lab.trust.keys = keys
    admission = await lab.authority.reserve_dispatch(
        lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
    )
    assert admission.reservation and admission.reservation.authority_valid_until == cap


@pytest.mark.parametrize(
    ("operation", "boundary"),
    [
        (operation, boundary)
        for operation in ("RESET", "EXECUTE", "TERMINATE")
        for boundary in (
            "lease",
            "heartbeat",
            "preflight",
            "approval",
            "provider",
            "signed_issuance",
        )
        if boundary != "signed_issuance" or operation == "EXECUTE"
    ],
)
async def test_last_commit_clock_rejects_expired_or_regressed_dispatch_atomically(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, operation: str, boundary: str
):
    if boundary == "lease":
        lab.lease_lifetime_seconds = lab.heartbeat_timeout_seconds = 120
    elif boundary == "heartbeat":
        lab.heartbeat_timeout_seconds = 120
    elif boundary == "preflight":
        lab.preflight_lifetime_seconds = 120
    elif boundary == "approval":
        lab.approval_lifetime_seconds = 120
    ep, request, pins = await candidate(lab, operation)
    assert lab.lease and lab.approval
    if boundary == "provider":
        target = datetime.now(UTC) + timedelta(seconds=30)
        provider_cap(lab, target)
        code = Code.CAPABILITY_DENIED
    elif boundary == "signed_issuance":
        target = lab.case.context.now - timedelta(microseconds=1)
        code = Code.SAFETY_DENIED
    elif boundary == "heartbeat":
        target, code = lab.lease.heartbeat_deadline, Code.LEASE_INVALID
    elif boundary == "lease":
        target, code = lab.lease.expires_at, Code.LEASE_INVALID
    elif boundary == "preflight":
        async with lab.authority.store.transaction(lab.project) as tx:
            preflight = await lab.authority._preflight(tx, lab.scope(), ep, lab.lease)
        target, code = preflight.valid_until, Code.PREFLIGHT_FAILED
    else:
        target, code = lab.approval.expires_at, Code.APPROVAL_INVALID
    scope = lab.scope(revision=ep.revision)
    async with lab.authority.store.transaction(lab.project) as tx:
        events = await tx.registry.events(ep.binding_id, 0, 100)
    original_now = RuntimeTransaction.now
    original_validate = RuntimeTransaction.validate_time_guards
    late = False

    async def now(tx):
        actual = await original_now(tx)  # Retain the real PostgreSQL clock query.
        return target if late else actual

    async def validate(tx):
        nonlocal late
        late = True
        await original_validate(tx)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeTransaction, "now", now)
        patch.setattr(RuntimeTransaction, "validate_time_guards", validate)
        with pytest.raises(RoboticsError) as error:
            await lab.authority.reserve_dispatch(
                scope, episode_id=ep.id, request=request, pins=pins
            )
        assert error.value.code is code
    assert late
    assert await lab.episode() == ep
    async with lab.authority.store.transaction(lab.project) as tx:
        assert await tx.get(DispatchReservation, request.request_digest) is None
        assert await tx.registry.events(ep.binding_id, 0, 100) == events
        assert (
            await tx.registry.ledger(lab.authority._ledger_scope(scope, "dispatch", ep.id)) is None
        )


async def test_changed_provider_cap_does_not_reissue_historical_reservation(lab: Lab):
    ep, request, pins = await candidate(lab, "RESET")
    scope = lab.scope(revision=ep.revision, key="historical-deadline")
    first = await lab.authority.reserve_dispatch(
        scope, episode_id=ep.id, request=request, pins=pins
    )
    cap = datetime.now(UTC) + timedelta(seconds=30)
    provider_cap(lab, cap)
    retry = await lab.authority.reserve_dispatch(
        scope, episode_id=ep.id, request=request, pins=pins
    )
    assert not retry.fresh and retry.reservation == first.reservation
    assert retry.reservation and retry.reservation.authority_valid_until > cap
