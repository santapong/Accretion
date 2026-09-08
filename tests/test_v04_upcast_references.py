"""Writer-sealed reference chains remain discoverable through a reader projection.

These fixtures emulate a newer minor writer in an isolated MemoryStore. They never
rewrite an application database or make an unknown contract executable.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from test_v04_m0_store import build
from test_v04_m2_service import RoutableExecution, SeededRouting, _routable_execution, _seed

from accretion.contracts.canonical import CanonicalContract, canonical_json, content_hash
from accretion.contracts.routing import (
    ConfigurationCandidate,
    NodeContract,
    RoutingContext,
    RoutingDecisionReceipt,
    VerificationSpec,
)
from accretion.contracts.upcast import UPCAST_DROPPED_KEYS_LABEL
from accretion.persistence.store import MemoryStore
from accretion.routing.bandit import (
    BASELINE_COST_LCB_LABEL,
    COST_UCB_LABEL,
    NODE_CLASS_LABEL,
    LedgerRegistry,
)
from accretion.routing.errors import RoutingError
from accretion.routing.protocols import FrozenNode, RoutingMode

pytestmark = pytest.mark.asyncio


class FutureNodeContract(NodeContract):
    reader_note: str | None = None


class FutureVerificationSpec(VerificationSpec):
    reader_note: str | None = None


class FutureRoutingContext(RoutingContext):
    reader_note: str | None = None


class FutureRoutingDecisionReceipt(RoutingDecisionReceipt):
    reader_note: str | None = None


class FutureConfigurationCandidate(ConfigurationCandidate):
    reader_note: str | None = None


def _reseal[C: CanonicalContract](model: type[C], original: CanonicalContract, **updates: Any) -> C:
    document = original.model_dump(mode="python")
    document.update(updates)
    for key in ("content_hash", *model.DERIVED_HASH_FIELDS):
        document.pop(key, None)
    return model.model_validate(document)


def _writer_row(store: MemoryStore, table: str, record: CanonicalContract) -> None:
    """Test-only peer write, retaining the full writer seal and promoted columns."""

    row = store.v04_contracts[table][record.contract_id]
    store.v04_contracts[table][record.contract_id] = row._replace(
        content_hash=record.content_hash,
        schema_version=record.schema_version,
        payload=record.model_dump(mode="json"),
    )


async def future_reference_chain() -> tuple[SeededRouting, FrozenNode, NodeContract]:
    seeded = await _seed()
    context = await seeded.store.get_routing_request(seeded.receipt.routing_request_id)
    assert context is not None
    node = await seeded.store.get_node_contract(context.node_contract_ref.node_contract_id)
    assert node is not None
    spec = build(
        VerificationSpec,
        contract_id=node.verification_spec_ref.verification_spec_id,
        workspace_id=node.workspace_id,
        project_id=node.project_id,
    )
    await seeded.store.put_verification_spec(spec)
    writer_spec = _reseal(
        FutureVerificationSpec, spec, schema_version="1.1.0", reader_note="peer annotation"
    )
    writer_node = _reseal(
        FutureNodeContract,
        node,
        schema_version="1.1.0",
        reader_note="peer annotation",
        verification_spec_ref={
            **node.verification_spec_ref.model_dump(mode="python"),
            "content_hash": writer_spec.content_hash,
        },
    )
    context = _reseal(
        RoutingContext,
        context,
        node_contract_ref={
            "node_contract_id": writer_node.contract_id,
            "immutable_hash": writer_node.immutable_hash,
        },
    )
    seeded.receipt = _reseal(
        RoutingDecisionReceipt, seeded.receipt, node_contract_hash=writer_node.immutable_hash
    )
    _writer_row(seeded.store, "verification_specs", writer_spec)
    _writer_row(seeded.store, "node_contracts", writer_node)
    _writer_row(seeded.store, "routing_requests", context)
    _writer_row(seeded.store, "routing_receipts", seeded.receipt)

    projected_node = await seeded.store.get_node_contract(writer_node.contract_id)
    projected_spec = await seeded.store.get_verification_spec(writer_spec.contract_id)
    assert projected_node is not None and projected_spec is not None
    assert projected_node.labels[UPCAST_DROPPED_KEYS_LABEL] == "reader_note"
    assert projected_node.immutable_hash != writer_node.immutable_hash
    assert projected_spec.content_hash != writer_spec.content_hash
    assert writer_node.verification_spec_ref.content_hash == writer_spec.content_hash
    assert context.node_contract_ref.immutable_hash == writer_node.immutable_hash
    return (
        seeded,
        FrozenNode(
            projected_node,
            projected_spec,
            projected_node.objective_contract_ref,
            projected_node.execution_instance_id,
        ),
        writer_node,
    )


async def test_graph_receipt_listing_uses_the_verified_writer_node_identity() -> None:
    seeded, frozen, writer = await future_reference_chain()
    before = canonical_json(
        seeded.store.v04_contracts["node_contracts"][writer.contract_id].payload
    )

    receipts = await seeded.store.list_routing_receipts_for_run_graph(
        workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
    )

    assert [record.contract_id for record in receipts] == [seeded.receipt.contract_id]
    assert await seeded.service.latest_receipt(frozen=frozen, run=seeded.run) == seeded.receipt
    assert (
        canonical_json(seeded.store.v04_contracts["node_contracts"][writer.contract_id].payload)
        == before
    )


async def test_lineage_lookup_does_not_report_a_valid_peer_chain_as_missing() -> None:
    seeded, _, _ = await future_reference_chain()

    assert await seeded.service._run_for(seeded.receipt) == seeded.run


async def test_a_copied_hash_does_not_authorize_a_different_receipt_body() -> None:
    seeded, _, _ = await future_reference_chain()
    # model_copy deliberately bypasses validation. The stored receipt, not this
    # caller-supplied object's copied digest or label, must establish identity.
    altered = seeded.receipt.model_copy(update={"labels": {"run_id": "run_unrelated"}})
    assert altered.content_hash == seeded.receipt.content_hash
    with pytest.raises(RoutingError) as error:
        await seeded.service._run_for(altered)
    assert error.value.status_code == 404


async def test_a_peer_payload_edited_after_sealing_is_not_discoverable() -> None:
    seeded, _, writer = await future_reference_chain()
    row = seeded.store.v04_contracts["node_contracts"][writer.contract_id]
    corrupt = deepcopy(row.payload)
    corrupt["reader_note"] = "edited after the peer sealed it"
    seeded.store.v04_contracts["node_contracts"][writer.contract_id] = row._replace(payload=corrupt)

    with pytest.raises(ValueError, match="content_hash"):
        await seeded.store.list_routing_receipts_for_run_graph(
            workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
        )


async def test_a_valid_header_does_not_launder_a_forged_writer_immutable_hash() -> None:
    seeded, _, writer = await future_reference_chain()
    row = seeded.store.v04_contracts["node_contracts"][writer.contract_id]
    corrupt = deepcopy(row.payload)
    corrupt["immutable_hash"] = "f" * 64
    # The outer seal is coherent. The defect is the inner derived identity, which
    # the projection drops; checking only the outer seal would launder it.
    corrupt["content_hash"] = content_hash(corrupt)
    seeded.store.v04_contracts["node_contracts"][writer.contract_id] = row._replace(
        content_hash=corrupt["content_hash"], payload=corrupt
    )

    with pytest.raises(ValueError, match="immutable_hash"):
        await seeded.store.list_routing_receipts_for_run_graph(
            workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
        )


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("contract_id", "nct_other"),
        ("project_id", "prj_other"),
        ("content_hash", "e" * 64),
        ("schema_version", "1.2.0"),
    ],
)
async def test_graph_lookup_refuses_conflicting_promoted_node_identity(
    field: str, wrong: str
) -> None:
    seeded, _, writer = await future_reference_chain()
    row = seeded.store.v04_contracts["node_contracts"][writer.contract_id]
    seeded.store.v04_contracts["node_contracts"][writer.contract_id] = row._replace(
        **{field: wrong}
    )

    with pytest.raises(ValueError):
        await seeded.store.list_routing_receipts_for_run_graph(
            workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
        )


@pytest.mark.parametrize("field", ["workspace_id", "run_graph_id"])
async def test_peer_read_is_still_scoped_to_the_requested_workspace_and_graph(
    field: str,
) -> None:
    seeded, _, writer = await future_reference_chain()
    scope = {
        "workspace_id": writer.workspace_id,
        "run_graph_id": writer.run_graph_id,
    }
    scope[field] = "unrelated-scope"

    assert await seeded.store.list_routing_receipts_for_run_graph(**scope) == []


@pytest.mark.parametrize("target", ["context", "receipt"])
async def test_read_lineage_refuses_a_cross_workspace_link(target: str) -> None:
    seeded, _, _ = await future_reference_chain()
    context = await seeded.store.get_routing_request(seeded.receipt.routing_request_id)
    assert context is not None
    if target == "context":
        altered = _reseal(
            RoutingContext,
            context,
            workspace_id="wks_foreign",
            task_features=_reseal(
                type(context.task_features), context.task_features, workspace_id="wks_foreign"
            ),
            project_features=_reseal(
                type(context.project_features),
                context.project_features,
                workspace_id="wks_foreign",
            ),
        )
        row = seeded.store.v04_contracts["routing_requests"][altered.contract_id]
        _writer_row(seeded.store, "routing_requests", altered)
        seeded.store.v04_contracts["routing_requests"][altered.contract_id] = (
            seeded.store.v04_contracts["routing_requests"][altered.contract_id]._replace(
                workspace_id=altered.workspace_id
            )
        )
        assert row.workspace_id != altered.workspace_id
    else:
        seeded.receipt = _reseal(RoutingDecisionReceipt, seeded.receipt, workspace_id="wks_foreign")
        _writer_row(seeded.store, "routing_receipts", seeded.receipt)
        row = seeded.store.v04_contracts["routing_receipts"][seeded.receipt.contract_id]
        seeded.store.v04_contracts["routing_receipts"][seeded.receipt.contract_id] = row._replace(
            workspace_id=seeded.receipt.workspace_id
        )

    with pytest.raises(RoutingError) as error:
        await seeded.service._run_for(seeded.receipt)
    assert error.value.status_code == 404


async def prepare_peer_execution(
    tmp_path: Path, peer_kind: str
) -> tuple[RoutableExecution, RoutingDecisionReceipt]:
    execution = await _routable_execution(tmp_path)
    receipt = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    if peer_kind != "current":
        node = execution.frozen.node_contract
        if peer_kind == "label-only":
            node = _reseal(
                NodeContract,
                node,
                labels={**node.labels, UPCAST_DROPPED_KEYS_LABEL: "reader_note"},
            )
        elif peer_kind == "node":
            node = _reseal(
                FutureNodeContract,
                node,
                schema_version="1.1.0",
                reader_note="unknown node execution semantics",
            )
        elif peer_kind == "spec":
            spec = _reseal(
                FutureVerificationSpec,
                execution.frozen.verification_spec,
                schema_version="1.1.0",
                reader_note="unknown independent-verification semantics",
            )
            _writer_row(execution.store, "verification_specs", spec)
            node = _reseal(
                NodeContract,
                node,
                verification_spec_ref={
                    **node.verification_spec_ref.model_dump(mode="python"),
                    "content_hash": spec.content_hash,
                },
            )
        elif peer_kind == "candidate":
            candidates = await execution.service._candidates(execution.store, receipt)
            candidate = next(
                item
                for item in candidates
                if item.configuration.contract_id == receipt.selected_configuration_id
            )
            _writer_row(
                execution.store,
                "configuration_candidates",
                _reseal(
                    FutureConfigurationCandidate,
                    candidate,
                    schema_version="1.1.0",
                    reader_note="unknown candidate eligibility semantics",
                ),
            )
        context = await execution.store.get_routing_request(receipt.routing_request_id)
        assert context is not None
        context = _reseal(
            RoutingContext,
            context,
            node_contract_ref={
                "node_contract_id": node.contract_id,
                "immutable_hash": node.immutable_hash,
            },
        )
        if peer_kind == "context":
            context = _reseal(
                FutureRoutingContext,
                context,
                schema_version="1.1.0",
                reader_note="unknown routing-context semantics",
            )
        receipt = _reseal(RoutingDecisionReceipt, receipt, node_contract_hash=node.immutable_hash)
        if peer_kind == "receipt":
            receipt = _reseal(
                FutureRoutingDecisionReceipt,
                receipt,
                schema_version="1.1.0",
                reader_note="unknown dispatch-receipt semantics",
            )
        _writer_row(execution.store, "node_contracts", node)
        _writer_row(execution.store, "routing_requests", context)
        _writer_row(execution.store, "routing_receipts", receipt)
        # The API hands callers the reader projection. Comparing that projection
        # to itself must not erase the underlying writer's unsupported fields.
        projected_receipt = await execution.store.get_routing_receipt(receipt.contract_id)
        assert projected_receipt is not None
        receipt = projected_receipt
    return execution, receipt


@pytest.mark.parametrize(
    "peer_kind", ["current", "label-only", "node", "spec", "context", "receipt", "candidate"]
)
async def test_reading_a_peer_chain_does_not_authorize_its_unknown_execution_fields(
    tmp_path: Path, peer_kind: str
) -> None:
    execution, receipt = await prepare_peer_execution(tmp_path, peer_kind)
    assert (await execution.service._run_for(receipt)).run_id == execution.run.run_id

    if peer_kind not in {"current", "label-only"}:
        with pytest.raises(RoutingError) as error:
            await execution.service.claim_dispatch(receipt=receipt, run=execution.run)
        # A disappearance or unrelated runtime failure is not the required refusal.
        assert error.value.code == "ROUTING_INPUT_INVALID"
    else:
        await execution.service.claim_dispatch(receipt=receipt, run=execution.run)

    dispatches = [
        event
        for event in await execution.store.list_events(execution.run.run_id)
        if event.native_type == "accretion/routing/dispatch"
    ]
    assert len(dispatches) == (1 if peer_kind in {"current", "label-only"} else 0)


@pytest.mark.parametrize("peer_kind", ["context", "receipt"])
async def test_override_cannot_reseal_unknown_execution_fields_into_a_current_receipt(
    tmp_path: Path, peer_kind: str
) -> None:
    execution, receipt = await prepare_peer_execution(tmp_path, peer_kind)
    candidates = await execution.service._candidates(execution.store, receipt)
    selected = next(item for item in candidates if item.hard_eligible)
    with pytest.raises(RoutingError) as error:
        await execution.service.override(
            receipt_id=receipt.contract_id,
            candidate_id=selected.contract_id,
            reason_code="REVIEWED",
            reason="Review cannot interpret a newer writer's unknown fields",
            expected_receipt_version=1,
            principal=execution.frozen.node_contract.created_by,
        )
    assert error.value.code == "ROUTING_INPUT_INVALID"
    # The operator can still stop a peer decision. Its cancelled successor cannot
    # be used to recover an executable override after the projection was copied.
    cancelled = await execution.service.cancel(
        receipt_id=receipt.contract_id,
        principal=execution.frozen.node_contract.created_by,
    )
    assert cancelled.labels["routing_status"] == "CANCELLED"
    with pytest.raises(RoutingError) as cancelled_error:
        await execution.service.override(
            receipt_id=cancelled.contract_id,
            candidate_id=selected.contract_id,
            reason_code="REVIEWED",
            reason="Try to revive a cancelled peer receipt",
            expected_receipt_version=2,
            principal=execution.frozen.node_contract.created_by,
        )
    assert cancelled_error.value.code == "RECEIPT_CANCELLED"
    assert all(
        event.native_type != "accretion/routing/dispatch"
        for event in await execution.store.list_events(execution.run.run_id)
    )


@pytest.mark.parametrize("pin", ["writer", "projection"])
async def test_peer_cost_inputs_remain_unavailable_for_exploration_accounting(pin: str) -> None:
    seeded, frozen, writer = await future_reference_chain()
    receipt = _reseal(
        RoutingDecisionReceipt,
        seeded.receipt,
        node_contract_hash=(
            writer.immutable_hash if pin == "writer" else frozen.node_contract.immutable_hash
        ),
        decision_type="EXPLORE",
        selection_propensity=0.4,
        labels={
            NODE_CLASS_LABEL: writer.node_kind.value,
            COST_UCB_LABEL: "0.25",
            BASELINE_COST_LCB_LABEL: "0.9",
        },
    )
    _writer_row(seeded.store, "routing_receipts", receipt)
    # Read lineage is available; projecting future resource semantics must not
    # make this receipt disappear from accounting as if the spend never happened.
    found = await seeded.store.list_routing_receipts_for_run_graph(
        workspace_id=writer.workspace_id, run_graph_id=writer.run_graph_id
    )
    assert found == ([receipt] if pin == "writer" else [])
    with pytest.raises(ValueError, match="EXPLORATION_ACCOUNTING_UNAVAILABLE"):
        await LedgerRegistry(seeded.store).ledger(
            workspace_id=writer.workspace_id, node_class=writer.node_kind.value
        )
