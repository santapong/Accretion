"""Corruption witnesses must not poison later CI gates sharing the same database."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
import test_v05_authority as authority_tests
import test_v05_authority_providers as provider_tests
from fastapi import FastAPI
from test_v05_lifecycle import manager_for

from accretion.api import main as api_main
from accretion.config import Settings
from accretion.contracts.canonical import canonical_json
from accretion.persistence.models import RoboticsContractRow
from accretion.persistence.store import MemoryStore
from accretion.robotics.runtime_store import RuntimeBinding

lab = authority_tests.lab
inventory = provider_tests.inventory


@pytest.mark.parametrize(
    "corruption_test",
    [
        authority_tests.test_original_binding_corruption_cannot_allocate,
        authority_tests.test_runtime_index_corruption_is_rejected,
    ],
    ids=["original-binding", "lease-index"],
)
@pytest.mark.parametrize("assertion_fails", [False, True], ids=["success", "assertion-failure"])
async def test_corruption_witness_then_unfiltered_reconcile_and_fresh_lifespan(
    lab, tmp_path, monkeypatch, corruption_test, assertion_fails
):
    # Exercise the actual shared corruption tests, including cleanup after an
    # assertion failure. Their genuine production refusal assertion runs first.
    with monkeypatch.context() as patch:
        if assertion_fails:
            original_rejected = authority_tests.rejected

            @contextmanager
            def rejected_then_fail(code):
                with original_rejected(code):
                    yield
                raise AssertionError("synthetic failure after verifying refusal")

            patch.setattr(authority_tests, "rejected", rejected_then_fail)
            with pytest.raises(AssertionError, match="synthetic failure after verifying refusal"):
                await corruption_test(lab)
        else:
            await corruption_test(lab)

    if isinstance(lab.state, MemoryStore):
        original = lab.state.robotics_registry_state["records"][
            lab.binding.contract_id
        ].original_json
    else:
        async with lab.state.sessions() as session:
            row = await session.get(RoboticsContractRow, lab.binding.contract_id)
            assert row is not None
            original = row.original_json
    assert original == canonical_json(lab.binding).decode()
    if lab.lease is not None:
        assert await lab.authority.get_lease(lab.scope(), lab.lease.id) == lab.lease
    await assert_global_reconcile_and_fresh_lifespan(lab, tmp_path, monkeypatch)


@pytest.mark.parametrize("failure", ["wrong_operator", "wrong_orchestrator"])
@pytest.mark.parametrize("assertion_fails", [False, True], ids=["success", "assertion-failure"])
async def test_provider_binding_corruption_then_global_reconcile(
    inventory, tmp_path, monkeypatch, failure, assertion_fails
):
    # Invoke the real provider corruption test before the same global CI startup
    # path. Independent Memory fixtures previously hid this cross-test PG leak.
    lab = inventory.lab
    original_bind = lab.bind
    original_binding = None

    async def capture_original_binding():
        nonlocal original_binding
        ep = await original_bind()
        async with lab.authority.store.transaction(lab.project) as tx:
            original_binding = await tx.get(RuntimeBinding, ep.binding_id)
        return ep

    with monkeypatch.context() as patch:
        patch.setattr(lab, "bind", capture_original_binding)
        if assertion_fails:
            original_snapshot = provider_tests.snapshot
            calls = 0

            async def snapshot_then_fail(case):
                nonlocal calls
                result = await original_snapshot(case)
                calls += 1
                if calls == 2:
                    # The production refusal was already asserted between the
                    # two snapshots; inject failure into the unchanged-state check.
                    raise AssertionError("synthetic failure after provider refusal")
                return result

            patch.setattr(provider_tests, "snapshot", snapshot_then_fail)
            with pytest.raises(AssertionError, match="synthetic failure after provider refusal"):
                await provider_tests.test_policy_refusals_leave_state_unchanged(inventory, failure)
            assert calls == 2
        else:
            await provider_tests.test_policy_refusals_leave_state_unchanged(inventory, failure)

    assert original_binding is not None
    async with lab.authority.store.transaction(lab.project) as tx:
        assert await tx.get(RuntimeBinding, original_binding.id) == original_binding
    await assert_global_reconcile_and_fresh_lifespan(lab, tmp_path, monkeypatch)


async def assert_global_reconcile_and_fresh_lifespan(lab, tmp_path, monkeypatch):
    before = await lab.state.get_run(lab.run.run_id)
    manager = manager_for(lab, tmp_path)
    # Do not restrict list_runs to this fixture: later CI invocations scan the
    # entire database. Repeating reconciliation must preserve bound run ownership.
    await manager.reconcile()
    await manager.reconcile()
    assert await lab.state.get_run(lab.run.run_id) == before
    assert not manager.background

    if not isinstance(lab.state, MemoryStore):
        settings = Settings(
            _env_file=None,
            database_url=authority_tests.POSTGRES_URL,
            data_dir=tmp_path / "lifespan",
            router_artifact_dir=tmp_path / "router-artifacts",
            enable_live_providers=False,
            auto_resume_on_reconcile=False,
        )
        monkeypatch.setattr(api_main, "get_settings", lambda: settings)
        fresh_app = FastAPI()
        # This is the real startup path with a fresh engine/store against the
        # same database, including the actual global manager.reconcile call.
        async with api_main.lifespan(fresh_app):
            assert await fresh_app.state.manager.store.get_run(lab.run.run_id) == before
            assert not fresh_app.state.manager.background
