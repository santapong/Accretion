"""Writer-sealed reference chains remain discoverable through a reader projection.

These fixtures emulate a newer minor writer in an isolated MemoryStore. They never
rewrite an application database or make an unknown contract executable.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_v04_m0_store import build
from test_v04_m2_service import SeededRouting, _seed

from accretion.contracts.canonical import CanonicalContract, canonical_json
from accretion.contracts.routing import (
    NodeContract,
    RoutingContext,
    RoutingDecisionReceipt,
    VerificationSpec,
)
from accretion.contracts.upcast import UPCAST_DROPPED_KEYS_LABEL
from accretion.persistence.store import MemoryStore
from accretion.routing.protocols import FrozenNode

pytestmark = pytest.mark.asyncio


class FutureNodeContract(NodeContract):
    reader_note: str | None = None


class FutureVerificationSpec(VerificationSpec):
    reader_note: str | None = None


def _reseal[C: CanonicalContract](
    model: type[C], original: CanonicalContract, **updates: Any
) -> C:
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
    assert canonical_json(
        seeded.store.v04_contracts["node_contracts"][writer.contract_id].payload
    ) == before


async def test_lineage_lookup_does_not_report_a_valid_peer_chain_as_missing() -> None:
    seeded, _, _ = await future_reference_chain()

    assert await seeded.service._run_for(seeded.receipt) == seeded.run
