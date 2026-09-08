"""Read-only writer identity must agree across memory and a disposable PostgreSQL store.

Peer payloads are constructed by a model that knows the additive field. Corruption
is injected only into task-owned test rows, never through a production write API.
No test dispatches a provider or reads the research corpora.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select, update
from test_v04_m0_store import FIXTURE_PROJECT_ID, FIXTURE_WORKSPACE_ID, build
from test_v04_m2_postgres_store import postgres_stores
from test_v04_upcast_references import FutureNodeContract, _reseal

from accretion.contracts import Project
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.routing import NodeContract, RoutingDecisionReceipt
from accretion.ids import new_id
from accretion.persistence.models import NodeContractRow, RoutingReceiptRow
from accretion.persistence.store import MemoryStore, PostgresStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("ACCRETION_TEST_POSTGRES_URL"),
        reason="ACCRETION_TEST_POSTGRES_URL is not set",
    ),
]


async def peer_pair(
    postgres: PostgresStore,
) -> tuple[MemoryStore, FutureNodeContract, RoutingDecisionReceipt]:
    memory = MemoryStore()
    for store in (memory, postgres):
        if await store.get_project(FIXTURE_PROJECT_ID) is None:
            await store.create_project(
                Project(
                    project_id=FIXTURE_PROJECT_ID,
                    name="Scoped peer reader fixtures",
                    repository_path=Path("/tmp/accretion-peer-reader-fixtures"),
                )
            )
    writer = _reseal(
        FutureNodeContract,
        build(NodeContract, run_graph_id=new_id("run_graph")),
        schema_version="1.1.0",
        reader_note="peer annotation",
    )
    receipt = build(
        RoutingDecisionReceipt,
        node_contract_hash=writer.immutable_hash,
        routing_request_id=new_id("routing_request"),
    )
    for store in (memory, postgres):
        await store.put_node_contract(writer)
        await store.put_routing_receipt(receipt)
    return memory, writer, receipt


async def test_both_backends_list_the_same_peer_receipt_without_rewriting_it() -> None:
    async with postgres_stores() as (postgres,):
        memory, writer, receipt = await peer_pair(postgres)
        before = canonical_json(writer.model_dump(mode="json"))
        found = []
        for store in (memory, postgres):
            records = await store.list_routing_receipts_for_run_graph(
                workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
            )
            found.append([record.model_dump(mode="json") for record in records])
        assert found == [[receipt.model_dump(mode="json")]] * 2
        async with postgres.sessions() as session:
            row = await session.get(NodeContractRow, writer.contract_id)
            assert row is not None
            assert canonical_json(row.payload) == before
            assert row.immutable_hash == writer.immutable_hash
        assert (
            canonical_json(memory.v04_contracts["node_contracts"][writer.contract_id].payload)
            == before
        )


@pytest.mark.parametrize(
    "corruption", ["unsealed-edit", "derived-hash", "header-column", "schema-column"]
)
async def test_a_postgres_join_never_trusts_unverified_node_digest_columns(corruption: str) -> None:
    async with postgres_stores() as (postgres,):
        _, writer, receipt = await peer_pair(postgres)
        payload = deepcopy(writer.model_dump(mode="json"))
        changes = {}
        if corruption == "unsealed-edit":
            payload["reader_note"] = "not covered by the original seal"
            changes["payload"] = payload
        elif corruption == "derived-hash":
            payload["immutable_hash"] = "f" * 64
            payload["content_hash"] = content_hash(payload)
            changes = {
                "payload": payload,
                "content_hash": payload["content_hash"],
                "immutable_hash": payload["immutable_hash"],
            }
            # Make the SQL join succeed against the false inner digest. The receipt
            # itself is honestly sealed, so rejecting it cannot hide the bad node.
            receipt = _reseal(
                RoutingDecisionReceipt, receipt, node_contract_hash=payload["immutable_hash"]
            )
            async with postgres.sessions.begin() as session:
                await session.execute(
                    update(RoutingReceiptRow)
                    .where(RoutingReceiptRow.id == receipt.contract_id)
                    .values(
                        payload=receipt.model_dump(mode="json"),
                        content_hash=receipt.content_hash,
                        node_contract_hash=receipt.node_contract_hash,
                    )
                )
        elif corruption == "header-column":
            changes["content_hash"] = content_hash({"corrupt_row": writer.contract_id})
        else:
            changes["schema_version"] = "1.2.0"
        async with postgres.sessions.begin() as session:
            await session.execute(
                update(NodeContractRow)
                .where(NodeContractRow.id == writer.contract_id)
                .values(**changes)
            )

        with pytest.raises(ValueError):
            await postgres.list_routing_receipts_for_run_graph(
                workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
            )


async def test_a_scoped_postgres_join_refuses_a_node_from_another_sealed_workspace() -> None:
    async with postgres_stores() as (postgres,):
        _, writer, receipt = await peer_pair(postgres)
        # Both indexed workspace columns agree with the query; the node's sealed
        # workspace does not. Filtering only columns would return the receipt.
        foreign = "wks_foreign_peer_scope"
        receipt = _reseal(RoutingDecisionReceipt, receipt, workspace_id=foreign)
        async with postgres.sessions.begin() as session:
            await session.execute(
                update(NodeContractRow)
                .where(NodeContractRow.id == writer.contract_id)
                .values(workspace_id=foreign)
            )
            await session.execute(
                update(RoutingReceiptRow)
                .where(RoutingReceiptRow.id == receipt.contract_id)
                .values(
                    workspace_id=foreign,
                    payload=receipt.model_dump(mode="json"),
                    content_hash=receipt.content_hash,
                )
            )

        with pytest.raises(ValueError):
            await postgres.list_routing_receipts_for_run_graph(
                workspace_id=foreign, run_graph_id=writer.run_graph_id
            )
        async with postgres.sessions() as session:
            row = await session.scalar(
                select(NodeContractRow).where(NodeContractRow.id == writer.contract_id)
            )
            assert row is not None
            assert row.payload["workspace_id"] == FIXTURE_WORKSPACE_ID
