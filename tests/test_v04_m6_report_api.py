"""M6.2's read side: the shadow report over HTTP, and what it refuses to invent.

Every claim here is about something a later milestone could break by accident. That the report
pairs a shadow recommendation with *both* the executed outcome and its CONTROL arm, and counts
only the trials where both arms exist. That the gates it still owes are named rather than
summarised into a boolean. That a version in another workspace is absent rather than forbidden.
That a policy nobody has shadowed yet answers with an empty report instead of a 500, which is
what a dashboard polling a minute-old policy actually receives.

The records are built from the committed golden fixtures through ``test_v04_m0_store.build``, for
the reason that file gives: a test that invented its own ``ShadowRolloutResult`` would prove the
pairing works on whatever this file thinks a rollout looks like.

The route tests share ``app`` with every other API test, so each installs its state and clears it
in a ``finally``; there is no ``conftest.py`` in this repository and none is added here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from test_v04_m0_store import (
    FIXTURE_WORKSPACE_ID,
    build,
    digest,
    new_store,
)

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.api.shadow import REPORT_CONFIG, SHADOW_POLICIES_PATH
from accretion.contracts import (
    Principal,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.routing import (
    RouterModelVersion,
    RouterStatus,
    ShadowDecision,
    ShadowRolloutKind,
    ShadowRolloutResult,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.shadow import (
    NON_INFERIORITY_GATE,
    PAIRED_RUNS_GATE,
    ShadowEvaluator,
)
from accretion.routing.train import ACCEPTANCE_LABEL, CALIBRATION_REPORT_LABEL

EVALUATED_LABELS = {
    ACCEPTANCE_LABEL: digest("holdout"),
    CALIBRATION_REPORT_LABEL: digest("calibration"),
}


def report_path(version_id: str) -> str:
    return f"{SHADOW_POLICIES_PATH}/{version_id}/report"


def shadow_version(**overrides: Any) -> RouterModelVersion:
    """A registered SHADOW stage, as ``ShadowEvaluator.register`` would have written one."""

    fields: dict[str, Any] = {
        "status": RouterStatus.SHADOW.value,
        "labels": dict(EVALUATED_LABELS),
        "artifact_digest": digest(f"artifact-{uuid4().hex}"),
        "calibration_artifact_digest": digest(f"calibration-{uuid4().hex}"),
    }
    fields.update(overrides)
    return build(RouterModelVersion, **fields)


def decision(version_id: str, *, agreement: bool = False, **overrides: Any) -> ShadowDecision:
    """One recorded comparison. Agreement is spelled as two equal hashes, never as a flag.

    ``ShadowDecision`` refuses a decision that claims agreement while its two configuration
    hashes disagree, so a disagreeing pair names two hashes and an agreeing pair names one twice.
    """

    executed = digest("executed-configuration")
    fields: dict[str, Any] = {
        "shadow_router_version_id": version_id,
        "executed_receipt_id": new_id("routing_receipt"),
        "shadow_receipt_id": new_id("routing_receipt"),
        "executed_configuration_hash": executed,
        "shadow_configuration_hash": (
            executed if agreement else digest("shadow-configuration")
        ),
        "agreement": agreement,
    }
    fields.update(overrides)
    return build(ShadowDecision, **fields)


def rollout(
    decision_id: str,
    kind: ShadowRolloutKind,
    trial_index: int,
    *,
    quality: float,
    cost: float = 0.1,
    latency_ms: float = 10_000.0,
) -> ShadowRolloutResult:
    return build(
        ShadowRolloutResult,
        shadow_decision_id=decision_id,
        kind=kind.value,
        trial_index=trial_index,
        fork_execution_id=new_id("execution_instance"),
        observed={
            "quality": quality,
            "cost": cost,
            "latency_ms": latency_ms,
            "verified": False,
        },
    )


async def setup_report(
    tmp_path: Path,
    *,
    pairs: int = 2,
    drop_control: bool = False,
) -> tuple[MemoryStore, RouterModelVersion, Principal, Principal]:
    """A workspace, one SHADOW version, ``pairs`` decisions and the rollout arms behind them.

    The SHADOW arm always scores better than its CONTROL partner, so a report built from these
    rows has a positive mean delta and a reader can tell a working pairing from one that scored
    the arms the other way round.
    """

    store = await new_store()
    version = await store.put_router_model_version(shadow_version())
    for index in range(pairs):
        record = await store.put_shadow_decision(
            decision(version.contract_id, agreement=index % 2 == 0)
        )
        await store.put_shadow_rollout_result(
            rollout(record.contract_id, ShadowRolloutKind.SHADOW, 0, quality=0.9)
        )
        if not drop_control:
            await store.put_shadow_rollout_result(
                rollout(record.contract_id, ShadowRolloutKind.CONTROL, 0, quality=0.5)
            )

    suffix = uuid4().hex[:8]
    owner = Principal(
        principal_id=f"usr_owner_{suffix}", issuer="test", subject=f"owner-{suffix}"
    )
    outsider = Principal(
        principal_id=f"usr_outsider_{suffix}", issuer="test", subject=f"outsider-{suffix}"
    )
    for principal in (owner, outsider):
        await store.upsert_principal(principal)
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id=FIXTURE_WORKSPACE_ID, name="v0.4 M6.2")
    )
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=FIXTURE_WORKSPACE_ID,
            principal_id=owner.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    return store, version, owner, outsider


def install_app_state(store: MemoryStore, artifacts: Path, who: Principal) -> None:
    """Wire ``app`` the way the lifespan does, for one principal."""

    app.state.manager = type("Manager", (), {"store": store})()
    app.state.shadow = ShadowEvaluator(store, ArtifactStore(artifacts))
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )


def clear_app_state() -> None:
    for attribute in ("auth", "shadow", "manager"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


async def call(method: str, url: str, **kwargs: Any) -> Any:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        return await client.request(method, url, **kwargs)


@pytest.mark.acceptance("AC4-M6-041")
async def test_the_report_pairs_every_recommendation_with_its_executed_and_control_arms(
    tmp_path: Path,
) -> None:
    """AC4-M6-041 on the wire: a pair is a recommendation, an executed outcome and a CONTROL arm.

    Each ``ShadowPair`` in the body names the executed receipt the recommendation was recorded
    beside *and* the two rollout rows that measured it, and ``paired_count`` counts only the
    trials that produced both arms. The delta is positive because the SHADOW arm outscored its
    partner on quality, which is what distinguishes a working pairing from one that subtracted
    the arms the other way round.
    """

    store, version, owner, _ = await setup_report(tmp_path, pairs=2)
    install_app_state(store, tmp_path / "artifacts", owner)
    try:
        response = await call("GET", report_path(version.contract_id))
        assert response.status_code == 200
        body = response.json()
        assert body["version_id"] == version.contract_id
        assert body["paired_count"] == 2
        assert body["mean_delta"] > 0
        assert body["agreement_rate"] == 0.5
        assert len(body["pairs"]) == 2

        stored_decisions = {
            item.contract_id: item
            for item in await store.list_shadow_decisions(workspace_id=FIXTURE_WORKSPACE_ID)
        }
        executed_ids = {item.executed_receipt_id for item in stored_decisions.values()}
        stored_rows = {
            item.contract_id: item
            for item in await store.list_shadow_rollout_results(
                workspace_id=FIXTURE_WORKSPACE_ID
            )
        }
        for pair in body["pairs"]:
            assert pair["executed_receipt_id"] in executed_ids
            assert pair["shadow_result_id"] in stored_rows
            assert pair["control_result_id"] in stored_rows
            assert stored_rows[pair["shadow_result_id"]].kind is ShadowRolloutKind.SHADOW
            assert stored_rows[pair["control_result_id"]].kind is ShadowRolloutKind.CONTROL
            assert pair["observed_delta"] > 0
    finally:
        clear_app_state()


@pytest.mark.acceptance("AC4-M6-041")
async def test_a_recommendation_whose_control_arm_is_missing_is_listed_but_never_counted(
    tmp_path: Path,
) -> None:
    """Dropping the CONTROL arm takes ``paired_count`` to zero; a lone arm is not evidence.

    The decisions are still listed — the M9b UI has to be able to tell "shadowed and every fork
    failed" from "never shadowed" — but each of them carries no observed delta and no result ids,
    and the paired-runs gate reports zero.
    """

    store, version, owner, _ = await setup_report(tmp_path, pairs=2, drop_control=True)
    install_app_state(store, tmp_path / "artifacts", owner)
    try:
        response = await call("GET", report_path(version.contract_id))
        assert response.status_code == 200
        body = response.json()
        assert body["paired_count"] == 0
        assert body["mean_delta"] == 0.0
        assert body["non_inferior"] is False
        assert len(body["pairs"]) == 2
        assert all(pair["observed_delta"] is None for pair in body["pairs"])
        assert all(pair["control_result_id"] is None for pair in body["pairs"])
        gates = {gate["gate"]: gate for gate in body["remaining_gates"]}
        assert gates[PAIRED_RUNS_GATE]["met"] is False
        assert "0 complete pairs" in gates[PAIRED_RUNS_GATE]["evidence"]
    finally:
        clear_app_state()


@pytest.mark.acceptance("AC4-M6-041")
async def test_the_report_names_every_gate_the_stage_still_owes(tmp_path: Path) -> None:
    """Both §10.2 preconditions are named with the number that decided them, not summarised.

    ``non_inferior`` alone would tell an operator that the stage is not promotable without
    telling them which of the two bars it missed, and M8.2 quotes these strings into a refusal.
    """

    store, version, owner, _ = await setup_report(tmp_path, pairs=2)
    install_app_state(store, tmp_path / "artifacts", owner)
    try:
        body = (await call("GET", report_path(version.contract_id))).json()
        gates = {gate["gate"]: gate for gate in body["remaining_gates"]}
        assert set(gates) == {PAIRED_RUNS_GATE, NON_INFERIORITY_GATE}
        assert gates[PAIRED_RUNS_GATE]["met"] is False
        assert (
            f"2 complete pairs of the {REPORT_CONFIG.min_paired_runs} required"
            in gates[PAIRED_RUNS_GATE]["evidence"]
        )
        assert str(body["delta_lcb"]) in gates[NON_INFERIORITY_GATE]["evidence"]
        assert gates[NON_INFERIORITY_GATE]["met"] is body["non_inferior"]
    finally:
        clear_app_state()


async def test_a_version_nobody_has_shadowed_yet_answers_with_the_empty_report(
    tmp_path: Path,
) -> None:
    """A dashboard polling a minute-old policy gets a report, not the arithmetic's refusal.

    ``shadow_report`` raises on an empty decision list on purpose, and this asserts the route
    does not pass that through as a 500: zero pairs, not non-inferior, and both gates unmet.
    """

    store, version, owner, _ = await setup_report(tmp_path, pairs=0)
    install_app_state(store, tmp_path / "artifacts", owner)
    try:
        response = await call("GET", report_path(version.contract_id))
        assert response.status_code == 200
        body = response.json()
        assert body["paired_count"] == 0
        assert body["pairs"] == []
        assert body["non_inferior"] is False
        assert {gate["gate"] for gate in body["remaining_gates"]} == {
            PAIRED_RUNS_GATE,
            NON_INFERIORITY_GATE,
        }
        assert all(gate["met"] is False for gate in body["remaining_gates"])
    finally:
        clear_app_state()


async def test_a_shadow_stage_in_another_workspace_is_absent_rather_than_forbidden(
    tmp_path: Path,
) -> None:
    """404 and not 403: an error that refused would confirm the id names a real version."""

    store, _, owner, _ = await setup_report(tmp_path, pairs=1)
    foreign = await store.put_router_model_version(
        shadow_version(workspace_id=new_id("workspace_entity"))
    )
    install_app_state(store, tmp_path / "artifacts", owner)
    try:
        response = await call("GET", report_path(foreign.contract_id))
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
    finally:
        clear_app_state()


async def test_an_unknown_version_id_is_a_404_with_the_same_body_as_a_foreign_one(
    tmp_path: Path,
) -> None:
    """The two refusals are indistinguishable, which is the whole point of the convention."""

    store, _, owner, _ = await setup_report(tmp_path, pairs=1)
    install_app_state(store, tmp_path / "artifacts", owner)
    try:
        unknown = await call("GET", report_path(new_id("router_model_version")))
        foreign = await call(
            "GET",
            report_path(
                (
                    await store.put_router_model_version(
                        shadow_version(workspace_id=new_id("workspace_entity"))
                    )
                ).contract_id
            ),
        )
        assert unknown.status_code == foreign.status_code == 404
        assert unknown.json()["code"] == foreign.json()["code"]
    finally:
        clear_app_state()


async def test_the_report_route_reports_the_service_unavailable_rather_than_a_500(
    tmp_path: Path,
) -> None:
    """An app assembled without the M6 lifespan line answers a typed 409, as POST does."""

    store, version, owner, _ = await setup_report(tmp_path, pairs=1)
    install_app_state(store, tmp_path / "artifacts", owner)
    app.state.shadow = None
    try:
        response = await call("GET", report_path(version.contract_id))
        assert response.status_code == 409
        assert response.json()["code"] == "SHADOW_EVALUATION_UNAVAILABLE"
    finally:
        clear_app_state()


def test_the_report_route_is_published_and_is_not_exempt_from_authentication() -> None:
    """The generated TypeScript client is built from this document, so the path must be in it."""

    from accretion.api.auth import is_exempt
    from accretion.api.shadow import SHADOW_REPORT_PATH

    document = app.openapi()
    assert SHADOW_REPORT_PATH in document["paths"]
    get = document["paths"][SHADOW_REPORT_PATH]["get"]
    assert (
        get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ShadowReport"
    )
    assert not is_exempt(SHADOW_REPORT_PATH)
