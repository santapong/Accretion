
"""SDD §11.2's five feedback endpoints: what they forward, what they refuse, what they hide.

The pipeline under the router is the *real* one over a fresh ``MemoryStore`` rather than a
stand-in, because every interesting property of these routes is a property of the records they
leave behind: an idempotent POST is one that stored one row, and a resolved contradiction is a
new row rather than an edited one. Only the two tests about *forwarding* — which grade, whose
authority — use a hand-written recording pipeline, because what they assert is the call and not
its consequences.

Tenancy is asserted as absence throughout. A principal outside the workspace must receive the
same answer as one asking about a record that was never written, and the test that would catch
a 403 leaking the existence of another tenant's run is the one that pins the status code.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from test_v04_m3_pipeline import (
    PRINCIPAL,
    PROJECT_ID,
    SEALED_AT,
    WORKSPACE_ID,
    Scenario,
    build,
    route,
    setup_pipeline,
    v01_result,
)

from accretion.api.feedback import (
    CONTRADICTION_NOT_OPEN,
    FEEDBACK_UNAVAILABLE,
    router,
)
from accretion.contracts import (
    Principal,
    PrincipalRef,
    Run,
    VerificationStatus,
)
from accretion.contracts.routing import (
    ContradictionStatus,
    ExperienceRecord,
    VerificationState,
    Visibility,
)
from accretion.experience.models import ExperienceSourceKind
from accretion.feedback.experience import (
    EXPERIENCE_ID_LABEL,
    PROJECTION_REVISION_LABEL,
    record_signature_for,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.errors import RoutingError

EVIDENCE = ("git-diff-sha256:" + "a" * 64,)
OUTSIDER = Principal(
    principal_id="usr_5NOMEMBERSHIP00000000000000",
    issuer="test",
    subject="outsider",
    display_name="A principal of some other workspace",
)
INSIDER = Principal(
    principal_id=PRINCIPAL.principal_id,
    issuer="test",
    subject="member",
    display_name=PRINCIPAL.display_name,
)


class RecordingPipeline:
    """The pipeline seam with the store removed: what was asked, and by whom.

    Hand-written and not a mock, so that the assertions are about a recorded call rather than
    about a framework's idea of one, and so a test can make the pipeline refuse by setting
    ``failure`` instead of by patching an import.
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.projector = None
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.records: list[ExperienceRecord] = []

    async def record_local(self, **values: Any) -> Any:
        self.calls.append(("record_local", values))
        raise AssertionError("this fake is only used for the record_final route")

    async def record_final(self, **values: Any) -> list[ExperienceRecord]:
        self.calls.append(("record_final", values))
        return list(self.records)


def build_app(pipeline: object | None) -> FastAPI:
    """The router under the two exception handlers ``main.py`` already registers for it."""

    application = FastAPI()
    application.include_router(router)

    class _Manager:
        feedback_pipeline = pipeline

    application.state.manager = _Manager()
    application.state.principal = INSIDER

    @application.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = request.app.state.principal
        return await call_next(request)

    @application.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=404,
            content={"code": "NOT_FOUND", "message": f"Resource {exc.args[0]} was not found"},
        )

    @application.exception_handler(RoutingError)
    async def refused(request: Request, exc: RoutingError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    return application


def client_for(application: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


async def run_with_policy(scenario: Scenario) -> Run:
    """A second run of the same task that names the acceptance policy a verdict is graded by.

    The scenario's own run names none — ``record_final`` does not need one — and the §7.9
    ingestion route refuses a run that cannot say what its verifiers were required to do. This
    is that run, and it is a real row rather than a patched copy of the first.
    """

    policy = build_policy()
    await scenario.store.save_acceptance_policy(policy)
    run = Run(
        run_id=new_id("run"),
        task_id=scenario.task.envelope.task_id,
        project_id=PROJECT_ID,
        provider=scenario.run.provider,
        state=scenario.run.state,
        principal_id=PRINCIPAL.principal_id,
        session_id=scenario.run.session_id,
        acceptance_policy_id=policy.policy_id,
    )
    return await scenario.store.create_run(run)


def verification_payload(run: Run, verification_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "verification_result_id": verification_id,
        "run_id": run.run_id,
        "verifier_id": "git-diff",
        "verifier_version": "1.0.0",
        "target_ref": "workspace",
        "status": VerificationStatus.PASS.value,
        "configuration_hash": "c" * 64,
        "evidence_refs": list(EVIDENCE),
        "producer_session_id": run.session_id,
    }
    payload.update(overrides)
    return payload


async def put_record(scenario: Scenario, **overrides: Any) -> ExperienceRecord:
    """One stored §7.10 projection under the scenario's experience, filed as generation one."""

    document: dict[str, Any] = {
        "contract_id": new_id("experience"),
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "created_at": SEALED_AT.isoformat(),
        "contract_signature": record_signature_for(
            scenario.nodes["plan"]
        ).model_dump(mode="json"),
        "labels": {
            EXPERIENCE_ID_LABEL: scenario.experience.experience_id,
            PROJECTION_REVISION_LABEL: "1",
        },
    }
    document.update(overrides)
    return await scenario.store.put_experience_record(
        build(ExperienceRecord, **document),
        experience_id=scenario.experience.experience_id,
    )


async def test_the_same_verification_result_posted_twice_is_stored_once() -> None:
    """§8.2: a verifier retrying a submission must not create a second history of one verdict.

    The second call is a *retry*, seconds later, so the two records would differ in their
    stamps: an implementation that let the append-only store decide idempotence would answer
    the correct caller with an immutability conflict instead of the record it already made.
    """

    scenario = await setup_pipeline()
    instance = scenario.nodes["plan"].execution_instance_id
    payload = verification_payload(await run_with_policy(scenario), new_id("verification"))
    application = build_app(scenario.pipeline)

    async with client_for(application) as client:
        first = await client.post(
            f"/api/v1/node-executions/{instance}/verification-results", json=payload
        )
        # The retry arrives later: under a frozen clock the two records would be byte-identical
        # and the store's content addressing would absorb the second write on its own, which
        # is not the behaviour under test. Advancing the clock makes the retried record differ
        # in ``signed_at``, so only the route's ``source_verification_id`` lookup can keep the
        # history to one row.
        scenario.pipeline.clock = lambda: SEALED_AT + timedelta(seconds=30)
        second = await client.post(
            f"/api/v1/node-executions/{instance}/verification-results", json=payload
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["contract_id"] == second.json()["contract_id"]
    stored = await scenario.store.list_verification_results(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID
    )
    assert [record.contract_id for record in stored] == [first.json()["contract_id"]]
    assert stored[0].execution_instance_id == instance
    assert stored[0].signed_at == SEALED_AT


async def test_a_verification_result_for_another_workspaces_run_is_not_found() -> None:
    """A cross-tenant write is refused as an absence, never as a forbidden.

    404 and not 403: the run id is a real one, and a permission error would confirm that to a
    caller who is not allowed to know it exists.
    """

    scenario = await setup_pipeline()
    instance = scenario.nodes["plan"].execution_instance_id
    run = await run_with_policy(scenario)
    application = build_app(scenario.pipeline)
    application.state.principal = OUTSIDER

    async with client_for(application) as client:
        response = await client.post(
            f"/api/v1/node-executions/{instance}/verification-results",
            json=verification_payload(run, new_id("verification")),
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert not await scenario.store.list_verification_results(workspace_id=WORKSPACE_ID)


async def test_search_narrows_by_the_signature_parts_and_by_eligibility() -> None:
    """Each filter constrains and an unstated one does not, which is the whole contract.

    A search that ANDed an absent parameter as ``None`` would return nothing for every partial
    question, and one that ignored a stated parameter would return another node's history to
    an operator investigating this one.
    """

    scenario = await setup_pipeline()
    signature = record_signature_for(scenario.nodes["plan"])
    wanted = await put_record(scenario, eligible_for_learning=True)
    ineligible = await put_record(scenario, eligible_for_learning=False)
    other = await put_record(
        scenario,
        contract_signature=signature.model_copy(
            update={"objective_digest": "f" * 64}
        ).model_dump(mode="json"),
    )
    application = build_app(scenario.pipeline)

    async with client_for(application) as client:
        everything = await client.get(
            "/api/v1/experiences/search", params={"workspace_id": WORKSPACE_ID}
        )
        by_objective = await client.get(
            "/api/v1/experiences/search",
            params={
                "workspace_id": WORKSPACE_ID,
                "objective_digest": signature.objective_digest,
            },
        )
        only_eligible = await client.get(
            "/api/v1/experiences/search",
            params={"workspace_id": WORKSPACE_ID, "eligible_only": True},
        )

    assert {record["contract_id"] for record in everything.json()} == {
        wanted.contract_id,
        ineligible.contract_id,
        other.contract_id,
    }
    assert {record["contract_id"] for record in by_objective.json()} == {
        wanted.contract_id,
        ineligible.contract_id,
    }
    assert [record["contract_id"] for record in only_eligible.json()] == [
        wanted.contract_id
    ]


async def test_a_record_in_another_workspace_reads_as_missing_rather_than_forbidden(
) -> None:
    """The same absence rule on the read path, asserted on a record that really exists."""

    scenario = await setup_pipeline()
    record = await put_record(scenario)
    application = build_app(scenario.pipeline)
    application.state.principal = OUTSIDER

    async with client_for(application) as client:
        response = await client.get(f"/api/v1/experiences/{record.contract_id}")
        searched = await client.get(
            "/api/v1/experiences/search", params={"workspace_id": WORKSPACE_ID}
        )

    assert response.status_code == 404
    assert response.json()["message"].endswith(f"{record.contract_id} was not found")
    assert searched.status_code == 404


async def test_resolving_an_open_contradiction_appends_a_resolved_revision() -> None:
    """§7.10's adjudication is a new row, and the row it settles is still readable.

    Both halves matter. If the resolution edited the record, the history of the contradiction
    would be gone; if it did not mark the line ``RESOLVED``, §10.1 would keep excluding a
    record that has been adjudicated.
    """

    scenario = await setup_pipeline()
    record = await put_record(
        scenario,
        contradiction_status=ContradictionStatus.OPEN.value,
        eligible_for_learning=False,
    )
    application = build_app(scenario.pipeline)

    async with client_for(application) as client:
        response = await client.post(
            f"/api/v1/experiences/{record.contract_id}/resolve-contradiction",
            json={"resolution": "the second verifier ran against a stale worktree"},
        )

    assert response.status_code == 201
    chain = await scenario.store.list_experience_record_revisions(
        scenario.experience.experience_id, workspace_id=WORKSPACE_ID
    )
    assert [item.contract_id for item in chain] == [
        record.contract_id,
        response.json()["contract_id"],
    ]
    assert chain[0].contradiction_status is ContradictionStatus.OPEN
    assert chain[1].contradiction_status is ContradictionStatus.RESOLVED
    assert chain[1].supersedes_contract_id == record.contract_id
    assert chain[1].created_by.principal_id == INSIDER.principal_id


async def test_resolving_a_settled_contradiction_is_refused_as_a_conflict() -> None:
    """Re-resolving would write a second adjudication of a question already answered."""

    scenario = await setup_pipeline()
    record = await put_record(
        scenario,
        contradiction_status=ContradictionStatus.OPEN.value,
        eligible_for_learning=False,
    )
    application = build_app(scenario.pipeline)

    async with client_for(application) as client:
        first = await client.post(
            f"/api/v1/experiences/{record.contract_id}/resolve-contradiction",
            json={"resolution": "adjudicated in favour of the failing verifier"},
        )
        again = await client.post(
            f"/api/v1/experiences/{record.contract_id}/resolve-contradiction",
            json={"resolution": "adjudicated again"},
        )

    assert first.status_code == 201
    assert again.status_code == 409
    assert again.json()["code"] == CONTRADICTION_NOT_OPEN
    chain = await scenario.store.list_experience_record_revisions(
        scenario.experience.experience_id, workspace_id=WORKSPACE_ID
    )
    assert len(chain) == 2


async def test_final_verification_projects_under_the_callers_grade_and_authority() -> None:
    """ADR-048's ordering as an endpoint: the run's grade decides, the caller authors.

    Asserted against the stored projection rather than the response, and against both fields,
    because a route that passed its own operator identity or dropped the grade would still
    return a plausible-looking record.
    """

    scenario = await setup_pipeline()
    receipt = await route(scenario, "plan")
    await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=scenario.run.session_id,
        results=[v01_result("git-diff", VerificationStatus.PASS, scenario.run.run_id,
                            evidence=EVIDENCE)],
        policy=build_policy(),
        configuration_hash=receipt.selected_configuration_hash or "",
    )
    application = build_app(scenario.pipeline)

    async with client_for(application) as client:
        response = await client.post(
            f"/api/v1/runs/{scenario.run.run_id}/final-verification",
            json={
                "status": VerificationState.FAIL.value,
                "source": ExperienceSourceKind.RUN.value,
                "visibility": Visibility.PROJECT.value,
            },
        )

    assert response.status_code == 201
    stored = await scenario.store.list_experience_records(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID
    )
    assert [record.contract_id for record in stored] == [
        record["contract_id"] for record in response.json()
    ]
    assert [record.final_run_status for record in stored] == [VerificationState.FAIL]
    assert [record.created_by.principal_id for record in stored] == [
        INSIDER.principal_id
    ]
    # AC4-M3-025: the two verdicts stay separate. The node passed its own verification, so
    # the projection is still evidence a router may learn from, and the run's FAIL is recorded
    # beside it rather than folded into it — a reader that collapsed them could no longer ask
    # "what did the verifier decide about this node" at all.
    assert [record.local_verification_status for record in stored] == [
        VerificationState.PASS
    ]
    assert [record.eligible_for_learning for record in stored] == [True]


async def test_final_verification_forwards_the_stated_scope_and_source() -> None:
    """The three body fields reach the pipeline unaltered, including the defaults.

    A recording pipeline rather than the real one: what is under test is the translation from
    request to call, and asserting it through a projection would also be asserting the
    projector's rules, which have their own tests.
    """

    scenario = await setup_pipeline()
    pipeline = RecordingPipeline(scenario.store)
    application = build_app(pipeline)

    async with client_for(application) as client:
        response = await client.post(
            f"/api/v1/runs/{scenario.run.run_id}/final-verification",
            json={"status": VerificationState.PASS.value},
        )

    assert response.status_code == 201
    name, values = pipeline.calls[-1]
    assert name == "record_final"
    assert values["status"] is VerificationState.PASS
    assert values["source"] is ExperienceSourceKind.RUN
    assert values["visibility"] is Visibility.PROJECT
    assert isinstance(values["principal"], PrincipalRef)
    assert values["principal"].principal_id == INSIDER.principal_id
    assert values["run"].run_id == scenario.run.run_id


async def test_every_route_refuses_when_no_feedback_pipeline_is_configured() -> None:
    """§15.1-style refusal, on all five paths, before any of them reads a store.

    One test over the whole family rather than five, because the property is the accessor's
    and a route that forgot to use it would be the only one that answered.
    """

    scenario = await setup_pipeline()
    application = build_app(None)
    instance = scenario.nodes["plan"].execution_instance_id

    async with client_for(application) as client:
        responses = [
            await client.post(
                f"/api/v1/node-executions/{instance}/verification-results",
                json=verification_payload(scenario.run, new_id("verification")),
            ),
            await client.post(
                f"/api/v1/runs/{scenario.run.run_id}/final-verification",
                json={"status": VerificationState.PASS.value},
            ),
            await client.get(
                "/api/v1/experiences/search", params={"workspace_id": WORKSPACE_ID}
            ),
            await client.get("/api/v1/experiences/exp_missing"),
            await client.post(
                "/api/v1/experiences/exp_missing/resolve-contradiction",
                json={"resolution": "no pipeline, no adjudication"},
            ),
        ]

    assert [response.status_code for response in responses] == [409] * 5
    assert {response.json()["code"] for response in responses} == {FEEDBACK_UNAVAILABLE}


def build_policy() -> Any:
    """The acceptance policy ``record_local`` names as the verifier contract."""

    from accretion.contracts import AcceptancePolicy

    return AcceptancePolicy(
        policy_id="acceptance-m3-api",
        required_verifiers=["git-diff"],
        outcome_check="the diff verifier must pass",
    )
