"""M6's three record kinds against a real PostgreSQL database, and against ``MemoryStore``.

Three things can only be proved here. That a ``SHADOW`` ``RouterModelVersion`` — a status §13.1
has an opinion about — inserts beside its ``CANDIDATE`` parent without tripping the "one ACTIVE
router per workspace" rule. That a ``ShadowDecision``'s two nullable configuration hashes and a
``ShadowRolloutResult``'s nested ``serving`` and ``observed`` objects survive the JSON column
unchanged, including the ``float`` that is a whole number in Python and could come back as an
``int``. And that both backends return *equal* documents in the *same order* for the same
writes, which is the property M6.2's report depends on and which no single-backend test can
see: ``shadow_report``'s bootstrap is seeded, so two orderings of one set of rows would produce
two different lower bounds while every document comparison still passed.

Every id is derived from a fresh uuid, so the file is re-runnable against a database it has
already written to, and no test asserts on a global row count. Nothing here carries an
acceptance marker: a criterion whose only claiming test skips without PostgreSQL would classify
``SKIPPED_ONLY``, and M6.1's claim for AC4-M4-016 is made against ``MemoryStore`` in
``tests/test_v04_m6_shadow.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_v04_m0_store import build, digest, new_store
from test_v04_m6_shadow import BUDGET, PRINCIPAL, WEIGHTS, candidate_version

from accretion.contracts import Project
from accretion.contracts.routing import (
    RouterModelVersion,
    RouterStatus,
    ShadowDecision,
    ShadowRolloutResult,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore, StateStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.shadow import (
    ShadowEvaluator,
    ShadowReportConfig,
    paired_deltas,
    shadow_report,
)

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


def unique_workspace() -> str:
    return f"wks_{uuid4().hex[:22].upper()}"


def postgres_store() -> PostgresStore:
    assert POSTGRES_URL is not None
    return PostgresStore(create_session_factory(create_engine(POSTGRES_URL)))


async def seed_project(*stores: StateStore) -> str:
    """One fresh project in every store, because every v0.4 row has a foreign key to one."""

    project_id = new_id("project")
    for store in stores:
        await store.create_project(
            Project(
                project_id=project_id,
                name="v0.4 M6 postgres parity",
                repository_path=Path("/tmp/accretion-v04-m6"),
            )
        )
    return project_id


def decision_row(
    version_id: str, workspace_id: str, project_id: str, **overrides: Any
) -> ShadowDecision:
    fields: dict[str, Any] = {
        "contract_id": new_id("shadow_decision"),
        "workspace_id": workspace_id,
        "project_id": project_id,
        "shadow_router_version_id": version_id,
        "executed_receipt_id": new_id("routing_receipt"),
        "shadow_receipt_id": new_id("routing_receipt"),
    }
    fields.update(overrides)
    return build(ShadowDecision, **fields)


def rollout_row(
    decision_id: str,
    workspace_id: str,
    project_id: str,
    kind: str,
    trial_index: int,
    *,
    quality: float,
) -> ShadowRolloutResult:
    return build(
        ShadowRolloutResult,
        contract_id=new_id("shadow_rollout_result"),
        workspace_id=workspace_id,
        project_id=project_id,
        shadow_decision_id=decision_id,
        kind=kind,
        trial_index=trial_index,
        fork_execution_id=new_id("runtime_call"),
        observed={
            "quality": quality,
            "cost": 0.25,
            "latency_ms": 18_400.0,
            "verified": False,
        },
    )


async def test_a_shadow_version_is_registered_beside_its_candidate_in_postgres(
    tmp_path: Path,
) -> None:
    """Registration works against the database, and the SHADOW row does not disturb the parent.

    §13.1's partial unique index is over ``ACTIVE`` versions. A SHADOW row is deliberately not
    covered by it, and asserting that here rather than reasoning about it is what would catch a
    migration that widened the predicate.
    """

    store = postgres_store()
    workspace_id = unique_workspace()
    candidate = await store.put_router_model_version(
        candidate_version(
            contract_id=new_id("router_model_version"), workspace_id=workspace_id
        )
    )
    evaluator = ShadowEvaluator(store, ArtifactStore(tmp_path / "artifacts"))

    registered = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=workspace_id,
    )
    stored = await store.get_router_model_version(registered.contract_id)
    assert stored is not None
    assert stored == registered
    assert stored.status is RouterStatus.SHADOW
    assert stored.parent_version_id == candidate.contract_id

    again = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=workspace_id,
    )
    assert again.contract_id == registered.contract_id
    versions = await store.list_router_model_versions(workspace_id=workspace_id)
    assert sorted(version.contract_id for version in versions) == sorted(
        [candidate.contract_id, registered.contract_id]
    )


async def test_shadow_decisions_and_rollouts_round_trip_through_postgres_unchanged() -> None:
    """Nullable hashes, a nested ``observed``, and a whole-number float that is not an int.

    ``latency_ms`` is declared ``float`` and written as ``18400.0``. A JSON round trip that
    handed it back as an ``int`` would still validate and would then change every utility the
    report computes by a rounding step nobody could see.
    """

    store = postgres_store()
    workspace_id = unique_workspace()
    project_id = await seed_project(store)
    version_id = new_id("router_model_version")
    chosen = digest("configuration-a")

    deferred = decision_row(version_id, workspace_id, project_id)
    agreed = decision_row(
        version_id,
        workspace_id,
        project_id,
        executed_configuration_hash=chosen,
        shadow_configuration_hash=chosen,
        agreement=True,
    )
    for decision in (deferred, agreed):
        await store.put_shadow_decision(decision)

    rows = [
        rollout_row(agreed.contract_id, workspace_id, project_id, kind, 0, quality=quality)
        for kind, quality in (("CONTROL", 0.5), ("SHADOW", 0.8))
    ]
    for row in rows:
        await store.put_shadow_rollout_result(row)

    read_decisions = await store.list_shadow_decisions(workspace_id=workspace_id)
    assert read_decisions == sorted(
        [deferred, agreed], key=lambda item: (item.created_at, item.contract_id)
    )
    by_id = {item.contract_id: item for item in read_decisions}
    assert by_id[deferred.contract_id].executed_configuration_hash is None
    assert by_id[deferred.contract_id].agreement is False
    assert by_id[agreed.contract_id].shadow_configuration_hash == chosen
    assert by_id[agreed.contract_id].agreement is True

    read_rows = await store.list_shadow_rollout_results(workspace_id=workspace_id)
    assert read_rows == sorted(rows, key=lambda item: (item.created_at, item.contract_id))
    assert all(isinstance(row.observed.latency_ms, float) for row in read_rows)
    assert all(not isinstance(row.observed.latency_ms, bool) for row in read_rows)
    assert paired_deltas(read_rows, weights=WEIGHTS) == paired_deltas(rows, weights=WEIGHTS)


async def test_both_stores_return_the_same_shadow_records_in_the_same_order() -> None:
    """The parity claim: same writes, same documents, same order, and the same report."""

    postgres = postgres_store()
    memory: MemoryStore = MemoryStore()
    workspace_id = unique_workspace()
    project_id = await seed_project(postgres, memory)
    version_id = new_id("router_model_version")

    decisions: list[ShadowDecision] = []
    results: list[ShadowRolloutResult] = []
    for index in range(4):
        decision = decision_row(version_id, workspace_id, project_id)
        decisions.append(decision)
        results.append(
            rollout_row(
                decision.contract_id, workspace_id, project_id, "CONTROL", index, quality=0.5
            )
        )
        results.append(
            rollout_row(
                decision.contract_id,
                workspace_id,
                project_id,
                "SHADOW",
                index,
                quality=0.5 + index / 50,
            )
        )

    # Written newest-id-first so that a backend returning insertion order would be visible
    # rather than lucky.
    for decision in sorted(decisions, key=lambda item: item.contract_id, reverse=True):
        await postgres.put_shadow_decision(decision)
        await memory.put_shadow_decision(decision)
    for result in sorted(results, key=lambda item: item.contract_id, reverse=True):
        await postgres.put_shadow_rollout_result(result)
        await memory.put_shadow_rollout_result(result)

    from_postgres_decisions = await postgres.list_shadow_decisions(workspace_id=workspace_id)
    from_memory_decisions = await memory.list_shadow_decisions(workspace_id=workspace_id)
    from_postgres_rows = await postgres.list_shadow_rollout_results(workspace_id=workspace_id)
    from_memory_rows = await memory.list_shadow_rollout_results(workspace_id=workspace_id)

    assert from_postgres_decisions == from_memory_decisions
    assert from_postgres_rows == from_memory_rows
    assert [item.contract_id for item in from_postgres_rows] == [
        item.contract_id for item in from_memory_rows
    ]

    config = ShadowReportConfig(seed=20260906, bootstraps=200)
    postgres_report = shadow_report(
        from_postgres_rows, from_postgres_decisions, weights=WEIGHTS, config=config
    )
    memory_report = shadow_report(
        from_memory_rows, from_memory_decisions, weights=WEIGHTS, config=config
    )
    assert postgres_report == memory_report
    assert postgres_report.paired_count == 4


async def test_both_stores_refuse_a_drifted_shadow_version_with_the_same_words() -> None:
    """The append-only rule for the row M6 introduces, worded identically on both backends.

    A shadow policy whose budget could be edited after registration would make the version row
    a description of the present rather than a record of what the workspace agreed to spend.
    """

    postgres = postgres_store()
    memory: MemoryStore = await new_store()
    workspace_id = unique_workspace()
    version = candidate_version(
        contract_id=new_id("router_model_version"),
        workspace_id=workspace_id,
        status=RouterStatus.SHADOW.value,
        labels={"shadow_budget_daily_cost_cap": "1.0"},
    )
    payload = version.model_dump(mode="json")
    payload["labels"] = {"shadow_budget_daily_cost_cap": "99.0"}
    payload.pop("content_hash", None)
    drifted = RouterModelVersion.model_validate(payload)
    assert drifted.content_hash != version.content_hash

    messages: list[str] = []
    for store in (postgres, memory):
        await store.put_router_model_version(version)
        with pytest.raises(ValueError) as refusal:
            await store.put_router_model_version(drifted)
        messages.append(str(refusal.value))
    assert messages[0] == messages[1]
    assert version.contract_id in messages[0]
