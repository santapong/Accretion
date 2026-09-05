"""Routing publication must not roll unrelated run state backward."""

import pytest
from test_v04_m2_service import _seed

from accretion.contracts import EventType, RunState


@pytest.mark.asyncio
async def test_routing_commit_preserves_a_concurrent_run_update():
    seeded = await _seed()
    async with seeded.store.routing_transaction(seeded.run.run_id) as transaction:
        await seeded.service._event(
            transaction, seeded.run, seeded.receipt, "created", EventType.ROUTING_DECISION_CREATED
        )
        await seeded.store.update_run(seeded.run.run_id, RunState.PAUSED)
    current = await seeded.store.get_run(seeded.run.run_id)
    assert current is not None
    assert current.state is RunState.PAUSED
    assert current.last_sequence == 1
