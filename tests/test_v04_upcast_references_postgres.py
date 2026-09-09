"""Read-only writer identity must agree across memory and a disposable PostgreSQL store.

Peer payloads are constructed by a model that knows the additive field. Corruption
is injected only into task-owned test rows, never through a production write API.
No test dispatches a provider or reads the research corpora.
"""

from __future__ import annotations

import os
from copy import copy, deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select, update
from test_v04_budget_admission import _seed_account
from test_v04_m0_store import FIXTURE_PROJECT_ID, build
from test_v04_m2_postgres_store import postgres_stores
from test_v04_m7_postgres_store import seed_experience
from test_v04_upcast_references import (
    FutureConfigurationCandidate,
    FutureNodeContract,
    FutureRoutingContext,
    FutureRoutingDecisionReceipt,
    FutureVerificationSpec,
    _reseal,
    prepare_peer_execution,
)

from accretion.contracts import Project
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ExperienceRecord,
    NodeContract,
    RoutingContext,
    RoutingDecisionReceipt,
    VerificationSpec,
)
from accretion.ids import new_id
from accretion.persistence.models import ExperienceRecordRow, NodeContractRow, RoutingReceiptRow
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.routing.bandit import LedgerRegistry
from accretion.routing.errors import RoutingError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("ACCRETION_TEST_POSTGRES_URL"),
        reason="ACCRETION_TEST_POSTGRES_URL is not set",
    ),
]


class FutureExperienceRecord(ExperienceRecord):
    reader_note: str | None = None


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
        build(
            NodeContract, run_graph_id=new_id("run_graph"), workspace_id=new_id("workspace_entity")
        ),
        schema_version="1.1.0",
        reader_note="peer annotation",
    )
    receipt = build(
        RoutingDecisionReceipt,
        workspace_id=writer.workspace_id,
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
            assert row.payload["workspace_id"] == writer.workspace_id


@pytest.mark.parametrize(
    "peer_kind", ["current", "label-only", "node", "spec", "context", "receipt", "candidate"]
)
@pytest.mark.parametrize("operation", ["dispatch", "override"])
async def test_postgres_preserves_read_lineage_but_refuses_unknown_dispatch_inputs(
    tmp_path: Path, peer_kind: str, operation: str
) -> None:
    execution, receipt = await prepare_peer_execution(tmp_path, peer_kind)
    memory = execution.store
    async with postgres_stores() as (postgres,):
        project = await memory.get_project(execution.run.project_id)
        assert project is not None
        await postgres.create_project(project)
        await postgres.create_task(execution.task)
        for principal in memory.principals.values():
            # PostgreSQL also keys identity by issuer/subject. Give each fixture
            # principal a unique subject so reruns do not resolve to an older id.
            await postgres.upsert_principal(
                principal.model_copy(
                    update={
                        "subject": f"{principal.subject}:{principal.principal_id}",
                    }
                )
            )
        # Attribution now survives PostgreSQL round trips and has a foreign key;
        # import the actual fixture identities before the run that references one.
        await postgres.create_run(execution.run)
        for workspace in memory.workspaces.values():
            await postgres.upsert_workspace(workspace)
        for membership in memory.workspace_memberships.values():
            await postgres.upsert_workspace_membership(membership)
        template = await postgres.upsert_workflow_template(execution.template)
        graph = await memory.get_run_graph(execution.run.run_id)
        assert graph is not None
        # Template id/version is deduplicated across test processes. Use the row
        # returned by the store, which has the same verified template checksum.
        await postgres.create_run_graph(
            graph.model_copy(
                update={
                    "template_record_id": template.template_record_id,
                }
            )
        )
        # Public inserts emulate the peer writer, including its actual seals and
        # all indexed values. Only the reader is the current implementation.
        for table, current, future, put in (
            ("node_contracts", NodeContract, FutureNodeContract, postgres.put_node_contract),
            (
                "verification_specs",
                VerificationSpec,
                FutureVerificationSpec,
                postgres.put_verification_spec,
            ),
            (
                "routing_requests",
                RoutingContext,
                FutureRoutingContext,
                postgres.put_routing_request,
            ),
            (
                "routing_receipts",
                RoutingDecisionReceipt,
                FutureRoutingDecisionReceipt,
                postgres.put_routing_receipt,
            ),
            (
                "configuration_candidates",
                ConfigurationCandidate,
                FutureConfigurationCandidate,
                postgres.put_configuration_candidate,
            ),
        ):
            for row in memory.v04_contracts[table].values():
                model = future if "reader_note" in row.payload else current
                await put(model.model_validate(row.payload))
        service = copy(execution.service)
        service.store = postgres
        projected = await postgres.get_routing_receipt(receipt.contract_id)
        assert projected is not None
        assert (await service._run_for(projected)).run_id == execution.run.run_id
        allowed = peer_kind in {"current", "label-only"}
        if operation == "override":
            candidates = await service._candidates(postgres, projected)
            selected = next(item for item in candidates if item.hard_eligible)
            if peer_kind in {"node", "context", "receipt", "candidate"}:
                with pytest.raises(RoutingError) as error:
                    await service.override(
                        receipt_id=projected.contract_id,
                        candidate_id=selected.contract_id,
                        reason_code="REVIEWED",
                        reason="Cannot remove unknown fields through override",
                        expected_receipt_version=1,
                        principal=execution.frozen.node_contract.created_by,
                    )
                assert error.value.code == "ROUTING_INPUT_INVALID"
            else:
                projected = await service.override(
                    receipt_id=projected.contract_id,
                    candidate_id=selected.contract_id,
                    reason_code="REVIEWED",
                    reason="Choose an eligible fixture candidate",
                    expected_receipt_version=1,
                    principal=execution.frozen.node_contract.created_by,
                )
        if allowed:
            await service.claim_dispatch(receipt=projected, run=execution.run)
        else:
            with pytest.raises(RoutingError) as error:
                await service.claim_dispatch(receipt=projected, run=execution.run)
            assert error.value.code == "ROUTING_INPUT_INVALID"
        assert len(
            [
                event
                for event in await postgres.list_events(execution.run.run_id)
                if event.native_type == "accretion/routing/dispatch"
            ]
        ) == int(allowed)


async def account_rows(postgres: PostgresStore, tmp_path: Path, peer_kind: str = "current"):
    project, run, node, receipt = await _seed_account(postgres, tmp_path)
    if peer_kind in {"node", "node-projection-pin"}:
        node = _reseal(
            FutureNodeContract,
            node,
            schema_version="1.1.0",
            reader_note="unknown resource-cap semantics",
        )
        async with postgres.sessions.begin() as session:
            await session.execute(
                update(NodeContractRow)
                .where(NodeContractRow.id == node.contract_id)
                .values(
                    payload=node.model_dump(mode="json"),
                    content_hash=node.content_hash,
                    schema_version=node.schema_version,
                    immutable_hash=node.immutable_hash,
                )
            )
        receipt = _reseal(RoutingDecisionReceipt, receipt, node_contract_hash=node.immutable_hash)
        if peer_kind == "node-projection-pin":
            projected = await postgres.get_node_contract(node.contract_id)
            assert projected is not None
            receipt = _reseal(
                RoutingDecisionReceipt, receipt, node_contract_hash=projected.immutable_hash
            )
    elif peer_kind == "receipt":
        receipt = _reseal(
            FutureRoutingDecisionReceipt,
            receipt,
            schema_version="1.1.0",
            reader_note="unknown charged-cost semantics",
        )
    await postgres.put_routing_receipt(receipt)
    record = build(
        ExperienceRecord,
        workspace_id=node.workspace_id,
        project_id=project.project_id,
        source_node_execution_id=node.execution_instance_id,
        configuration_hash=receipt.selected_configuration_hash,
        outcomes={"quality": 0.5, "cost": "0.4", "latency_ms": 100},
    )
    if peer_kind == "observation":
        record = _reseal(
            FutureExperienceRecord,
            record,
            schema_version="1.1.0",
            reader_note="unknown measured-cost semantics",
        )
    await seed_experience(
        postgres,
        experience_id=record.contract_id,
        project_id=project.project_id,
        task_id=run.task_id,
        run_id=run.run_id,
    )
    await postgres.put_experience_record(record)
    return node, receipt, record


@pytest.mark.parametrize("target", ["node", "receipt", "observation"])
@pytest.mark.parametrize("column", ["workspace_id", "content_hash"])
async def test_accounting_refuses_a_mismatched_promoted_scope_or_identity(
    tmp_path: Path, target: str, column: str
) -> None:
    async with postgres_stores() as (postgres,):
        node, receipt, record = await account_rows(postgres, tmp_path)
        assert (
            await LedgerRegistry(postgres).ledger(
                workspace_id=node.workspace_id, node_class="AGENT"
            )
        ).explore_count == 1
        row_type, contract = {
            "node": (NodeContractRow, node),
            "receipt": (RoutingReceiptRow, receipt),
            "observation": (ExperienceRecordRow, record),
        }[target]
        foreign = new_id("workspace_entity")
        wrong = foreign if column == "workspace_id" else content_hash({"bad": contract.contract_id})
        async with postgres.sessions.begin() as session:
            await session.execute(
                update(row_type)
                .where(row_type.id == contract.contract_id)
                .values(**{column: wrong})
            )
        with pytest.raises(ValueError):
            await LedgerRegistry(postgres).ledger(
                workspace_id=foreign if column == "workspace_id" else node.workspace_id,
                node_class="AGENT",
            )


@pytest.mark.parametrize("peer_kind", ["node", "node-projection-pin", "receipt", "observation"])
async def test_unknown_peer_cost_semantics_remain_unavailable_after_read_support(
    tmp_path: Path, peer_kind: str
) -> None:
    async with postgres_stores() as (postgres,):
        node, receipt, _ = await account_rows(postgres, tmp_path, peer_kind)
        found = await postgres.list_routing_receipts_for_run_graph(
            workspace_id=node.workspace_id, run_graph_id=node.run_graph_id
        )
        expected = [] if peer_kind == "node-projection-pin" else [receipt.contract_id]
        assert [row.contract_id for row in found] == expected
        with pytest.raises(ValueError, match="EXPLORATION_ACCOUNTING_UNAVAILABLE"):
            await LedgerRegistry(postgres).ledger(
                workspace_id=node.workspace_id, node_class="AGENT"
            )
