"""Pure host-journal construction against Memory/PG; no actual Docker or approval."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_v05_authority import lab  # noqa: F401

from accretion.contracts import PrincipalStatus
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics.values import LeaseBinding
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.docker import (
    CleanupWitness,
    ContainerWitness,
    CreationAttempt,
    HostLimits,
    LaunchProfile,
)
from accretion.robotics.host.inventory import HostProfileBinding, ModelBundlePins
from accretion.robotics.host_journal import (
    HostCreationJournal,
    HostJournalHooks,
    creation_attempt,
    creation_identity,
)
from accretion.robotics.runtime_store import (
    RuntimeEpisode,
    RuntimeHostCreation,
    RuntimeLease,
    RuntimeResource,
    RuntimeTransaction,
)


class SyntheticWitnesses:
    """Exact-object proof double; test inputs are not real supervisor evidence."""

    created = cleaned = None

    def verify_created(self, attempt, witness):
        if witness is not self.created:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)

    def verify_cleaned(self, attempt, witness):
        if witness is not self.cleaned:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)


@pytest.fixture
async def journal_case(lab, tmp_path):  # noqa: F811
    lease = await lab.acquire()
    dep = lab.setup.dependencies
    profile = LaunchProfile(
        resource_id=lease.resource_id,
        image_id="sha256:" + dep.simulator_image_digest,
        adapter_artifact_digest=dep.adapter_artifact_digest,
        model_bundle_digest="a" * 64,
        worker_kind="UR5E",
        limits=HostLimits(
            cpu_millicores=1000,
            memory_bytes=128 * 1024**2,
            temporary_bytes=1024**2,
            shared_memory_bytes=1024**2,
            pids=16,
            cpu_seconds=5,
            wall_seconds=10,
        ),
    )
    now = datetime.now(UTC)
    host = HostProfileBinding(
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic-host-instance",
        profile=profile,
        dependencies=dep,
        model_bundle=ModelBundlePins(
            bundle_digest=profile.model_bundle_digest,
            robot_model_digest=dep.robot_model_digest,
            world_digest=dep.world_digest,
        ),
        host_compatibility_ref=dict(
            uri="artifact://sha256/" + dep.host_compatibility_profile_hash,
            digest=dep.host_compatibility_profile_hash,
            media_type="application/json",
            size_bytes=100,
            retention_class="RUN",
        ),
        adapter_principal_id=lab.adapter.principal_id,
        evaluator_principal_id=lab.evaluator.principal_id,
        orchestrator_principal_id=lab.orchestrator.principal_id,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=10),
        disposition="ACTIVE",
    )
    verifier = SyntheticWitnesses()
    journal = HostCreationJournal(lab.authority, profiles=[host], verifier=verifier)
    attempt = CreationAttempt(
        "accretion-sim-" + content_hash(lease.id, exclude=())[:32],
        host.host_instance_id,
        profile.image_id,
        canonical_json(profile),
        lease.episode_id,
        canonical_json(LeaseBinding(lease_id=lease.id, generation=lease.generation)),
        tmp_path,
        bootstrap_digest="b" * 64,
    )
    witness = ContainerWitness(
        "c" * 64,
        host.host_instance_id,
        canonical_json(profile),
        content_hash(profile, exclude=()),
        lease.episode_id,
        attempt.lease_bytes,
        tmp_path,
        b"{}",
        b"[]",
        b"[]",
        attempt.name,
        "b" * 64,
    )
    # CIDs must be globally unique in the shared disposable PostgreSQL fixtures.
    witness = replace(witness, container_id=content_hash([lease.id, "test-cid"], exclude=()))
    cleanup = CleanupWitness(
        witness.container_id,
        host.host_instance_id,
        lease.episode_id,
        attempt.lease_bytes,
        True,
        b"[]",
    )
    verifier.created, verifier.cleaned = witness, cleanup
    return SimpleNamespace(
        lab=lab,
        journal=journal,
        attempt=attempt,
        witness=witness,
        cleanup=cleanup,
        host=host,
        verifier=verifier,
        scope=lab.scope(lab.host, key="plan"),
    )


async def begin(c):
    return await c.journal.begin(c.scope, attempt=c.attempt)


async def get(c):
    async with c.lab.authority.store.transaction(c.lab.project) as tx:
        return await tx.get(RuntimeHostCreation, creation_identity(c.lab.lease.id))


async def created(c):
    row = (await begin(c)).record
    return await c.journal.created(
        c.lab.scope(c.lab.host, row.revision, key="created"), attempt_id=row.id, witness=c.witness
    )


async def fence(c):
    return await c.journal.cleanup_started(
        c.lab.scope(c.lab.host, key="fence"), attempt_id=creation_identity(c.lab.lease.id)
    )


async def test_fresh_once_before_create_and_historical_receipt_never_rearms(journal_case):
    c = journal_case
    first = await begin(c)
    assert first.fresh and first.record.status == "PLANNED"
    again = await begin(c)
    assert not again.fresh and again.record == first.record
    with pytest.raises(RoboticsError):
        await c.journal.begin(c.lab.scope(c.lab.host, key="different"), attempt=c.attempt)
    await fence(c)
    assert not (await begin(c)).fresh
    assert (await get(c)).status == "CLEANUP_PENDING"


async def test_same_key_mutation_and_racing_create_refused(journal_case):
    c = journal_case
    results = await asyncio.gather(begin(c), begin(c))
    assert sorted(r.fresh for r in results) == [False, True]
    with pytest.raises(RoboticsError) as error:
        await c.journal.begin(c.scope, attempt=replace(c.attempt, bootstrap_digest="d" * 64))
    assert error.value.code is Code.IDEMPOTENCY_CONFLICT
    with pytest.raises(RoboticsError):
        await c.journal.begin(
            c.lab.scope(c.lab.host, key="replace"),
            attempt=replace(c.attempt, name="accretion-sim-" + "e" * 32),
        )


async def test_exact_observed_cid_then_fenced_cleanup_is_required_for_reuse(journal_case):
    c = journal_case
    row = await created(c)
    assert row.container_id == c.witness.container_id
    with pytest.raises(RoboticsError):
        await c.journal.cleaned(c.lab.scope(c.lab.host), attempt_id=row.id, witness=c.cleanup)
    await fence(c)
    row = await c.journal.cleaned(
        c.lab.scope(c.lab.host, key="cleaned"), attempt_id=row.id, witness=c.cleanup
    )
    assert row.status == "CLEANED"
    assert [e.kind for e in row.entries] == [
        "PLANNED",
        "CREATED",
        "CLEANUP_STARTED",
        "CLEANUP_CONFIRMED",
    ]
    async with c.lab.authority.store.transaction(c.lab.project) as tx:
        lease = await tx.get(RuntimeLease, c.lab.lease.id)
        resource = await tx.get(RuntimeResource, c.lab.lease.resource_id)
        assert lease.status == "REVOKED" and resource.quarantined
        await c.journal.verify(tx, resource, lease)
    c.lab.authority.cleanup = c.journal
    released = await c.lab.authority.confirm_cleanup(
        c.lab.scope(c.lab.host, resource.revision),
        episode_id=row.episode_id,
        generation=lease.generation,
    )
    assert released.current_lease_id is None and not released.quarantined
    assert (await c.journal.unresolved(c.scope)).records == ()
    assert not (await begin(c)).fresh


async def test_restart_unknown_cid_fences_but_missing_evidence_stays_unresolved(journal_case):
    c = journal_case
    row = (await begin(c)).record
    restarted = HostCreationJournal(c.lab.authority, profiles=[c.host], verifier=c.verifier)
    recovered = await restarted.authorize_recovery(
        c.lab.scope(c.lab.host, key="restart"), attempt_id=row.id
    )
    assert recovered.status == "CLEANUP_PENDING" and recovered.container_id is None
    assert creation_attempt(recovered) == c.attempt
    assert (await restarted.unresolved(c.scope)).records == (recovered,)
    with pytest.raises(RoboticsError):
        await restarted.cleaned(
            c.lab.scope(c.lab.host), attempt_id=row.id, witness=replace(c.cleanup, removed=False)
        )
    # Only the independently issued exact witness may resolve an unknown CID.
    with pytest.raises(RoboticsError):
        await restarted.cleaned(
            c.lab.scope(c.lab.host), attempt_id=row.id, witness=replace(c.cleanup)
        )
    await restarted.cleaned(c.lab.scope(c.lab.host), attempt_id=row.id, witness=c.cleanup)
    with pytest.raises(RoboticsError):
        await restarted.authorize_recovery(
            c.lab.scope(c.lab.host, key="new-restart"), attempt_id=row.id
        )


@pytest.mark.parametrize("change", ["actor", "scope", "host", "profile", "expired", "policy"])
async def test_current_authority_and_configuration_fail_closed(journal_case, change):
    c = journal_case
    scope = c.scope
    attempt = c.attempt
    if change == "actor":
        scope = c.lab.scope(c.lab.adapter)
    elif change == "scope":
        scope = scope.model_copy(update={"workspace_id": "other-workspace"})
    elif change == "host":
        attempt = replace(attempt, host_instance_id="other-host")
    elif change == "profile":
        profile = c.host.profile.model_copy(update={"model_bundle_digest": "f" * 64})
        attempt = replace(attempt, profile_bytes=canonical_json(profile))
    elif change == "expired":
        c.journal = HostCreationJournal(
            c.lab.authority,
            profiles=[
                c.host.model_copy(
                    update={
                        "valid_from": datetime.now(UTC) - timedelta(minutes=2),
                        "valid_until": datetime.now(UTC) - timedelta(minutes=1),
                    }
                )
            ],
            verifier=c.verifier,
        )
    else:
        c.lab.authority.policy = None
    with pytest.raises(RoboticsError):
        await c.journal.begin(scope, attempt=attempt)
    assert await get(c) is None


async def test_disabled_initiator_blocks_create_but_cannot_prevent_owned_cleanup(journal_case):
    c = journal_case
    row = (await begin(c)).record
    await c.lab.state.upsert_principal(
        c.lab.human.model_copy(update={"status": PrincipalStatus.DISABLED})
    )
    with pytest.raises(RoboticsError):
        await c.journal.created(
            c.lab.scope(c.lab.host, row.revision), attempt_id=row.id, witness=c.witness
        )
    await fence(c)
    assert (await get(c)).status == "CLEANUP_PENDING"
    await c.lab.state.upsert_principal(
        c.lab.host.model_copy(update={"status": PrincipalStatus.DISABLED})
    )
    with pytest.raises(RoboticsError):
        await c.journal.unresolved(c.scope)


async def test_untrusted_witness_and_mutated_original_or_history_fail_closed(journal_case):
    c = journal_case
    row = (await begin(c)).record
    for witness in [
        replace(c.witness),
        replace(c.witness, container_id="e" * 64),
        replace(c.witness, bootstrap_digest="f" * 64),
    ]:
        with pytest.raises(RoboticsError):
            await c.journal.created(
                c.lab.scope(c.lab.host, row.revision), attempt_id=row.id, witness=witness
            )
    assert (await get(c)) == row
    async with c.lab.authority.store.transaction(c.lab.project) as tx:
        with pytest.raises((RoboticsError, ValueError)):
            await tx.put(row.model_copy(update={"bootstrap_digest": "e" * 64}))
        with pytest.raises((RoboticsError, ValueError)):
            await tx.put(row.model_copy(update={"created_by": c.lab.adapter.principal_id}))
    await fence(c)
    async with c.lab.authority.store.transaction(c.lab.project) as tx:
        with pytest.raises(RoboticsError):
            await tx.put(row)


async def test_hooks_do_not_escape_transaction_or_replay_create(journal_case):
    c = journal_case
    hooks = HostJournalHooks(c.journal, c.scope)
    assert await asyncio.create_task(hooks.planned(c.attempt)) is True
    assert await hooks.planned(c.attempt) is False
    await asyncio.create_task(hooks.created(c.attempt, c.witness))
    attempt = replace(c.attempt, container_id=c.witness.container_id)
    assert await asyncio.create_task(hooks.starting(attempt, c.witness)) > datetime.now(UTC)
    await asyncio.create_task(hooks.cleanup_started(c.attempt))
    with pytest.raises(RoboticsError):
        await asyncio.create_task(hooks.starting(attempt, c.witness))
    await asyncio.create_task(hooks.cleaned(c.attempt, c.cleanup))


async def test_start_requires_current_created_row_and_returns_earliest_cap(journal_case):
    c = journal_case
    row = (await begin(c)).record
    attempt = replace(c.attempt, container_id=c.witness.container_id)
    hooks = HostJournalHooks(c.journal, c.scope)
    with pytest.raises(RoboticsError):
        await hooks.starting(attempt, c.witness)
    row = await created(c)
    cap = datetime.now(UTC) + timedelta(seconds=30)
    old = c.lab.trust.check

    async def current_policy(tx, check):
        await old(tx, check)
        tx.require_valid_interval(cap - timedelta(minutes=1), cap, Code.CAPABILITY_DENIED)

    c.lab.trust.check = current_policy
    assert await hooks.starting(attempt, c.witness) == min(cap, row.creation_valid_until)
    assert (await get(c)) == row  # A freshness read is not a lifecycle/approval claim.


@pytest.mark.parametrize(
    "change",
    [
        "copied_witness",
        "cid",
        "attempt_name",
        "bootstrap",
        "scope",
        "actor",
        "human_disabled",
        "host_disabled",
        "policy",
        "conformance",
        "host",
        "revoked",
        "quarantined",
        "generation",
        "episode_running",
        "expired_profile",
    ],
)
async def test_start_rechecks_current_identity_fence_configuration_and_proof(journal_case, change):
    c = journal_case
    row = await created(c)
    attempt = creation_attempt(row)
    witness = c.witness
    scope = c.lab.scope(c.lab.host, row.revision)
    if change == "copied_witness":
        witness = replace(witness)
    elif change == "cid":
        witness = replace(witness, container_id="f" * 64)
    elif change == "attempt_name":
        attempt = replace(attempt, name="accretion-sim-" + "e" * 32)
    elif change == "bootstrap":
        attempt = replace(attempt, bootstrap_digest="f" * 64)
    elif change == "scope":
        scope = scope.model_copy(update={"workspace_id": "different-workspace"})
    elif change == "actor":
        scope = c.lab.scope(c.lab.adapter, row.revision)
    elif change in {"human_disabled", "host_disabled"}:
        principal = c.lab.human if change == "human_disabled" else c.lab.host
        await c.lab.state.upsert_principal(
            principal.model_copy(update={"status": PrincipalStatus.DISABLED})
        )
    elif change in {"policy", "conformance", "host"}:
        setattr(c.lab.authority, change, None)
    elif change == "expired_profile":
        c.journal = HostCreationJournal(
            c.lab.authority,
            profiles=[
                c.host.model_copy(
                    update={
                        "valid_from": datetime.now(UTC) - timedelta(minutes=2),
                        "valid_until": datetime.now(UTC) - timedelta(minutes=1),
                    }
                )
            ],
            verifier=c.verifier,
        )
    else:
        # Synthetic durable state changes isolate each current fence predicate.
        model = (
            RuntimeLease
            if change == "revoked"
            else RuntimeEpisode
            if change == "episode_running"
            else RuntimeResource
        )
        identity = (
            c.lab.lease.id
            if model is RuntimeLease
            else row.episode_id
            if model is RuntimeEpisode
            else row.resource_id
        )
        updates = {
            "revoked": {"status": "REVOKED"},
            "quarantined": {"quarantined": True},
            "generation": {"generation": c.lab.lease.generation + 1},
            "episode_running": {"status": "RUNNING"},
        }[change]
        async with c.lab.authority.store.transaction(c.lab.project) as tx:
            old = await tx.get(model, identity)
            await tx.put(old.model_copy(update={**updates, "revision": old.revision + 1}))
    with pytest.raises(RoboticsError):
        await c.journal.starting(scope, attempt=attempt, witness=witness)
    assert (await get(c)) == row


async def test_historical_created_receipt_after_cleanup_cannot_authorize_start(journal_case):
    c = journal_case
    original = await created(c)
    await fence(c)
    previous = await c.journal.created(
        c.lab.scope(c.lab.host, 1, key="created"), attempt_id=original.id, witness=c.witness
    )
    assert previous == original
    with pytest.raises(RoboticsError):
        await HostJournalHooks(c.journal, c.scope).starting(creation_attempt(previous), c.witness)
    assert (await get(c)).status == "CLEANUP_PENDING"


async def test_start_final_commit_expiry_never_publishes_deadline(journal_case, monkeypatch):
    c = journal_case
    row = await created(c)
    original_validate = RuntimeTransaction.validate_time_guards
    original_now = RuntimeTransaction.now
    late = False

    async def now(tx):
        actual = await original_now(tx)  # PostgreSQL still performs its real clock query.
        return row.creation_valid_until if late else actual

    async def validate(tx):
        nonlocal late
        late = True
        await original_validate(tx)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeTransaction, "now", now)
        patch.setattr(RuntimeTransaction, "validate_time_guards", validate)
        with pytest.raises(RoboticsError) as error:
            await HostJournalHooks(c.journal, c.scope).starting(creation_attempt(row), c.witness)
        assert error.value.code is Code.LEASE_INVALID
    assert late and (await get(c)) == row


async def test_missing_verifier_refuses_before_any_creation_plan(journal_case):
    c = journal_case
    journal = HostCreationJournal(c.lab.authority, profiles=[c.host])
    with pytest.raises(RoboticsError) as error:
        await journal.begin(c.scope, attempt=c.attempt)
    assert error.value.code is Code.ISOLATION_UNAVAILABLE
    assert await get(c) is None


async def test_cleanup_exact_retry_never_reopens_or_drops_prior_history(journal_case):
    c = journal_case
    row = await created(c)
    pending = await fence(c)
    clean_scope = c.lab.scope(c.lab.host, key="cleanup-publication")
    clean = await c.journal.cleaned(clean_scope, attempt_id=row.id, witness=c.cleanup)
    assert await c.journal.cleaned(clean_scope, attempt_id=row.id, witness=c.cleanup) == clean
    assert await fence(c) == pending  # Historical receipt only.
    assert (await get(c)) == clean
    with pytest.raises(RoboticsError):
        await c.journal.cleaned(
            clean_scope, attempt_id=row.id, witness=replace(c.cleanup, container_id="e" * 64)
        )
    with pytest.raises(RoboticsError):
        await c.journal.authorize_recovery(
            c.lab.scope(c.lab.host, key="restart-after-clean"), attempt_id=row.id
        )


async def test_current_config_late_expiry_rolls_back_plan(journal_case):
    c = journal_case
    # A real clock read after collaborators must invalidate the transaction.
    old = c.journal._configuration

    async def expired(tx, row, ep, lease, resource):
        await old(tx, row, ep, lease, resource)
        now = await tx.now()
        tx.require_valid_interval(now - timedelta(seconds=1), now, Code.ISOLATION_UNAVAILABLE)

    c.journal._configuration = expired
    with pytest.raises(RoboticsError):
        await begin(c)
    assert await get(c) is None


async def test_empty_filtered_page_retains_cursor_for_later_unresolved_rows(journal_case):
    c = journal_case
    row = await created(c)
    await fence(c)
    await c.journal.cleaned(c.lab.scope(c.lab.host), attempt_id=row.id, witness=c.cleanup)
    page = await c.journal.unresolved(c.scope, limit=1)
    assert page.records == () and page.next_after == row.id
    final = await c.journal.unresolved(c.scope, after=page.next_after, limit=1)
    assert final.records == () and final.next_after is None


async def test_corrupt_index_never_supplies_a_different_cleanup_identity(journal_case):
    from sqlalchemy import update

    from accretion.persistence.models import SimulationHostCreationRow
    from accretion.persistence.store import MemoryStore

    c = journal_case
    row = (await begin(c)).record
    changed = "accretion-sim-" + "d" * 32

    async def write(name):
        if isinstance(c.lab.state, MemoryStore):
            async with c.lab.state.robotics_registry_lock:
                c.lab.state.robotics_runtime_state["RuntimeHostCreation"][row.id]["docker_name"] = (
                    name
                )
        else:
            async with c.lab.state.sessions.begin() as session:
                await session.execute(
                    update(SimulationHostCreationRow)
                    .where(SimulationHostCreationRow.id == row.id)
                    .values(docker_name=name)
                )

    await write(changed)
    try:
        with pytest.raises(RoboticsError) as error:
            await c.journal.unresolved(c.scope)
        assert error.value.code is Code.INVALID_CONTRACT
        with pytest.raises(RoboticsError):
            await c.journal.authorize_recovery(c.lab.scope(c.lab.host), attempt_id=row.id)
    finally:
        await write(row.docker_name)
