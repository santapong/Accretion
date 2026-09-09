"""Budget safety at the real PostgreSQL transaction and replay boundary.

Each store uses an independent engine. Rows are synthetic and uniquely named;
no provider, scientific corpus or production database is involved.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from test_v04_m0_postgres_store import build
from test_v04_m2_postgres_store import postgres_stores, routing_event, seed_run
from test_v04_m7_postgres_store import seed_experience

from accretion.contracts.routing import ExperienceRecord, NodeContract, RoutingDecisionReceipt
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.routing.bandit import (
    BASELINE_COST_LCB_LABEL,
    COST_UCB_LABEL,
    NODE_CLASS_LABEL,
    LedgerRegistry,
)
from accretion.routing.ledger import ExplorationCaps


def receipt(*, workspace_id: str, project_id: str, node_hash: str | None = None):
    return build(
        RoutingDecisionReceipt,
        workspace_id=workspace_id,
        project_id=project_id,
        decision_type="EXPLORE",
        selection_propensity=0.4,
        selected_configuration_id=new_id("execution_configuration"),
        selected_configuration_hash="b" * 64,
        node_contract_hash=node_hash or "a" * 64,
        routing_request_id=new_id("routing_request"),
        labels={NODE_CLASS_LABEL: "AGENT", COST_UCB_LABEL: "0.25", BASELINE_COST_LCB_LABEL: "0.9"},
    )


@pytest.mark.parametrize("limit", ["count", "cost", "inequality"])
async def test_different_run_transactions_cannot_spend_the_same_last_slot(tmp_path: Path, limit):
    async with postgres_stores(2) as (left, right):
        project, run_left = await seed_run(left, tmp_path, "budget-left")
        _, run_right = await seed_run(left, tmp_path, "budget-right")
        workspace = new_id("workspace_entity")
        node = build(
            NodeContract, workspace_id=workspace, project_id=project.project_id, node_kind="AGENT"
        )
        await left.put_node_contract(node)
        cost = 0.75 if limit == "inequality" else 0.25
        baseline_cost = 0.0 if limit == "inequality" else 0.9
        alpha = 0.0 if limit == "inequality" else 1.0
        caps = ExplorationCaps(1 if limit == "count" else 10, 0.4 if limit == "cost" else 10)
        if limit == "inequality":
            initial = receipt(
                workspace_id=workspace, project_id=project.project_id, node_hash=node.immutable_hash
            ).model_dump(mode="python")
            initial["labels"].update({COST_UCB_LABEL: "0", BASELINE_COST_LCB_LABEL: "1"})
            initial["content_hash"] = ""
            await left.put_routing_receipt(RoutingDecisionReceipt.model_validate(initial))
        first_read = asyncio.Event()
        release = asyncio.Event()

        async def admit(store, run, *, pause=False):
            async with store.routing_transaction(
                run.run_id, budget_key=(workspace, "AGENT")
            ) as scoped:
                ledger = await LedgerRegistry(scoped).ledger(
                    workspace_id=workspace, node_class="AGENT"
                )
                allowed = ledger.can_explore(
                    candidate_cost_ucb=cost,
                    baseline_cost_lcb=baseline_cost,
                    alpha=alpha,
                    caps=caps,
                ).allowed
                if pause:
                    first_read.set()
                    await release.wait()
                if allowed:
                    payload = receipt(
                        workspace_id=workspace,
                        project_id=project.project_id,
                        node_hash=node.immutable_hash,
                    ).model_dump(mode="python")
                    payload["labels"].update(
                        {COST_UCB_LABEL: str(cost), BASELINE_COST_LCB_LABEL: str(baseline_cost)}
                    )
                    payload["content_hash"] = ""
                    await scoped.put_routing_receipt(RoutingDecisionReceipt.model_validate(payload))
                return allowed

        first = asyncio.create_task(admit(left, run_left, pause=True))
        await asyncio.wait_for(first_read.wait(), 5)
        second = asyncio.create_task(admit(right, run_right))
        # Before the fix the second run can commit while the first still holds
        # its run lock. After it, both contend on the shared budget instead.
        try:
            await asyncio.wait_for(asyncio.shield(second), 0.2)
        except TimeoutError:
            pass
        finally:
            release.set()
        assert sum(await asyncio.gather(first, second)) == 1


@pytest.mark.parametrize("observed", ["0.1", "0.25", "0.8", "1.4"])
async def test_known_overrun_is_not_forgotten_by_a_new_registry(tmp_path: Path, observed):
    async with postgres_stores(2) as (store, restarted):
        project, run = await seed_run(store, tmp_path, "overrun")
        workspace = new_id("workspace_entity")
        node = build(
            NodeContract,
            workspace_id=workspace,
            project_id=project.project_id,
            node_kind="AGENT",
            execution_instance_id=new_id("execution_instance"),
            resource_cap={
                "maximum_cost": "1",
                "maximum_latency_ms": 1000,
                "maximum_attempts": 1,
                "maximum_tool_calls": 10,
            },
        )
        await store.put_node_contract(node)
        charged = receipt(
            workspace_id=workspace, project_id=project.project_id, node_hash=node.immutable_hash
        )
        await store.put_routing_receipt(charged)
        record = build(
            ExperienceRecord,
            workspace_id=workspace,
            project_id=project.project_id,
            source_node_execution_id=node.execution_instance_id,
            configuration_hash=charged.selected_configuration_hash,
            outcomes={"quality": 0.5, "cost": observed, "latency_ms": 100},
        )
        await seed_experience(
            store,
            experience_id=record.contract_id,
            project_id=project.project_id,
            task_id=run.task_id,
            run_id=run.run_id,
        )
        await store.put_experience_record(record)
        original = await LedgerRegistry(store).ledger(workspace_id=workspace, node_class="AGENT")
        assert original.explored_cost_sum == float(observed)
        recovered = await LedgerRegistry(restarted).ledger(
            workspace_id=workspace, node_class="AGENT"
        )
        assert recovered.snapshot() == original.snapshot()
        assert recovered.explore_count == 1
        assert recovered.can_explore(
            candidate_cost_ucb=0.3, baseline_cost_lcb=0.9, alpha=1.0, caps=ExplorationCaps(10, 1.0)
        ).allowed == (float(observed) <= 0.7)
        # Revisions cannot talk already measured spend down. Retain a higher
        # observation even when a later, superseding record claims it was cheap.
        revision_id = new_id("experience")
        lower_payload = record.model_dump(mode="python")
        lower_payload.update(
            contract_id=revision_id,
            supersedes_contract_id=record.contract_id,
            content_hash="",
            labels={"experience_id": record.contract_id},
        )
        lower_payload["outcomes"]["cost"] = "0.05"
        try:
            await store.put_experience_record(
                ExperienceRecord.model_validate(lower_payload), experience_id=record.contract_id
            )
            again = await LedgerRegistry(restarted).ledger(
                workspace_id=workspace, node_class="AGENT"
            )
            assert again.snapshot() == recovered.snapshot()
        finally:
            # Migration 0020 correctly refuses downgrade with any revisions.
            # Remove only this test's revision, including after failed assertions;
            # the production store remains append-only.
            async with store.sessions.begin() as session:
                await session.execute(
                    sa.text(
                        "DELETE FROM experience_records "
                        "WHERE id = :revision_id AND experience_id = :experience_id"
                    ),
                    {"revision_id": revision_id, "experience_id": record.contract_id},
                )


@pytest.mark.parametrize("bad", [None, "unknown", "nan", "inf", "-0.1", "1.01"])
@pytest.mark.parametrize("charge_label", [COST_UCB_LABEL, BASELINE_COST_LCB_LABEL])
async def test_unreadable_explore_charge_never_becomes_an_empty_budget(
    tmp_path: Path, bad, charge_label
):
    async with postgres_stores() as (store,):
        _, _, node, row = await _seed_account(store, tmp_path)
        payload = row.model_dump(mode="python")
        if bad is None:
            del payload["labels"][charge_label]
        else:
            payload["labels"][charge_label] = bad
        payload["content_hash"] = ""
        await store.put_routing_receipt(RoutingDecisionReceipt.model_validate(payload))
        with pytest.raises(ValueError, match="unreadable charge"):
            await LedgerRegistry(store).ledger(workspace_id=node.workspace_id, node_class="AGENT")


async def _seed_account(store, tmp_path):
    project, run = await seed_run(store, tmp_path, "account")
    workspace = new_id("workspace_entity")
    node = build(
        NodeContract,
        workspace_id=workspace,
        project_id=project.project_id,
        node_kind="AGENT",
        execution_instance_id=new_id("execution_instance"),
    )
    await store.put_node_contract(node)
    row = receipt(
        workspace_id=workspace, project_id=project.project_id, node_hash=node.immutable_hash
    )
    return project, run, node, row


@pytest.mark.parametrize("independent", ["workspace", "node_class"])
async def test_unrelated_budget_keys_do_not_wait_for_each_other(tmp_path: Path, independent):
    async with postgres_stores(2) as (left, right):
        _, run, node, _ = await _seed_account(left, tmp_path)
        _, other = await seed_run(left, tmp_path, "independent")
        key = (node.workspace_id, "AGENT")
        other_key = (
            (new_id("workspace_entity"), "AGENT")
            if independent == "workspace"
            else (node.workspace_id, "VERIFIER")
        )
        async with left.routing_transaction(run.run_id, budget_key=key):

            async def acquire():
                async with right.routing_transaction(other.run_id, budget_key=other_key):
                    return True

            assert await asyncio.wait_for(acquire(), 3)


async def test_rollback_and_lost_commit_ack_do_not_create_or_double_charge(tmp_path: Path):
    async with postgres_stores(2) as (store, restarted):
        _, run, node, row = await _seed_account(store, tmp_path)
        kwargs = {"budget_key": (node.workspace_id, "AGENT")}
        event = routing_event(run, "budget-rollback")
        with pytest.raises(RuntimeError, match="injected rollback"):
            async with store.routing_transaction(run.run_id, **kwargs) as scoped:
                await scoped.put_routing_receipt(row)
                await scoped.append_event(event)
                raise RuntimeError("injected rollback")
        assert await restarted.get_routing_receipt(row.contract_id) is None
        assert await restarted.list_events(run.run_id) == []
        async with restarted.routing_transaction(run.run_id, **kwargs) as scoped:
            await scoped.put_routing_receipt(row)
            await scoped.append_event(event)
        # The client lost the acknowledgement; retrying the identical durable
        # record and event must leave one charge, not charge the retry again.
        async with store.routing_transaction(run.run_id, **kwargs) as scoped:
            # DefaultNodeRoutingService.route returns the committed receipt
            # before repeating events when the routing request already exists.
            existing = await scoped.get_routing_receipt_for_request(row.routing_request_id)
            assert existing == row
            await scoped.put_routing_receipt(row)
        ledger = await LedgerRegistry(restarted).ledger(
            workspace_id=node.workspace_id, node_class="AGENT"
        )
        assert ledger.explore_count == 1
        assert ledger.explored_cost_sum == 0.25
        assert len(await restarted.list_events(run.run_id)) == 1
        changed = row.model_dump(mode="python")
        changed["labels"][COST_UCB_LABEL] = "0.01"
        changed["content_hash"] = ""
        with pytest.raises(ValueError):
            await store.put_routing_receipt(RoutingDecisionReceipt.model_validate(changed))
        assert (
            await restarted.get_routing_receipt(row.contract_id)
        ).content_hash == row.content_hash


@pytest.mark.parametrize(
    "damage",
    [
        "missing_node",
        "missing_class",
        "unknown_class",
        "wrong_class",
        "foreign_configuration",
        "foreign_project",
    ],
)
async def test_uncertain_provenance_refuses_credit(tmp_path: Path, damage):
    async with postgres_stores() as (store,):
        project, run, node, row = await _seed_account(store, tmp_path)
        if damage == "missing_node":
            row = receipt(workspace_id=node.workspace_id, project_id=project.project_id)
        if damage in {"missing_class", "unknown_class", "wrong_class"}:
            payload = row.model_dump(mode="python")
            if damage == "missing_class":
                del payload["labels"][NODE_CLASS_LABEL]
            else:
                payload["labels"][NODE_CLASS_LABEL] = (
                    "bogus" if damage == "unknown_class" else "TOOL"
                )
            payload["content_hash"] = ""
            row = RoutingDecisionReceipt.model_validate(payload)
        await store.put_routing_receipt(row)
        if damage.startswith("foreign"):
            other_project = project
            if damage == "foreign_project":
                other_project, _ = await seed_run(store, tmp_path, "wrong-project")
            record = build(
                ExperienceRecord,
                workspace_id=node.workspace_id,
                project_id=other_project.project_id,
                source_node_execution_id=node.execution_instance_id,
                configuration_hash="c" * 64
                if damage == "foreign_configuration"
                else row.selected_configuration_hash,
                outcomes={"quality": 0.9, "cost": "0", "latency_ms": 1},
            )
            await seed_experience(
                store,
                experience_id=record.contract_id,
                project_id=other_project.project_id,
                task_id=run.task_id,
                run_id=run.run_id,
            )
            await store.put_experience_record(record)
        with pytest.raises(ValueError, match="EXPLORATION_ACCOUNTING_UNAVAILABLE"):
            await LedgerRegistry(store).ledger(workspace_id=node.workspace_id, node_class="AGENT")


def _process_admit(url, run_id, payload, attempted, entered, release, result):
    """One independent interpreter/engine; no process-global lock can satisfy this test."""

    async def execute():
        engine = create_engine(url)
        store = PostgresStore(create_session_factory(engine))
        row = RoutingDecisionReceipt.model_validate(payload)
        try:
            attempted.set()
            async with store.routing_transaction(
                run_id, budget_key=(row.workspace_id, "AGENT")
            ) as scoped:
                ledger = await LedgerRegistry(scoped).ledger(
                    workspace_id=row.workspace_id, node_class="AGENT"
                )
                allowed = ledger.can_explore(
                    candidate_cost_ucb=0.25,
                    baseline_cost_lcb=0.9,
                    alpha=1,
                    caps=ExplorationCaps(1, 1),
                ).allowed
                entered.set()
                if release is not None and not await asyncio.to_thread(release.wait, 15):
                    raise TimeoutError("parent did not release the witness")
                if allowed:
                    await scoped.put_routing_receipt(row)
                result.put(("ok", allowed))
        except Exception as exc:
            result.put(("error", type(exc).__name__))
        finally:
            await engine.dispose()

    asyncio.run(execute())


async def test_independent_processes_share_the_budget_lock(tmp_path: Path):
    async with postgres_stores() as (store,):
        project, first_run, node, first_row = await _seed_account(store, tmp_path)
        _, second_run = await seed_run(store, tmp_path, "second-process")
        second_row = receipt(
            workspace_id=node.workspace_id,
            project_id=project.project_id,
            node_hash=node.immutable_hash,
        )
    context = multiprocessing.get_context("spawn")
    attempted = [context.Event(), context.Event()]
    entered = [context.Event(), context.Event()]
    release = context.Event()
    result = context.Queue()
    processes = [
        context.Process(
            target=_process_admit,
            args=(
                os.environ["ACCRETION_TEST_POSTGRES_URL"],
                run.run_id,
                row.model_dump(mode="json"),
                attempted[index],
                entered[index],
                release if index == 0 else None,
                result,
            ),
        )
        for index, (run, row) in enumerate(((first_run, first_row), (second_run, second_row)))
    ]
    try:
        processes[0].start()
        assert await asyncio.to_thread(entered[0].wait, 15), result.get(timeout=1)
        processes[1].start()
        assert await asyncio.to_thread(attempted[1].wait, 15)
        assert not await asyncio.to_thread(entered[1].wait, 0.2)
        release.set()
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            assert process.exitcode == 0
        outcomes = [result.get(timeout=2), result.get(timeout=2)]
        assert sorted(outcomes) == [("ok", False), ("ok", True)]
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
        result.close()


async def test_service_uses_its_budget_locked_store_and_accounting_errors_refuse(tmp_path: Path):
    from test_v04_m7_bandit import route_once, setup_exploring

    fixture = await setup_exploring(tmp_path)
    original = fixture.store.routing_transaction
    seen = []

    @asynccontextmanager
    async def tracking(run_id, *, budget_key=None):
        seen.append(budget_key)
        async with original(run_id, budget_key=budget_key) as scoped:
            yield scoped

    fixture.store.routing_transaction = tracking
    await route_once(fixture)
    assert seen == [(fixture.workspace_id, "AGENT")]
    captured = fixture.behavior.calls[0]
    assert isinstance(captured["store"], MemoryStore)
    assert captured["store"] is not fixture.store

    class UnreadableStore:
        async def list_routing_receipts(self, **kwargs: Any):
            raise RuntimeError("never echo a private storage error")

    decision = await fixture.bandit.select(**{**captured, "store": UnreadableStore()})
    assert decision.propensity == 1
    assert decision.labels["exploration.refused"] == "EXPLORATION_ACCOUNTING_UNAVAILABLE"


@pytest.mark.parametrize("contradictory", [False, True])
async def test_duplicate_reader_rows_do_not_double_charge_or_rewrite_history(
    tmp_path: Path, contradictory
):
    store = MemoryStore()
    _, _, node, row = await _seed_account(store, tmp_path)
    await store.put_routing_receipt(row)
    payload = row.model_dump(mode="python")
    if contradictory:
        payload["labels"][COST_UCB_LABEL] = "0.01"
        payload["content_hash"] = ""
    duplicate = RoutingDecisionReceipt.model_validate(payload)

    class DuplicateReader:
        def __getattr__(self, name):
            return getattr(store, name)

        async def list_routing_receipts(self, **kwargs):
            return [row, duplicate]

    registry = LedgerRegistry(DuplicateReader())
    if contradictory:
        with pytest.raises(ValueError, match="contradictory duplicate"):
            await registry.ledger(workspace_id=node.workspace_id, node_class="AGENT")
    else:
        ledger = await registry.ledger(workspace_id=node.workspace_id, node_class="AGENT")
        assert ledger.explore_count == 1
        assert ledger.explored_cost_sum == 0.25
