"""PostgreSQL parity for the two M5 reads that decide which router a receipt is pinned to.

M5 adds no store method, so there is nothing new to round-trip. What it does add is a
*read* whose answer must not depend on the backend:
:class:`~accretion.routing.stages.StatusActiveVersionResolver` picks the latest ACTIVE
version per scope, and the labels it returns go into
:func:`~accretion.routing.identity.routing_request_id`. A resolver that took whichever row a
backend happened to return first would derive one request id in memory and a different one in
PostgreSQL for the same workspace — and §8.2's replay guarantee would hold on neither.

The second half is the receipt itself. M5 begins writing three fields M2 always left at their
defaults — ``experience_refs``, a real ``selection_propensity`` and a ``degraded`` label — and
a JSON column that round-tripped an empty list correctly is not evidence that it round-trips a
populated one.

Both tests carry parity assertions only and no acceptance marker: AC4-M5-021's marker sits on
the MemoryStore property test in ``tests/test_v04_m5_coldstart.py``. Every domain id is
freshly minted, so this module is safe to rerun against the same database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_v04_m0_postgres_store import build

from accretion.contracts import (
    PrincipalRef,
    PrincipalStatus,
    Project,
)
from accretion.contracts.routing import (
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RoutingDecisionReceipt,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.stages import StatusActiveVersionResolver

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
    pytest.mark.asyncio,
]


@asynccontextmanager
async def postgres_store() -> AsyncIterator[PostgresStore]:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    try:
        yield PostgresStore(create_session_factory(engine))
    finally:
        await engine.dispose()


def _version(
    *,
    workspace_id: str,
    project_id: str | None,
    scope: RouterScope,
    status: RouterStatus,
    created_at: datetime,
    snapshot_id: str,
) -> RouterModelVersion:
    return RouterModelVersion.model_validate(
        {
            "contract_id": new_id("router_model_version"),
            "created_at": created_at,
            "created_by": PrincipalRef(
                principal_id=new_id("principal"),
                display_name="M5 parity",
                status=PrincipalStatus.ACTIVE,
            ).model_dump(mode="python"),
            "workspace_id": workspace_id,
            "project_id": project_id,
            "scope": scope.value,
            "algorithm_id": "gbdt-bagged-v1",
            "feature_schema_version": "1.0.0",
            "training_snapshot_id": snapshot_id,
            "artifact_digest": "a" * 64,
            "calibration_artifact_digest": "b" * 64,
            "status": status.value,
        }
    )


def _version_set(
    workspace_id: str, project_id: str
) -> tuple[tuple[RouterModelVersion, ...], str, str]:
    """Four versions written to *both* stores, and the two the resolver must pick.

    §13.1 permits exactly one ACTIVE workspace router, so a "latest wins" ordering can only
    be exercised against rows the resolver must *skip*. Two retired versions straddle the
    active one in time, which is the arrangement that catches a resolver ordering by
    ``created_at`` before filtering on status.

    The records are built once and written to both backends, so the parity assertion below
    is a direct equality on ids rather than a comparison of two independently minted sets.
    """

    snapshot_id = new_id("router_training_snapshot")
    active = _version(
        workspace_id=workspace_id,
        project_id=None,
        scope=RouterScope.TEAM_WORKSPACE,
        status=RouterStatus.ACTIVE,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
        snapshot_id=snapshot_id,
    )
    adapter = _version(
        workspace_id=workspace_id,
        project_id=project_id,
        scope=RouterScope.PROJECT_ADAPTER,
        status=RouterStatus.ACTIVE,
        created_at=datetime(2026, 2, 15, tzinfo=UTC),
        snapshot_id=snapshot_id,
    )
    older = _version(
        workspace_id=workspace_id,
        project_id=None,
        scope=RouterScope.TEAM_WORKSPACE,
        status=RouterStatus.RETIRED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_id=snapshot_id,
    )
    newer = _version(
        workspace_id=workspace_id,
        project_id=None,
        scope=RouterScope.TEAM_WORKSPACE,
        status=RouterStatus.RETIRED,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        snapshot_id=snapshot_id,
    )
    return (older, active, newer, adapter), active.contract_id, adapter.contract_id


async def test_the_active_version_resolver_answers_identically_on_both_backends(
    tmp_path: Path,
) -> None:
    """The same four rows resolve to the same two ids, in memory and in PostgreSQL.

    Parity only. The labels this returns are inside ``routing_request_id``, so a divergence
    here would not look like a wrong version — it would look like every receipt written by
    one backend being unreplayable by the other.
    """

    workspace_id = new_id("workspace_entity")
    project_id = new_id("project")
    versions, expected_router, expected_adapter = _version_set(workspace_id, project_id)
    memory = MemoryStore()
    async with postgres_store() as postgres:
        for store in (memory, postgres):
            await store.create_project(
                Project(
                    project_id=project_id,
                    name="M5 resolver parity",
                    repository_path=tmp_path,
                )
            )
            for record in versions:
                await store.put_router_model_version(record)

        memory_versions = await StatusActiveVersionResolver(memory).resolve(
            workspace_id=workspace_id, project_id=project_id
        )
        postgres_versions = await StatusActiveVersionResolver(postgres).resolve(
            workspace_id=workspace_id, project_id=project_id
        )

    assert memory_versions == postgres_versions
    assert memory_versions.router_version_id == expected_router
    assert memory_versions.adapter_version_id == expected_adapter
    assert memory_versions.router_label == expected_router
    assert memory_versions.adapter_label == expected_adapter
    assert memory_versions.router_label != WORKSPACE_ROUTER_VERSION


async def test_a_receipt_with_experience_refs_and_a_degraded_label_round_trips_identically(
    tmp_path: Path,
) -> None:
    """The three fields M5 starts populating survive the JSON column on both backends.

    Read back rather than compared to what was written: the assertion is that the store
    returns an equal document, and equality on a
    :class:`~accretion.contracts.routing.RoutingDecisionReceipt` includes its ``content_hash``,
    so a backend that reordered the reference list would fail here rather than at the next
    replay.
    """

    workspace_id = new_id("workspace_entity")
    project_id = new_id("project")
    refs = sorted(new_id("experience") for _ in range(3))
    memory = MemoryStore()
    async with postgres_store() as postgres:
        for store in (memory, postgres):
            await store.create_project(
                Project(
                    project_id=project_id,
                    name="M5 receipt parity",
                    repository_path=tmp_path,
                )
            )
        receipt = build(
            RoutingDecisionReceipt,
            workspace_id=workspace_id,
            project_id=project_id,
            routing_request_id=new_id("routing_request"),
            experience_refs=refs,
            selection_propensity=0.375,
            workspace_router_version=WORKSPACE_ROUTER_VERSION,
            project_adapter_version=None,
            labels={"run_id": new_id("run"), "degraded": "ADAPTER_UNAVAILABLE"},
        )
        stored_memory = await memory.put_routing_receipt(receipt)
        stored_postgres = await postgres.put_routing_receipt(receipt)
        read_memory = await memory.get_routing_receipt(receipt.contract_id)
        read_postgres = await postgres.get_routing_receipt(receipt.contract_id)

    assert stored_memory == stored_postgres == receipt
    assert read_memory == read_postgres == receipt
    assert read_postgres is not None
    assert read_postgres.experience_refs == refs
    assert read_postgres.selection_propensity == 0.375
    assert read_postgres.labels["degraded"] == "ADAPTER_UNAVAILABLE"
