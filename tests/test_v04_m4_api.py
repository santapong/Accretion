"""The two v0.4 router-model routes over HTTP (SDD §11.3).

``POST /api/v1/router-models/train-candidate`` and ``GET /api/v1/router-models`` are the
first v0.4 surface to reach the wire, so the things checked here are the rules §11 states
once for every mutating endpoint and then never repeats: authentication, workspace
authorization, idempotency, and an error envelope with a code a client can branch on.

**Why idempotency gets two tests and not a mention.** Training is expensive and its output is
durable. A retry after a timeout that trained a second time would leave two candidates where
the operator believes there is one, and the second would be indistinguishable from a
deliberate second run — so the key is required, and a repeat of it returns the first result
rather than doing the work again.

**What "unauthorized" means at this boundary.** A principal who is not a member of the
workspace is refused with 403 by ``_require_workspace_access`` rather than shown an empty
list, because the router versions of a workspace are the workspace's policy history and the
absence of a 404 here is deliberate: the caller named a workspace, not a resource.

The corpus builders are imported from ``test_v04_m4_train`` rather than copied — there is no
``conftest.py``, and one definition of "a workspace with trainable evidence" is what keeps
the service tests and the route tests talking about the same thing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from test_v04_m4_train import (
    TEST_CONFIG,
    WINDOW,
    Corpus,
    frozen_clock,
    setup_corpus,
)

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.contracts import (
    Principal,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.train import ACCEPTANCE_LABEL, RouterTrainingService

TRAIN_PATH = "/api/v1/router-models/train-candidate"
LIST_PATH = "/api/v1/router-models"


def train_body(corpus: Corpus, *, seed: int = 11) -> dict[str, Any]:
    window_start, window_end = WINDOW
    return {
        "workspace_id": corpus.workspace_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "seed": seed,
    }


async def setup_router_api(
    tmp_path: Path, *, projects: int = 10, per_project: int = 4
) -> tuple[Corpus, Principal, Principal]:
    """A trainable workspace, one owner of it, and one principal who is not a member.

    Both principals are real ``principals`` rows with real memberships (or, for the
    outsider, deliberately none), so the 403 below comes from the same membership lookup
    production uses rather than from a stubbed decision.
    """

    corpus = await setup_corpus(projects=projects, per_project=per_project)
    suffix = uuid4().hex[:8]
    owner = Principal(
        principal_id=f"usr_owner_{suffix}", issuer="test", subject=f"owner-{suffix}"
    )
    outsider = Principal(
        principal_id=f"usr_outsider_{suffix}", issuer="test", subject=f"outsider-{suffix}"
    )
    for principal in (owner, outsider):
        await corpus.store.upsert_principal(principal)
    await corpus.store.upsert_workspace(
        WorkspaceEntity(workspace_id=corpus.workspace_id, name="v0.4 M4")
    )
    await corpus.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=corpus.workspace_id,
            principal_id=owner.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    return corpus, owner, outsider


def install_app_state(corpus: Corpus, artifacts: Path, who: Principal) -> None:
    """Wire the app the way the lifespan does, with a small learner and a frozen clock."""

    app.state.manager = type("Manager", (), {"store": corpus.store})()
    app.state.router_training = RouterTrainingService(
        corpus.store,
        ArtifactStore(artifacts),
        clock=frozen_clock,
        config=TEST_CONFIG,
    )
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(corpus.store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )


def clear_app_state() -> None:
    for attribute in ("auth", "router_training", "manager"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


async def call(method: str, url: str, **kwargs: Any) -> Any:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        return await client.request(method, url, **kwargs)


async def test_training_a_candidate_returns_the_version_its_snapshot_and_its_evaluation(
    tmp_path: Path,
) -> None:
    """The response is the candidate, the evidence it cites, and what it scored.

    Read back from ``GET`` afterwards rather than trusted from the ``POST`` body, because a
    route that returned a well-formed document without writing it would pass the first
    assertion and fail the operator.
    """

    corpus, owner, _ = await setup_router_api(tmp_path)
    install_app_state(corpus, tmp_path / "artifacts", owner)
    try:
        created = await call(
            "POST",
            TRAIN_PATH,
            json=train_body(corpus),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["version"]["status"] == "CANDIDATE"
        assert body["version"]["scope"] == "TEAM_WORKSPACE"
        assert body["version"]["training_snapshot_id"] == body["training_snapshot_id"]
        assert body["version"]["labels"][ACCEPTANCE_LABEL] == body["holdout"]["digest"]
        assert body["holdout"]["n_rows"] >= 4
        assert body["holdout"]["project_ids"]
        assert 0.0 <= body["calibration"]["ece_10bin"] <= 1.0
        assert body["calibration"]["bin_count"] >= 1

        listed = await call(
            "GET", LIST_PATH, params={"workspace_id": corpus.workspace_id}
        )
        assert listed.status_code == 200
        versions = listed.json()
        assert [version["contract_id"] for version in versions] == [
            body["version"]["contract_id"]
        ]
        # Lineage travels with the row: a reader can walk an ancestry without a second call.
        assert versions[0]["feature_schema_version"] == body["version"]["feature_schema_version"]
        assert versions[0]["artifact_digest"] != versions[0]["calibration_artifact_digest"]
        assert versions[0]["parent_version_id"] is None

        stored = await corpus.store.get_router_training_snapshot(body["training_snapshot_id"])
        assert stored is not None
        assert stored.workspace_id == corpus.workspace_id
    finally:
        clear_app_state()


async def test_a_repeated_idempotency_key_returns_the_first_candidate_without_training_again(
    tmp_path: Path,
) -> None:
    """A retry is a read, not a second expensive write.

    The *write count* is the witness, not the row count. A version's id is derived from the
    idempotency key and the evidence, so a route that trained a second time would derive the
    same id and the same content, and the append-only store would absorb the second put as a
    silent no-op — leaving exactly one row either way. Counting rows would therefore pass
    whether or not the replay branch exists, so the recording store is asked how many times
    it was written to instead.
    """

    corpus, owner, _ = await setup_router_api(tmp_path)
    install_app_state(corpus, tmp_path / "artifacts", owner)
    try:
        assert corpus.store.calls.count("put_router_model_version") == 0
        key = f"idem-{uuid4().hex[:8]}"
        first = await call(
            "POST", TRAIN_PATH, json=train_body(corpus), headers={"Idempotency-Key": key}
        )
        second = await call(
            "POST", TRAIN_PATH, json=train_body(corpus), headers={"Idempotency-Key": key}
        )
        assert first.status_code == 201 and second.status_code == 201
        assert second.json()["version"]["contract_id"] == first.json()["version"]["contract_id"]
        assert second.json()["holdout"] == first.json()["holdout"]
        assert second.json()["calibration"] == first.json()["calibration"]

        writes = corpus.store.calls.count("put_router_model_version")
        assert writes == 1, f"the retry trained again: {writes} version writes"
        assert corpus.store.calls.count("put_router_training_snapshot") == 1

        versions = await corpus.store.list_router_model_versions(
            workspace_id=corpus.workspace_id
        )
        assert len(versions) == 1
    finally:
        clear_app_state()


async def test_a_train_request_without_an_idempotency_key_is_refused(tmp_path: Path) -> None:
    """§11 requires idempotency of every mutating endpoint, and the refusal says so."""

    corpus, owner, _ = await setup_router_api(tmp_path)
    install_app_state(corpus, tmp_path / "artifacts", owner)
    try:
        response = await call("POST", TRAIN_PATH, json=train_body(corpus))
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        assert body["correlation_id"]
        assert body["retryable"] is False
        assert (
            await corpus.store.list_router_model_versions(workspace_id=corpus.workspace_id)
            == []
        )
    finally:
        clear_app_state()


async def test_a_principal_outside_the_workspace_can_neither_train_nor_list(
    tmp_path: Path,
) -> None:
    """Membership is checked before anything is read, and the code is the same for both."""

    corpus, _, outsider = await setup_router_api(tmp_path)
    install_app_state(corpus, tmp_path / "artifacts", outsider)
    try:
        refused = await call(
            "POST",
            TRAIN_PATH,
            json=train_body(corpus),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "FORBIDDEN"

        listed = await call("GET", LIST_PATH, params={"workspace_id": corpus.workspace_id})
        assert listed.status_code == 403
        assert listed.json()["code"] == "FORBIDDEN"

        # Refused before any work: the outsider's request left nothing behind.
        assert (
            await corpus.store.list_router_model_versions(workspace_id=corpus.workspace_id)
            == []
        )
    finally:
        clear_app_state()


async def test_too_little_evidence_is_a_named_conflict_and_not_a_server_error(
    tmp_path: Path,
) -> None:
    """A workspace that cannot fill the five split groups gets an answer it can act on."""

    corpus, owner, _ = await setup_router_api(tmp_path, projects=4, per_project=2)
    install_app_state(corpus, tmp_path / "artifacts", owner)
    try:
        response = await call(
            "POST",
            TRAIN_PATH,
            json=train_body(corpus),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "ROUTER_TRAINING_DATA_INSUFFICIENT"
        assert "eligible experience records" in body["message"]
    finally:
        clear_app_state()


async def test_the_split_fractions_in_the_request_body_are_validated_before_any_training(
    tmp_path: Path,
) -> None:
    """The sealed ``SplitFractions`` refuses a body that would retire one of the five splits.

    Validation happens in the request model, so the caller learns which rule they broke
    instead of watching a training run fail somewhere inside the split enforcer.
    """

    corpus, owner, _ = await setup_router_api(tmp_path)
    install_app_state(corpus, tmp_path / "artifacts", owner)
    try:
        response = await call(
            "POST",
            TRAIN_PATH,
            json={
                **train_body(corpus),
                "split_fractions": {
                    "train": 0.5,
                    "calibration": 0.2,
                    "development": 0.2,
                    "test": 0.2,
                    "drift": 0.1,
                },
            },
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 422
        assert (
            await corpus.store.list_router_model_versions(workspace_id=corpus.workspace_id)
            == []
        )
    finally:
        clear_app_state()


def test_the_openapi_document_declares_both_router_model_routes() -> None:
    """§11.3's two paths, their methods and their response models, as published.

    The generated TypeScript client is built from this document, so a route that existed
    only in Python would be invisible to the Studio and to every other consumer.
    """

    document = app.openapi()
    assert TRAIN_PATH in document["paths"]
    assert LIST_PATH in document["paths"]
    assert set(document["paths"][TRAIN_PATH]) == {"post"}
    assert set(document["paths"][LIST_PATH]) == {"get"}

    post = document["paths"][TRAIN_PATH]["post"]
    assert "201" in post["responses"]
    assert (
        post["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/RouterCandidateTrained"
    )
    assert any(
        parameter["name"] == "Idempotency-Key" for parameter in post.get("parameters", [])
    )

    get = document["paths"][LIST_PATH]["get"]
    assert [parameter["name"] for parameter in get["parameters"]] == [
        "workspace_id",
        "project_id",
    ]
    assert (
        get["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"]
        == "#/components/schemas/RouterModelVersion"
    )
    assert "RouterModelVersion" in document["components"]["schemas"]


@pytest.mark.parametrize("path", [TRAIN_PATH, LIST_PATH])
def test_neither_router_route_is_exempt_from_authentication(path: str) -> None:
    """Both paths sit behind the session middleware's authentication gate."""

    from accretion.api.auth import is_exempt

    assert not is_exempt(path)
