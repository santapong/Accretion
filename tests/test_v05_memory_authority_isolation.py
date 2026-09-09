"""Memory isolation regressions; no operational approvals, providers or DB server."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from accretion.contracts import (
    Capability,
    CapabilityPolicy,
    Principal,
    PrincipalStatus,
    PrincipalType,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.persistence.store import MemoryStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.runtime_store import RuntimeStore


def records(suffix="test"):
    task = Task(
        envelope=TaskEnvelope(
            task_id="task-" + suffix,
            project_id="project-" + suffix,
            objective="Memory transaction construction",
        )
    )
    run = Run(
        run_id="run-" + suffix,
        task_id=task.envelope.task_id,
        project_id=task.envelope.project_id,
        provider=Provider.FAKE,
        state=RunState.PENDING,
    )
    return task, run


@pytest.mark.parametrize("commit", [False, True])
async def test_generic_readers_cannot_observe_uncommitted_task_or_run(commit):
    state = MemoryStore()
    store = RuntimeStore(state)
    task, run = records()
    entered, release = asyncio.Event(), asyncio.Event()

    async def unit_of_work():
        try:
            async with store.transaction(run.project_id) as tx:
                await tx.create_task_run(task, run)
                entered.set()
                await release.wait()
                if not commit:
                    raise ValueError("synthetic failure after inserting real DTOs")
        except ValueError:
            pass

    writer = asyncio.create_task(unit_of_work())
    await entered.wait()
    readers = [
        asyncio.create_task(state.get_task(task.envelope.task_id)),
        asyncio.create_task(state.get_run(run.run_id)),
        asyncio.create_task(state.list_runs()),
    ]
    await asyncio.sleep(0.02)
    assert all(not reader.done() for reader in readers)
    release.set()
    await asyncio.wait_for(writer, 2)
    actual_task, actual_run, runs = await asyncio.wait_for(asyncio.gather(*readers), 2)
    assert (actual_task is not None) == (actual_run is not None) == bool(runs) == commit


async def test_late_expiry_cannot_erase_concurrent_create_or_undo_cancellation():
    state = MemoryStore()
    now = {"value": datetime.now(UTC)}
    store = RuntimeStore(state, clock=lambda: now["value"])
    old_task, old_run = records("old")
    await state.create_task(old_task)
    await state.create_run(old_run)
    entered, release = asyncio.Event(), asyncio.Event()

    async def expire():
        with pytest.raises(RoboticsError):
            async with store.transaction("different-project") as tx:
                tx.require_valid_interval(
                    now["value"], now["value"] + timedelta(seconds=1), Code.CAPABILITY_DENIED
                )
                entered.set()
                await release.wait()

    pending = asyncio.create_task(expire())
    await entered.wait()
    new_task, new_run = records("new")
    create_task = asyncio.create_task(state.create_task(new_task))
    create_run = asyncio.create_task(state.create_run(new_run))
    cancel_run = asyncio.create_task(state.update_run(old_run.run_id, RunState.CANCELLED))
    await asyncio.sleep(0.02)
    assert not any(task.done() for task in (create_task, create_run, cancel_run))
    now["value"] += timedelta(seconds=2)
    release.set()
    await asyncio.wait_for(asyncio.gather(pending, create_task, create_run, cancel_run), 2)
    assert (await state.get_run(old_run.run_id)).state is RunState.CANCELLED
    assert await state.get_run(new_run.run_id) == new_run
    assert await state.get_task(new_task.envelope.task_id) == new_task


async def test_child_task_does_not_inherit_parent_transaction_lock_ownership():
    state = MemoryStore()
    store = RuntimeStore(state)
    task, run = records()
    await state.create_task(task)
    await state.create_run(run)
    async with store.transaction(run.project_id):
        child = asyncio.create_task(state.get_run(run.run_id))
        await asyncio.sleep(0.02)
        assert not child.done()
        # The same task's public reads can nest without a self-deadlock.
        assert await state.get_run(run.run_id) == run
    assert await asyncio.wait_for(child, 2) == run


async def test_cancelling_waiter_does_not_release_other_tasks_transaction_lock():
    state = MemoryStore()
    store = RuntimeStore(state)
    _, run = records()
    async with store.transaction(run.project_id):
        waiter = asyncio.create_task(state.create_run(run))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert state.robotics_registry_lock.locked()
    assert not state.robotics_registry_lock.locked()
    assert await state.get_run(run.run_id) is None


@pytest.mark.parametrize(
    "record_type", ["run", "task", "principal", "membership", "capability", "policy"]
)
async def test_authority_models_are_copied_on_input_return_and_getters(record_type):
    state = MemoryStore()
    task, run = records()
    principal = Principal(
        principal_id="principal-test",
        issuer="test.invalid",
        subject="test",
        type=PrincipalType.SERVICE,
    )
    membership = WorkspaceMembership(
        membership_id="membership-test",
        workspace_id="workspace-test",
        principal_id=principal.principal_id,
        role=WorkspaceRole.SERVICE,
    )
    capability = Capability(capability_id="test-capability", version="1.0.0", backend="NATIVE")
    policy = CapabilityPolicy(policy_id="test-policy", version="1.0.0")
    if record_type == "run":
        original, field, changed, put = run, "state", RunState.CANCELLED, state.create_run

        async def get():
            return await state.get_run(run.run_id)
    elif record_type == "task":
        original, field, changed, put = task.envelope, "objective", "mutated", None
        await state.create_task(task)
        original.objective = changed
        assert (await state.get_task(task.envelope.task_id)).envelope.objective != changed
        borrowed = await state.get_task(task.envelope.task_id)
        borrowed.envelope.objective = changed
        assert (await state.get_task(task.envelope.task_id)).envelope.objective != changed
        return
    elif record_type == "principal":
        original, field, changed, put = (
            principal,
            "status",
            PrincipalStatus.DISABLED,
            state.upsert_principal,
        )

        async def get():
            return await state.get_principal(principal.principal_id)
    elif record_type == "membership":
        original, field, changed, put = (
            membership,
            "role",
            WorkspaceRole.VIEWER,
            state.upsert_workspace_membership,
        )

        async def get():
            return (await state.list_workspace_memberships())[0]
    elif record_type == "capability":
        original, field, changed, put = capability, "enabled", False, state.upsert_capability

        async def get():
            return await state.get_capability(capability.capability_id, capability.version)
    else:
        original, field, changed, put = (
            policy,
            "explicitly_denied",
            ["test-capability"],
            state.upsert_capability_policy,
        )

        async def get():
            return await state.get_capability_policy(policy.policy_id, policy.version)

    stored = await put(original)
    setattr(original, field, changed)
    setattr(stored, field, changed)
    assert getattr(await get(), field) != changed
    borrowed = await get()
    setattr(borrowed, field, changed)
    assert getattr(await get(), field) != changed
    if record_type in {"capability", "policy"}:
        with pytest.raises(ValueError, match="immutable"):
            await put(borrowed)


async def test_held_current_identity_read_survives_external_model_mutation_and_serializes_update():
    state = MemoryStore()
    person = Principal(principal_id="principal-test", issuer="test.invalid", subject="test")
    returned = await state.upsert_principal(person)
    borrowed = await state.get_principal(person.principal_id)
    async with RuntimeStore(state).transaction("test-project"):
        returned.status = borrowed.status = person.status = PrincipalStatus.DISABLED
        assert state.principals[person.principal_id].status is PrincipalStatus.ACTIVE
        disable = asyncio.create_task(state.upsert_principal(borrowed))
        await asyncio.sleep(0.02)
        assert not disable.done()
    await asyncio.wait_for(disable, 2)
    assert (await state.get_principal(person.principal_id)).status is PrincipalStatus.DISABLED


async def test_routing_commit_and_runtime_rollback_have_one_consistent_lock_order():
    state = MemoryStore()
    task, run = records()
    await state.create_task(task)
    await state.create_run(run)
    entered, release = asyncio.Event(), asyncio.Event()

    async def routing():
        async with state.routing_transaction(run.run_id) as scoped:
            assert await scoped.get_run(run.run_id) == run
            entered.set()
            await release.wait()

    route = asyncio.create_task(routing())
    await entered.wait()

    async def authority():
        async with RuntimeStore(state).transaction(run.project_id):
            assert await state.get_run(run.run_id) == run

    other = asyncio.create_task(authority())
    await asyncio.sleep(0.02)
    assert not other.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(route, other), 2)


@pytest.mark.parametrize("outcome", ["commit", "rollback", "cancelled"])
async def test_runtime_handle_closes_on_every_exit_and_cannot_register_late_guards(outcome):
    state = MemoryStore()
    store = RuntimeStore(state)
    now = datetime.now(UTC)
    task, run = records()
    escaped = None
    try:
        async with store.transaction(run.project_id) as tx:
            escaped = tx
            assert await tx.now()
            if outcome == "rollback":
                raise ValueError("construction rollback")
            if outcome == "cancelled":
                raise asyncio.CancelledError
    except (ValueError, asyncio.CancelledError):
        pass
    assert escaped is not None
    checks = [
        lambda: escaped.require_active(),
        lambda: escaped.require_valid_interval(now, now + timedelta(seconds=1), Code.SAFETY_DENIED),
        lambda: escaped.authority_valid_until,
        lambda: escaped.registry,
    ]
    for call in checks:
        with pytest.raises(RoboticsError) as error:
            call()
        assert error.value.code is Code.SIMULATION_UNAVAILABLE
    from accretion.robotics.runtime_store import RuntimeResource

    resource = RuntimeResource(
        id="resource-test",
        workspace_id="workspace-test",
        project_id=run.project_id,
        host_principal_id="host-test",
        host_instance_id="instance-test",
    )
    async_checks = [
        escaped.now(),
        escaped.validate_time_guards(),
        escaped.capability("test", "1.0.0"),
        escaped.capability_policy("test", "1.0.0"),
        escaped.get(RuntimeResource, resource.id),
        escaped.list_rows(
            RuntimeResource, workspace_id=resource.workspace_id, project_id=run.project_id
        ),
        escaped.put(resource),
        escaped.lock_resource(resource.id),
        escaped.create_task_run(task, run),
        escaped.run_task(run.run_id),
        escaped.require_scope(resource.workspace_id, run.project_id),
    ]
    for call in async_checks:
        with pytest.raises(RoboticsError) as error:
            await call
        assert error.value.code is Code.SIMULATION_UNAVAILABLE
    assert not escaped._time_guards
    assert await state.get_run(run.run_id) is None


async def test_open_runtime_handle_is_not_a_child_task_authorization():
    store = RuntimeStore(MemoryStore())
    async with store.transaction("project-test") as tx:

        async def use_inherited_handle():
            with pytest.raises(RoboticsError) as error:
                await tx.now()
            assert error.value.code is Code.SIMULATION_UNAVAILABLE
            with pytest.raises(RoboticsError):
                tx.require_valid_interval(
                    datetime.now(UTC), datetime.now(UTC) + timedelta(seconds=1), Code.SAFETY_DENIED
                )

        await asyncio.wait_for(asyncio.create_task(use_inherited_handle()), 2)
        assert not tx._time_guards
        assert await tx.now()


@pytest.mark.parametrize("provider", ["host", "key", "policy", "conformance"])
async def test_providers_refuse_escaped_and_wrong_scope_runtime_transaction(tmp_path, provider):
    from test_v05_host_inventory import case as inventory_case

    from accretion.robotics.authority_providers import (
        InventoryConformanceAuthority,
        InventoryPolicyAuthority,
    )
    from accretion.robotics.host.inventory import (
        ConfiguredSafetyKeyAuthority,
        HostInventoryAuthority,
    )

    case = await inventory_case.__wrapped__(tmp_path)
    operation = {
        "host": HostInventoryAuthority([case.host]).resolve,
        "key": ConfiguredSafetyKeyAuthority([case.key]).keys,
        "policy": InventoryPolicyAuthority().check,
        "conformance": InventoryConformanceAuthority().check,
    }[provider]
    async with case.transaction() as tx:
        escaped = tx
    with pytest.raises(RoboticsError) as error:
        await operation(escaped, case.check)
    assert error.value.code is Code.SIMULATION_UNAVAILABLE
    async with case.store.transaction("other-project") as wrong:
        with pytest.raises(RoboticsError) as error:
            await operation(wrong, case.check)
        assert error.value.code is Code.RESOURCE_NOT_FOUND
        assert not wrong._time_guards
    async with case.transaction() as tx:
        other_workspace = case.check.model_copy(
            update={
                "episode": case.check.episode.model_copy(update={"workspace_id": "other-workspace"})
            },
            deep=True,
        )
        with pytest.raises(RoboticsError) as error:
            await operation(tx, other_workspace)
        assert error.value.code is Code.RESOURCE_NOT_FOUND
        assert not tx._time_guards


async def test_registry_authorize_returns_independent_identity(tmp_path):
    from test_v05_host_inventory import case as inventory_case

    case = await inventory_case.__wrapped__(tmp_path)
    async with case.transaction() as tx:
        human = case.actors["human"]
        borrowed = await tx.registry.authorize(human.principal_id, case.workspace, case.project)
        borrowed.status = PrincipalStatus.DISABLED
        again = await tx.registry.authorize(human.principal_id, case.workspace, case.project)
        assert again.status is PrincipalStatus.ACTIVE
    assert (await case.state.get_principal(human.principal_id)).status is PrincipalStatus.ACTIVE
