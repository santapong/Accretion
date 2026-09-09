"""Deployment pin/transaction construction; no host, network, signing or DB server."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from v05_sdk_fixtures import editable, fixture

from accretion.contracts import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    Project,
    Provider,
    RiskLevel,
    Run,
    Task,
    TaskEnvelope,
    TaskType,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics import EmbodimentDescriptor
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.robotics.authority import AuthorityCheck, CurrentAuthority, SafetyKeyAuthority
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.docker import HostLimits, LaunchProfile
from accretion.robotics.host.inventory import (
    ConfiguredSafetyKeyAuthority,
    HostInventoryAuthority,
    HostProfileBinding,
    ModelBundlePins,
    SafetyPublicKeyBinding,
)
from accretion.robotics.runtime_store import (
    EpisodeSetup,
    RuntimeBinding,
    RuntimeEpisode,
    RuntimeLease,
    RuntimeResource,
    RuntimeStore,
)

PUBLIC_KEY = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
SECOND_PUBLIC_KEY = "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"


@pytest.fixture
async def case(tmp_path):
    state = MemoryStore()
    now = datetime.now(UTC)
    clock = {"now": now}
    store = RuntimeStore(state, clock=lambda: clock["now"])
    workspace, project = new_id("workspace_entity"), new_id("project")
    await state.upsert_workspace(WorkspaceEntity(workspace_id=workspace, name="Inventory fixture"))
    await state.create_project(
        Project(project_id=project, name="Inventory fixture", repository_path=tmp_path)
    )
    await store.registry.bootstrap_bind_project(workspace_id=workspace, project_id=project)
    actors = {}
    for name in ("human", "orchestrator", "host", "adapter", "evaluator"):
        person = Principal(
            principal_id=new_id("principal"),
            issuer="inventory-test.invalid",
            subject=name,
            type=PrincipalType.HUMAN if name == "human" else PrincipalType.SERVICE,
        )
        await state.upsert_principal(person)
        await state.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=workspace,
                principal_id=person.principal_id,
                role=WorkspaceRole.OWNER if name == "human" else WorkspaceRole.SERVICE,
            )
        )
        actors[name] = person
    descriptor = EmbodimentDescriptor.model_validate(
        {
            **editable("EmbodimentDescriptor"),
            "workspace_id": workspace,
            "project_id": project,
        }
    )
    pins = fixture("SimulationEpisodeApproval")["pins"]
    setup = EpisodeSetup(
        **{
            key: value
            for key, value in pins.items()
            if key not in {"episode_id", "lease", "preflight_receipt_hash"}
        },
        dependencies=fixture("AdapterConformanceReport")["dependencies"],
        descriptor_original=canonical_json(descriptor).decode(),
        adapter_principal_id=actors["adapter"].principal_id,
        evaluator_principal_id=actors["evaluator"].principal_id,
        evaluator=fixture("SafetyDecisionReceipt")["decision"]["evaluator"],
        policy_ref=fixture("SimulationEpisodeApproval")["policy_ref"],
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project,
            objective="Inventory construction only",
            task_type=TaskType.EXPERIMENT,
            risk_level=RiskLevel.HIGH,
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project,
        provider=Provider.DETERMINISTIC,
        state="PENDING",
        principal_id=actors["human"].principal_id,
    )
    binding = RuntimeBinding(
        id=new_id("simulation_run_binding"),
        workspace_id=workspace,
        project_id=project,
        run_id=run.run_id,
        episode_id=pins["episode_id"],
        contract_id=new_id("simulation_run_binding"),
        initiator_id=actors["human"].principal_id,
        orchestrator_id=actors["orchestrator"].principal_id,
    )
    episode = RuntimeEpisode(
        id=pins["episode_id"],
        workspace_id=workspace,
        project_id=project,
        run_id=run.run_id,
        binding_id=binding.id,
        setup=setup,
    )
    profile = LaunchProfile(
        resource_id="configured-test-resource",
        image_id="sha256:" + setup.dependencies.simulator_image_digest,
        adapter_artifact_digest=setup.dependencies.adapter_artifact_digest,
        model_bundle_digest="a" * 64,
        worker_kind="UR5E",
        limits=HostLimits(
            cpu_millicores=500,
            memory_bytes=128 * 1024**2,
            temporary_bytes=1024**2,
            shared_memory_bytes=1024**2,
            pids=16,
            cpu_seconds=5,
            wall_seconds=10,
        ),
    )
    host = HostProfileBinding(
        workspace_id=workspace,
        project_id=project,
        host_principal_id=actors["host"].principal_id,
        host_instance_id="configured-instance",
        profile=profile,
        dependencies=setup.dependencies,
        model_bundle=ModelBundlePins(
            bundle_digest=profile.model_bundle_digest,
            robot_model_digest=setup.dependencies.robot_model_digest,
            world_digest=setup.dependencies.world_digest,
        ),
        host_compatibility_ref=dict(
            uri="artifact://sha256/" + setup.dependencies.host_compatibility_profile_hash,
            digest=setup.dependencies.host_compatibility_profile_hash,
            media_type="application/json",
            size_bytes=100,
            retention_class="RUN",
        ),
        adapter_principal_id=actors["adapter"].principal_id,
        evaluator_principal_id=actors["evaluator"].principal_id,
        orchestrator_principal_id=actors["orchestrator"].principal_id,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=20),
        disposition="ACTIVE",
    )
    key = SafetyPublicKeyBinding(
        workspace_id=workspace,
        project_id=project,
        evaluator_principal_id=actors["evaluator"].principal_id,
        dependencies=setup.dependencies,
        key_id="configured-public-key",
        public_key_hex=PUBLIC_KEY,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=10),
        disposition="ACTIVE",
    )
    async with store.transaction(project) as tx:
        await tx.create_task_run(task, run)
        await tx.put(binding, insert=True)
        await tx.put(episode, insert=True)

    def transaction():
        return store.transaction(project)

    check = AuthorityCheck(
        operation="BIND_RUN", actor_id=actors["orchestrator"].principal_id, now=now, episode=episode
    )
    return SimpleNamespace(
        state=state,
        store=store,
        clock=clock,
        now=now,
        workspace=workspace,
        project=project,
        actors=actors,
        binding=binding,
        episode=episode,
        host=host,
        key=key,
        transaction=transaction,
        check=check,
    )


async def leased(case):
    resource = RuntimeResource(
        id=case.host.profile.resource_id,
        workspace_id=case.workspace,
        project_id=case.project,
        host_principal_id=case.host.host_principal_id,
        host_instance_id=case.host.host_instance_id,
        generation=1,
        current_lease_id=new_id("simulation_lease"),
    )
    lease = RuntimeLease(
        id=resource.current_lease_id,
        contract_id=resource.current_lease_id,
        workspace_id=case.workspace,
        project_id=case.project,
        resource_id=resource.id,
        episode_id=case.episode.id,
        run_id=case.episode.run_id,
        generation=1,
        owner_principal_id=case.host.orchestrator_principal_id,
        endpoint_handle="simh_testonly",
        expires_at=case.now + timedelta(minutes=5),
        heartbeat_deadline=case.now + timedelta(minutes=2),
        last_heartbeat_at=case.now,
        heartbeat_timeout_seconds=120,
    )
    episode = case.episode.model_copy(update={"lease_id": lease.id, "status": "LEASED"})
    async with case.transaction() as tx:
        await tx.put(episode)
        await tx.put(resource, insert=True)
        await tx.put(lease, insert=True)
    return AuthorityCheck(
        operation="HEARTBEAT",
        actor_id=case.host.orchestrator_principal_id,
        now=case.now,
        episode=episode,
        lease=lease,
        resource=resource,
    )


async def test_exact_bind_profile_and_public_keys_use_same_current_transaction(case):
    host: CurrentAuthority = HostInventoryAuthority([case.host])
    keys: SafetyKeyAuthority = ConfiguredSafetyKeyAuthority([case.key])
    async with case.transaction() as tx:
        await host.check(tx, case.check)
        result = await keys.keys(tx, case.check)
        assert result[case.key.key_id].public_key == bytes.fromhex(PUBLIC_KEY)
        assert result[case.key.key_id].principal_id == case.host.evaluator_principal_id
        assert tx.authority_valid_until == case.key.valid_until
        resolved = await host.resolve(tx, case.check)
        assert resolved.profile == case.host.profile
        assert await host.require_profile(tx, case.check, case.host.profile) == case.host


@pytest.mark.parametrize("field", list(fixture("AdapterConformanceReport")["dependencies"]))
async def test_any_full_closure_change_cannot_reuse_host_or_key_inventory(case, field):
    changed = case.episode.model_copy(deep=True)
    setattr(changed.setup.dependencies, field, "0" * 64)
    async with case.transaction() as tx:
        await tx.put(changed)
        check = case.check.model_copy(update={"episode": changed})
        with pytest.raises(RoboticsError):
            await HostInventoryAuthority([case.host]).check(tx, check)
        with pytest.raises(RoboticsError):
            await ConfiguredSafetyKeyAuthority([case.key]).keys(tx, check)


@pytest.mark.parametrize("change", ["image", "adapter", "bundle", "robot", "world", "host", "kind"])
async def test_inconsistent_deployment_sources_fail_at_construction(case, change):
    values = case.host.model_dump(mode="json")
    if change == "image":
        values["profile"]["image_id"] = "sha256:" + "0" * 64
    elif change == "adapter":
        values["profile"]["adapter_artifact_digest"] = "0" * 64
    elif change == "bundle":
        values["profile"]["model_bundle_digest"] = "0" * 64
    elif change in {"world", "robot"}:
        values["model_bundle"][change + ("_model_digest" if change == "robot" else "_digest")] = (
            "0" * 64
        )
    elif change == "host":
        values["host_compatibility_ref"].update(
            digest="0" * 64, uri="artifact://sha256/" + "0" * 64
        )
    else:
        values["profile"]["worker_kind"] = "CONFORMANCE"
    with pytest.raises(ValueError):
        HostProfileBinding.model_validate(values)


@pytest.mark.parametrize("who", ["host", "adapter", "evaluator", "orchestrator"])
@pytest.mark.parametrize("change", ["disabled", "human", "membership"])
async def test_current_real_service_identity_is_mandatory(case, who, change):
    person = case.actors[who]
    if change == "membership":
        case.state.workspace_memberships.pop((case.workspace, person.principal_id))
    else:
        changes = (
            {"status": PrincipalStatus.DISABLED}
            if change == "disabled"
            else {"type": PrincipalType.HUMAN}
        )
        await case.state.upsert_principal(person.model_copy(update=changes))
    async with case.transaction() as tx:
        with pytest.raises(RoboticsError):
            await HostInventoryAuthority([case.host]).check(tx, case.check)
        if who == "evaluator":
            with pytest.raises(RoboticsError):
                await ConfiguredSafetyKeyAuthority([case.key]).keys(tx, case.check)


@pytest.mark.parametrize("provider", ["host", "key"])
@pytest.mark.parametrize("change", ["scope", "revoked", "future", "expired"])
async def test_config_scope_disposition_and_current_time_fail_closed(case, provider, change):
    entry = getattr(case, provider)
    updates = {}
    if change == "scope":
        updates["workspace_id"] = "other-workspace"
    elif change == "revoked":
        updates["disposition"] = "REVOKED"
    elif change == "future":
        updates["valid_from"] = case.now + timedelta(minutes=1)
    else:
        updates["valid_until"] = case.now
    entry = entry.model_copy(update=updates)
    async with case.transaction() as tx:
        with pytest.raises(RoboticsError):
            if provider == "host":
                await HostInventoryAuthority([entry]).check(tx, case.check)
            else:
                await ConfiguredSafetyKeyAuthority([entry]).keys(tx, case.check)


async def test_host_and_key_entries_are_immutable_constructor_snapshots(case):
    host, keys = HostInventoryAuthority([case.host]), ConfiguredSafetyKeyAuthority([case.key])
    case.host.dependencies.controller_digest = "0" * 64
    case.key.dependencies.world_digest = "0" * 64
    async with case.transaction() as tx:
        # Restore the DTO from stored bytes; mutation of test input models must
        # not redefine either configured inventory or the persisted episode.
        current = await tx.get(RuntimeEpisode, case.episode.id)
        check = case.check.model_copy(update={"episode": current})
        resolved = await host.resolve(tx, check)
        resolved.dependencies.controller_digest = "f" * 64
        assert (await host.resolve(tx, check)).dependencies.controller_digest != "f" * 64
        assert await keys.keys(tx, check)


async def test_duplicate_resources_ambiguous_closures_and_key_aliases_are_rejected(case):
    with pytest.raises(ValueError):
        HostInventoryAuthority([case.host, case.host])
    second = case.host.model_copy(
        update={"profile": case.host.profile.model_copy(update={"resource_id": "another-resource"})}
    )
    with pytest.raises(ValueError):
        HostInventoryAuthority([case.host, second])
    with pytest.raises(ValueError):
        ConfiguredSafetyKeyAuthority([case.key, case.key])
    second_key = case.key.model_copy(update={"key_id": "alias-key"})
    with pytest.raises(ValueError):
        ConfiguredSafetyKeyAuthority([case.key, second_key])
    with pytest.raises(ValueError):
        HostInventoryAuthority([])
    with pytest.raises(ValueError):
        ConfiguredSafetyKeyAuthority([])


@pytest.mark.parametrize(
    "field",
    [
        "host_instance_id",
        "host_principal_id",
        "generation",
        "quarantined",
        "scope",
        "owner",
        "lease_status",
        "expiry",
    ],
)
async def test_exact_current_resource_and_lease_are_required(case, field):
    check = await leased(case)
    async with case.transaction() as tx:
        resource, lease = check.resource.model_copy(deep=True), check.lease.model_copy(deep=True)
        if field == "host_instance_id":
            resource = resource.model_copy(update={field: "replacement-instance"})
        elif field == "host_principal_id":
            resource = resource.model_copy(update={field: case.host.orchestrator_principal_id})
        elif field == "generation":
            resource = resource.model_copy(update={field: 2})
        elif field == "quarantined":
            resource = resource.model_copy(update={field: True})
        elif field == "scope":
            resource = resource.model_copy(update={"project_id": "other-project"})
            # Scope is now enforced at storage as well as provider admission.
            with pytest.raises(RoboticsError) as error:
                await tx.put(resource)
            assert error.value.code is Code.RESOURCE_NOT_FOUND
            changed = check.model_copy(update={"resource": resource})
            with pytest.raises(RoboticsError):
                await HostInventoryAuthority([case.host]).check(tx, changed)
            return
        elif field == "owner":
            lease = lease.model_copy(update={"owner_principal_id": case.host.host_principal_id})
        elif field == "lease_status":
            lease = lease.model_copy(update={"status": "REVOKED"})
        else:
            lease = lease.model_copy(update={"expires_at": case.now})
        await tx.put(resource)
        await tx.put(lease)
        changed = check.model_copy(update={"resource": resource, "lease": lease})
        with pytest.raises(RoboticsError):
            await HostInventoryAuthority([case.host]).check(tx, changed)


async def test_active_lease_interval_and_idempotent_acquire_stay_pinned(case):
    check = await leased(case)
    async with case.transaction() as tx:
        provider = HostInventoryAuthority([case.host])
        await provider.check(tx, check)
        assert tx.authority_valid_until == check.lease.heartbeat_deadline
        retry = check.model_copy(update={"operation": "ACQUIRE_LEASE", "lease": None})
        await provider.check(tx, retry)
        with pytest.raises(RoboticsError):
            await provider.check(tx, check.model_copy(update={"resource": None, "lease": None}))


async def test_selected_launch_profile_cannot_change_limits_or_model_bundle(case):
    provider = HostInventoryAuthority([case.host])
    async with case.transaction() as tx:
        for profile in (
            case.host.profile.model_copy(update={"model_bundle_digest": "f" * 64}),
            case.host.profile.model_copy(
                update={"limits": case.host.profile.limits.model_copy(update={"wall_seconds": 11})}
            ),
        ):
            with pytest.raises(RoboticsError):
                await provider.require_profile(tx, case.check, profile)


async def test_project_binding_is_not_inferred_from_inventory(case):
    case.state.robotics_registry_state["bindings"].clear()
    async with case.transaction() as tx:
        with pytest.raises(RoboticsError):
            await HostInventoryAuthority([case.host]).check(tx, case.check)
        with pytest.raises(RoboticsError):
            await ConfiguredSafetyKeyAuthority([case.key]).keys(tx, case.check)
    assert not case.state.robotics_registry_state["bindings"]


async def test_rotation_excludes_revoked_key_and_returns_only_exact_current_keys(case):
    revoked = case.key.model_copy(update={"disposition": "REVOKED"})
    active = case.key.model_copy(
        update={"key_id": "rotated-public-key", "public_key_hex": SECOND_PUBLIC_KEY}
    )
    async with case.transaction() as tx:
        keys = await ConfiguredSafetyKeyAuthority([revoked, active]).keys(tx, case.check)
        assert list(keys) == ["rotated-public-key"]


@pytest.mark.parametrize("provider", ["host", "key"])
async def test_final_transaction_guard_catches_expiry_after_provider_returns(case, provider):
    with pytest.raises(RoboticsError):
        async with case.transaction() as tx:
            if provider == "host":
                await HostInventoryAuthority([case.host]).check(tx, case.check)
                case.clock["now"] = case.host.valid_until
            else:
                await ConfiguredSafetyKeyAuthority([case.key]).keys(tx, case.check)
                case.clock["now"] = case.key.valid_until


async def test_older_runtime_without_interval_seam_is_refused(case):
    async with case.store.transaction(case.project) as tx:
        # Explicitly erase the additive seam even after parent integrates it.
        tx.require_valid_interval = None
        with pytest.raises(RoboticsError) as error:
            await HostInventoryAuthority([case.host]).check(tx, case.check)
        assert error.value.code is Code.SIMULATION_UNAVAILABLE


@pytest.mark.parametrize("provider", ["host", "key"])
@pytest.mark.parametrize("change", ["empty", "naive", "unbounded"])
async def test_deployment_lifetimes_require_finite_aware_bounded_intervals(case, provider, change):
    record = getattr(case, provider)
    values = record.model_dump(mode="python")
    if change == "empty":
        values["valid_until"] = values["valid_from"]
    elif change == "naive":
        values["valid_from"] = values["valid_from"].replace(tzinfo=None)
    else:
        values["valid_until"] = values["valid_from"] + timedelta(hours=24, seconds=1)
    with pytest.raises(ValueError):
        type(record).model_validate(values)


async def test_check_timestamp_never_overrides_authoritative_transaction_clock(case):
    case.clock["now"] = case.host.valid_until
    async with case.transaction() as tx:
        with pytest.raises(RoboticsError):
            await HostInventoryAuthority([case.host]).check(tx, case.check)


async def test_forged_check_cannot_replace_current_persisted_episode(case):
    changed = case.episode.model_copy(deep=True)
    changed.setup.adapter_principal_id = case.actors["host"].principal_id
    async with case.transaction() as tx:
        with pytest.raises(RoboticsError):
            await HostInventoryAuthority([case.host]).check(
                tx, case.check.model_copy(update={"episode": changed})
            )
        with pytest.raises(RoboticsError):
            await ConfiguredSafetyKeyAuthority([case.key]).keys(
                tx, case.check.model_copy(update={"episode": changed})
            )


async def test_public_key_trust_does_not_make_producer_an_independent_evaluator(case):
    changed = case.episode.model_copy(deep=True)
    changed.setup.adapter_principal_id = changed.setup.evaluator_principal_id
    async with case.transaction() as tx:
        await tx.put(changed)
        with pytest.raises(RoboticsError):
            await ConfiguredSafetyKeyAuthority([case.key]).keys(
                tx, case.check.model_copy(update={"episode": changed})
            )


async def test_corrupt_original_resource_row_is_not_replaced_by_check_projection(case):
    check = await leased(case)
    row = case.state.robotics_runtime_state["RuntimeResource"][check.resource.id]
    row["record_hash"] = "0" * 64
    async with case.transaction() as tx:
        with pytest.raises(RoboticsError) as error:
            await HostInventoryAuthority([case.host]).check(tx, check)
        assert error.value.code is Code.INVALID_CONTRACT
