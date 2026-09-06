"""The three read/evaluate routes M8.2 adds, and the lineage AC4-M8-042 is about.

**AC4-M8-042** — router lineage and the promotion report are inspectable. The lineage route
answers three questions a caller would otherwise join by hand and could join inconsistently:
what this version descends from, everything that has happened to its family, and which
reports authorised those acts. The test below promotes a real candidate through the real
service first, so the activation history it reads is one the ledger actually wrote; dropping
``activations`` from the response, or filtering them to entries that name this version, fails
it.

**The snapshots here are cut under the empty vocabulary.** The HTTP route builds its gate from
what the deployment wired and therefore takes
:meth:`~accretion.routing.features.Vocabulary.frozen_over`'s default table, which is the same
one :mod:`accretion.routing.coldstart` featurizes under. A bench whose snapshots were sealed
under another one would be refused by ``materialize`` — correctly — and the route would be
untestable for a reason that has nothing to do with the route.

There is no ``conftest.py``. The bench is the evaluator module's, imported and not edited;
``app.state`` is installed and torn down by each test here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from test_v04_m4_train import frozen_clock
from test_v04_m8_evaluator import Bench, seed_shadow_stage, setup_bench
from test_v04_m8_promotion import APPROVER, OPERATOR

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.contracts import (
    Principal,
    PrincipalRef,
    PrincipalStatus,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.routing import (
    RouterActivationKind,
    RouterPromotionDecision,
    RouterStatus,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.routing.activation import ActivationLedger
from accretion.routing.features import Vocabulary
from accretion.routing.promotion import PromotionService, RollbackDrill
from accretion.routing.training_snapshot import SnapshotRules

REPORT_PATH = "/api/v1/router-promotions/{report_id}"
EVALUATE_PATH = "/api/v1/router-promotions"
LINEAGE_PATH = "/api/v1/router-models/{version_id}/lineage"

EMPTY_RULES = SnapshotRules.over()
EMPTY_VOCABULARY = Vocabulary.frozen_over()


class WiredBench:
    """A bench plus the promotion service the routes read off ``app.state``."""

    def __init__(
        self,
        bench: Bench,
        service: PromotionService,
        owner: Principal,
        member: Principal,
        outsider: Principal,
    ) -> None:
        self.bench = bench
        self.service = service
        self.owner = owner
        self.member = member
        self.outsider = outsider

    @property
    def store(self) -> Any:
        return self.bench.store

    @property
    def workspace_id(self) -> str:
        return self.bench.workspace_id


async def setup_wired(tmp_path: Path, **overrides: Any) -> WiredBench:
    """One workspace with an owner, a plain member and a stranger, and a wired service.

    Three principals and not two: the read routes stop at membership and the mutating one
    does not, and a bench with only an owner and an outsider could not tell those two rules
    apart.
    """

    bench = await setup_bench(
        tmp_path, rules=EMPTY_RULES, vocabulary=EMPTY_VOCABULARY, **overrides
    )
    suffix = uuid4().hex[:8]
    owner = Principal(
        principal_id=f"usr_owner_{suffix}", issuer="test", subject=f"owner-{suffix}"
    )
    member = Principal(
        principal_id=f"usr_member_{suffix}", issuer="test", subject=f"member-{suffix}"
    )
    outsider = Principal(
        principal_id=f"usr_outsider_{suffix}", issuer="test", subject=f"outsider-{suffix}"
    )
    for principal in (owner, member, outsider):
        await bench.store.upsert_principal(principal)
    await bench.store.upsert_workspace(
        WorkspaceEntity(workspace_id=bench.workspace_id, name="v0.4 M8.2")
    )
    for principal, role in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.DEVELOPER)):
        await bench.store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=bench.workspace_id,
                principal_id=principal.principal_id,
                role=role,
            )
        )
    service = PromotionService(
        bench.store,
        ActivationLedger(bench.store),
        RollbackDrill(bench.loader, bench.artifacts),
        clock=frozen_clock,
        created_by=PrincipalRef(
            principal_id=OPERATOR,
            display_name="Accretion promotion service",
            status=PrincipalStatus.ACTIVE,
        ),
    )
    return WiredBench(bench, service, owner, member, outsider)


def install_app_state(wired: WiredBench, who: Principal) -> None:
    app.state.router_admin = wired.service
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(wired.store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )


def clear_app_state() -> None:
    for attribute in ("auth", "router_admin"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


async def call(method: str, url: str, **kwargs: Any) -> Any:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        return await client.request(method, url, **kwargs)


def evaluation_body(wired: WiredBench, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "candidate_version_id": wired.bench.candidate.contract_id,
        "baseline_version_id": wired.bench.baseline.contract_id,
        "holdout_snapshot_id": wired.bench.holdout.contract_id,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------------------
# POST /api/v1/router-promotions
# --------------------------------------------------------------------------------------


async def test_the_evaluation_route_seals_a_report_and_returns_it(tmp_path: Path) -> None:
    """The gate reached over HTTP produces the same sealed document a direct call does.

    Asserted against what the store gives back rather than against the response body, so a
    route that returned a well-formed report it never persisted fails here.
    """

    wired = await setup_wired(tmp_path)
    await seed_shadow_stage(wired.bench)
    install_app_state(wired, wired.owner)
    try:
        response = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
            json=evaluation_body(wired),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        stored = await wired.store.get_router_promotion_report(body["contract_id"])
        assert stored is not None
        assert stored.content_hash == body["content_hash"]
        assert stored.decision is RouterPromotionDecision.PROMOTE
        assert stored.rollback_target == wired.bench.baseline.contract_id
    finally:
        clear_app_state()


async def test_a_plain_member_cannot_ask_for_an_evaluation(tmp_path: Path) -> None:
    """Producing the report a later promotion will be judged by needs the same authority.

    403 and not 404: the caller named a workspace they belong to, so nothing is being hidden
    from them — they simply may not do this.
    """

    wired = await setup_wired(tmp_path)
    install_app_state(wired, wired.member)
    try:
        response = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
            json=evaluation_body(wired),
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"
        assert (
            await wired.store.list_router_promotion_reports(
                workspace_id=wired.workspace_id
            )
            == []
        )
    finally:
        clear_app_state()


async def test_evaluating_without_an_idempotency_key_is_refused(tmp_path: Path) -> None:
    """SDD §11: a mutating endpoint states its key or is refused, and seals nothing first."""

    wired = await setup_wired(tmp_path)
    install_app_state(wired, wired.owner)
    try:
        response = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            json=evaluation_body(wired),
        )
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        assert (
            await wired.store.list_router_promotion_reports(
                workspace_id=wired.workspace_id
            )
            == []
        )
    finally:
        clear_app_state()


async def test_a_replayed_evaluation_returns_the_first_report_rather_than_a_second(
    tmp_path: Path,
) -> None:
    """The property a client retrying a timed-out request depends on."""

    wired = await setup_wired(tmp_path)
    await seed_shadow_stage(wired.bench)
    install_app_state(wired, wired.owner)
    key = f"idem-{uuid4().hex[:8]}"
    try:
        first = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            headers={"Idempotency-Key": key},
            json=evaluation_body(wired),
        )
        second = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            headers={"Idempotency-Key": key},
            json=evaluation_body(wired),
        )
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["contract_id"] == second.json()["contract_id"]
        assert (
            len(
                await wired.store.list_router_promotion_reports(
                    workspace_id=wired.workspace_id
                )
            )
            == 1
        )
    finally:
        clear_app_state()


async def test_a_version_in_another_workspace_is_invisible_rather_than_forbidden(
    tmp_path: Path,
) -> None:
    """Tenancy is a 404, so administering one workspace discovers nothing about another."""

    wired = await setup_wired(tmp_path)
    other = f"wks_{uuid4().hex[:12]}"
    await wired.store.upsert_workspace(WorkspaceEntity(workspace_id=other, name="other"))
    await wired.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=other,
            principal_id=wired.owner.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    install_app_state(wired, wired.owner)
    try:
        response = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": other},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
            json=evaluation_body(wired),
        )
        assert response.status_code == 404, response.text
    finally:
        clear_app_state()


async def test_a_misspelt_field_in_the_body_is_a_422_and_not_a_silent_default(
    tmp_path: Path,
) -> None:
    """``extra="forbid"`` on the request body, so a typo cannot become a default."""

    wired = await setup_wired(tmp_path)
    install_app_state(wired, wired.owner)
    try:
        response = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
            json=evaluation_body(wired, candidate_version="rmv_typo"),
        )
        assert response.status_code == 422, response.text
    finally:
        clear_app_state()


async def test_a_leaking_holdout_returns_its_own_code_and_not_a_500(tmp_path: Path) -> None:
    """AC4-M8-036 over HTTP: the refusal reaches the wire as itself, in the error envelope."""

    from test_v04_m8_evaluator import TRAINING_WINDOW

    wired = await setup_wired(tmp_path, holdout_window=TRAINING_WINDOW)
    install_app_state(wired, wired.owner)
    try:
        response = await call(
            "POST",
            EVALUATE_PATH,
            params={"workspace_id": wired.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
            json=evaluation_body(wired),
        )
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "ROUTER_HOLDOUT_LEAKAGE"
        assert set(body) >= {"code", "message", "correlation_id", "retryable"}
        assert (
            await wired.store.list_router_promotion_reports(
                workspace_id=wired.workspace_id
            )
            == []
        )
    finally:
        clear_app_state()


# --------------------------------------------------------------------------------------
# GET /api/v1/router-promotions/{report_id}
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M8-042")
async def test_a_member_can_read_the_report_that_authorised_a_promotion(
    tmp_path: Path,
) -> None:
    """AC4-M8-042, first half: the promotion report is inspectable, not only writable.

    A plain member reads it: §10.3 makes the *act* a human decision by an administrator, and
    the audit of that act belongs to everyone whose work the router routes.
    """

    wired = await setup_wired(tmp_path)
    await seed_shadow_stage(wired.bench)
    report = await wired.bench.evaluate()
    install_app_state(wired, wired.member)
    try:
        response = await call(
            "GET",
            REPORT_PATH.format(report_id=report.contract_id),
            params={"workspace_id": wired.workspace_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["contract_id"] == report.contract_id
        assert body["decision"] == report.decision.value
        assert body["rollback_target"] == wired.bench.baseline.contract_id
        assert body["primary_metric_result"]["metric_id"] == "policy_value_improvement"
        assert {item["cohort_id"] for item in body["cohort_results"]} >= {"secrets"}
    finally:
        clear_app_state()


async def test_a_stranger_reading_a_report_is_refused_before_it_is_looked_up(
    tmp_path: Path,
) -> None:
    """The membership check runs first, so a non-member learns nothing about the id."""

    wired = await setup_wired(tmp_path)
    await seed_shadow_stage(wired.bench)
    report = await wired.bench.evaluate()
    install_app_state(wired, wired.outsider)
    try:
        response = await call(
            "GET",
            REPORT_PATH.format(report_id=report.contract_id),
            params={"workspace_id": wired.workspace_id},
        )
        assert response.status_code == 403, response.text
    finally:
        clear_app_state()


async def test_a_report_read_under_the_wrong_workspace_is_a_404(tmp_path: Path) -> None:
    """A report in another workspace is absent rather than forbidden."""

    wired = await setup_wired(tmp_path)
    await seed_shadow_stage(wired.bench)
    report = await wired.bench.evaluate()
    other = f"wks_{uuid4().hex[:12]}"
    await wired.store.upsert_workspace(WorkspaceEntity(workspace_id=other, name="other"))
    await wired.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=other,
            principal_id=wired.member.principal_id,
            role=WorkspaceRole.DEVELOPER,
        )
    )
    install_app_state(wired, wired.member)
    try:
        response = await call(
            "GET",
            REPORT_PATH.format(report_id=report.contract_id),
            params={"workspace_id": other},
        )
        assert response.status_code == 404, response.text
    finally:
        clear_app_state()


# --------------------------------------------------------------------------------------
# GET /api/v1/router-models/{version_id}/lineage
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M8-042")
async def test_the_lineage_route_returns_the_parent_chain_the_history_and_the_report_ids(
    tmp_path: Path,
) -> None:
    """AC4-M8-042, second half: where this router came from and everything done to it.

    A real promotion runs first, through the real service and the real ledger, so the
    activation the route reports is one that was actually written. Three things are asserted
    and each is a different join: the ``parent_version_id`` chain reaches the candidate the
    promoted row was minted from; the activation history holds the ``PROMOTE`` entry with its
    sequence; and the report that authorised it is named. Dropping ``activations`` — or
    filtering them to entries mentioning this version — loses the second and the third.
    """

    wired = await setup_wired(tmp_path)
    await seed_shadow_stage(wired.bench)
    report = await wired.bench.evaluate()
    assert report.decision is RouterPromotionDecision.PROMOTE
    activation = await wired.service.promote(report.contract_id, APPROVER)
    install_app_state(wired, wired.member)
    try:
        response = await call(
            "GET",
            LINEAGE_PATH.format(version_id=activation.router_version_id),
            params={"workspace_id": wired.workspace_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()

        chain = [entry["version_id"] for entry in body["parent_chain"]]
        assert chain[0] == activation.router_version_id
        assert wired.bench.candidate.contract_id in chain
        assert body["parent_chain"][0]["status"] == RouterStatus.ACTIVE.value

        assert [entry["sequence"] for entry in body["activations"]] == [1]
        assert body["activations"][0]["kind"] == RouterActivationKind.PROMOTE.value
        assert body["promotion_report_ids"] == [report.contract_id]
        assert body["active_version_id"] == activation.router_version_id
        assert body["rollback_target_version_id"] == wired.bench.baseline.contract_id
        assert body["family_key"] == "workspace"
    finally:
        clear_app_state()


async def test_a_version_with_no_activation_yet_has_a_chain_and_an_empty_history(
    tmp_path: Path,
) -> None:
    """A candidate nobody promoted is lineage too, and its head is ``None`` rather than absent."""

    wired = await setup_wired(tmp_path)
    install_app_state(wired, wired.member)
    try:
        response = await call(
            "GET",
            LINEAGE_PATH.format(version_id=wired.bench.candidate.contract_id),
            params={"workspace_id": wired.workspace_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [entry["version_id"] for entry in body["parent_chain"]] == [
            wired.bench.candidate.contract_id
        ]
        assert body["activations"] == []
        assert body["promotion_report_ids"] == []
        assert body["active_version_id"] is None
    finally:
        clear_app_state()


async def test_the_lineage_of_a_version_in_another_workspace_is_a_404(
    tmp_path: Path,
) -> None:
    """Tenancy again: the version must belong to the workspace the caller named."""

    wired = await setup_wired(tmp_path)
    other = f"wks_{uuid4().hex[:12]}"
    await wired.store.upsert_workspace(WorkspaceEntity(workspace_id=other, name="other"))
    await wired.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=other,
            principal_id=wired.member.principal_id,
            role=WorkspaceRole.DEVELOPER,
        )
    )
    install_app_state(wired, wired.member)
    try:
        response = await call(
            "GET",
            LINEAGE_PATH.format(version_id=wired.bench.candidate.contract_id),
            params={"workspace_id": other},
        )
        assert response.status_code == 404, response.text
    finally:
        clear_app_state()


async def test_every_new_route_refuses_when_no_promotion_service_is_wired(
    tmp_path: Path,
) -> None:
    """A deployment that never started the subsystem says so, rather than raising a 500."""

    wired = await setup_wired(tmp_path)
    install_app_state(wired, wired.owner)
    delattr(app.state, "router_admin")
    try:
        for method, url, body in (
            ("GET", REPORT_PATH.format(report_id="rpr_missing"), None),
            ("GET", LINEAGE_PATH.format(version_id="rmv_missing"), None),
            ("POST", EVALUATE_PATH, evaluation_body(wired)),
        ):
            response = await call(
                method,
                url,
                params={"workspace_id": wired.workspace_id},
                headers={"Idempotency-Key": "idem-unwired"},
                json=body,
            )
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "ROUTER_ADMIN_UNAVAILABLE"
    finally:
        clear_app_state()
