"""Opt-in actual Docker+PostgreSQL construction-authority witness, never robot evidence.

Root must review the finite packet and immutable image before setting the opt-in.
This module never builds/pulls images, creates databases, runs a simulator, or
installs operational grants. The existing Lab supplies explicitly synthetic
policy/conformance/preflight/approval/observation data; production host, channel,
transport, durable lease/reservation and journal code perform the joined test.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v05_authority import lab as lab_fixture
from test_v05_safety import change

from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics.values import LeaseBinding
from accretion.ids import new_id
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.authority_channel import AuthorityGrant, LeaseAuthorityChannel
from accretion.robotics.host.docker import DockerSupervisor, HostLimits, LaunchProfile
from accretion.robotics.host.inventory import HostProfileBinding, ModelBundlePins
from accretion.robotics.host.transport import AdapterTransport
from accretion.robotics.host_journal import (
    HostCreationJournal,
    HostJournalHooks,
    creation_attempt,
    creation_identity,
)
from accretion.robotics.protocol import (
    ObservationResult,
    ProtocolRequest,
    ProtocolResponse,
    ResetRequest,
    SuccessOutcome,
)
from accretion.robotics.runtime_store import (
    DispatchReservation,
    RuntimeHostCreation,
    RuntimeLease,
    RuntimeResource,
)

SCOPE = "NONROBOT_CONSTRUCTION_PROBE"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ACCRETION_RUN_HOST_LEASE_PROBE") != "1",
        reason="reviewed finite actual-host packet not enabled",
    ),
]


class Evidence:
    def __init__(self, directory):
        self.directory = directory
        self.total = 0
        self.events = []

    def write(self, name, value):
        raw = value if isinstance(value, bytes) else canonical_json(evidence_value(value))
        assert len(raw) <= 4 * 1024**2 and self.total + len(raw) <= 128 * 1024**2
        destination = self.directory / name
        assert not destination.exists()
        destination.write_bytes(raw)
        self.total += len(raw)
        self.events.append(dict(name=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))


def evidence_value(value):
    """Lossless binary evidence including malformed/partial non-UTF8 frames."""
    if isinstance(value, bytes):
        return dict(
            format="accretion.probe-bytes.v1",
            base64=base64.b64encode(value).decode("ascii"),
            size_bytes=len(value),
            sha256=hashlib.sha256(value).hexdigest(),
        )
    if isinstance(value, dict):
        return {key: evidence_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [evidence_value(item) for item in value]
    return value


@asynccontextmanager
async def fixture_lab(directory, image_id):
    # Calling the fixture's undecorated generator uses its exact persisted
    # principals and test-trust setup without collecting a second Memory run.
    generator = lab_fixture.__wrapped__(SimpleNamespace(param="postgres"), directory)
    lab = await anext(generator)
    lab.setup = lab.setup.model_copy(
        update={
            "dependencies": lab.setup.dependencies.model_copy(
                update={"simulator_image_digest": image_id.removeprefix("sha256:")}
            )
        }
    )
    original_check = lab.trust.check
    fixture_window = SimpleNamespace(
        valid_from=datetime.now(UTC) - timedelta(seconds=1),
        valid_until=datetime.now(UTC) + timedelta(seconds=60),
    )

    async def fixture_check(tx, check):
        await original_check(tx, check)
        # Explicit SYNTHETIC_TRUST_CONTROL current interval. This proves the
        # real transaction-to-IPC expiry propagation, not a production grant.
        tx.require_valid_interval(
            fixture_window.valid_from, fixture_window.valid_until, Code.CONFORMANCE_STALE
        )

    lab.trust.check = fixture_check
    lab.trust.probe_window = fixture_window
    try:
        yield lab
    finally:
        await generator.aclose()


def another_episode(lab):
    run = lab.run.model_copy(update={"run_id": new_id("run"), "task_id": new_id("task")})
    return replace(
        lab,
        ep=None,
        lease=None,
        approval=None,
        run=run,
        task=lab.task.model_copy(
            update={"envelope": lab.task.envelope.model_copy(update={"task_id": run.task_id})}
        ),
        binding=change(
            lab.binding,
            contract_id=new_id("simulation_run_binding"),
            run_id=run.run_id,
            episode_id=new_id("simulation_episode"),
        ),
    )


async def race(lab, evidence):
    other = another_episode(lab)
    episodes = [await lab.bind(), await other.bind()]
    resource = RuntimeResource(
        id=new_id("principal"),
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic-host-instance",
    )
    await lab.authority.bootstrap_resource(lab.scope(), resource=resource)

    async def acquire(owner, ep):
        return await owner.authority.acquire_lease(
            owner.scope(revision=ep.revision),
            episode_id=ep.id,
            resource_id=resource.id,
            lifetime_seconds=60,
            heartbeat_timeout_seconds=30,
        )

    results = await asyncio.gather(
        *(acquire(owner, ep) for owner, ep in zip((lab, other), episodes, strict=True)),
        return_exceptions=True,
    )
    assert sum(isinstance(item, RuntimeLease) for item in results) == 1
    refused = [item for item in results if isinstance(item, RoboticsError)]
    assert len(refused) == 1 and refused[0].code is Code.LEASE_BUSY
    winner = lab if isinstance(results[0], RuntimeLease) else other
    winner.lease = next(item for item in results if isinstance(item, RuntimeLease))
    loser = other if winner is lab else lab
    evidence.write(
        "race.json",
        dict(winner=winner.lease, loser_episode=loser.binding.episode_id, refusal=refused[0].code),
    )
    return winner


def profile_for(lab, image_id):
    assert lab.lease
    dependencies = lab.setup.dependencies
    profile = LaunchProfile(
        resource_id=lab.lease.resource_id,
        image_id=image_id,
        adapter_artifact_digest=dependencies.adapter_artifact_digest,
        model_bundle_digest="a" * 64,
        worker_kind="UR5E",  # Test-owned enum only; exact image is a labelled nonrobot counter.
        limits=HostLimits(
            cpu_millicores=500,
            memory_bytes=128 * 1024**2,
            temporary_bytes=1024**2,
            shared_memory_bytes=1024**2,
            pids=16,
            cpu_seconds=5,
            wall_seconds=20,
        ),
    )
    now = datetime.now(UTC)
    binding = HostProfileBinding(
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic-host-instance",
        profile=profile,
        dependencies=dependencies,
        model_bundle=ModelBundlePins(
            bundle_digest=profile.model_bundle_digest,
            world_digest=dependencies.world_digest,
            robot_model_digest=dependencies.robot_model_digest,
        ),
        host_compatibility_ref=dict(
            uri="artifact://sha256/" + dependencies.host_compatibility_profile_hash,
            digest=dependencies.host_compatibility_profile_hash,
            media_type="application/json",
            size_bytes=100,
            retention_class="RUN",
        ),
        adapter_principal_id=lab.adapter.principal_id,
        evaluator_principal_id=lab.evaluator.principal_id,
        orchestrator_principal_id=lab.orchestrator.principal_id,
        valid_from=now - timedelta(seconds=1),
        valid_until=now + timedelta(minutes=5),
        disposition="ACTIVE",
    )
    return profile, binding


def supervisor_for(lab, profile, binding):
    supervisor = DockerSupervisor(instance_id=binding.host_instance_id, profiles=[profile])
    journal = HostCreationJournal(lab.authority, profiles=[binding], verifier=supervisor)
    # Trusted fixture composition once, before any prepare. No request can replace this.
    assert supervisor._journal is None and not supervisor._owned
    supervisor._journal = HostJournalHooks(journal, lab.scope(lab.host))
    lab.authority.cleanup = journal
    return supervisor, journal


async def row(lab, model, identity):
    async with lab.authority.store.transaction(lab.project) as tx:
        value = await tx.get(model, identity)
    assert value is not None
    return value


def private_ipc_directory():
    """Use actual POSIX private storage; some evidence mounts normalize modes."""
    directory = Path(tempfile.mkdtemp(prefix="accretion-v05-host-probe-", dir="/tmp"))
    directory.chmod(0o700)
    DockerSupervisor._private_directory(directory)
    return directory


async def journal_observation(lab, identity):
    async with lab.authority.store.transaction(lab.project) as tx:
        record = await tx.get(RuntimeHostCreation, identity)
    return dict(
        status="NOT_CREATED" if record is None else "RECORDED", attempt_id=identity, record=record
    )


async def case(name, output, image_id):
    directory = output / name
    directory.mkdir(mode=0o700)
    evidence = Evidence(directory)
    started = time.monotonic()
    async with fixture_lab(directory, image_id) as lab:
        if name == "ownership":
            lab = await race(lab, evidence)
        else:
            lab.lease_lifetime_seconds = 60
            lab.heartbeat_timeout_seconds = 30
            await lab.acquire()
        assert lab.lease
        lease = lab.lease
        binding_pin = LeaseBinding(lease_id=lease.id, generation=lease.generation)
        profile, binding = profile_for(lab, image_id)
        supervisor, journal = supervisor_for(lab, profile, binding)
        bootstrap = private_ipc_directory()
        evidence.write(
            "ipc-directory.json",
            dict(
                path=bootstrap,
                uid=bootstrap.stat().st_uid,
                mode=oct(bootstrap.stat().st_mode & 0o777),
            ),
        )
        bootstrap_raw = canonical_json(
            dict(
                scope=SCOPE,
                case=name,
                episode_id=lease.episode_id,
                lease=binding_pin,
                authority_expires_at=min(lease.expires_at, lease.heartbeat_deadline),
            )
        )
        (bootstrap / "bootstrap.json").write_bytes(bootstrap_raw)
        (bootstrap / "bootstrap.json").chmod(0o444)
        evidence.write("bootstrap.json", bootstrap_raw)
        evidence.write("fixture-profile.json", binding)
        witness = process = transport = channel = None
        cleanup_proof = None
        request = pins = reservation = None
        admissions, failures, completions = [], [], []
        cleanup_lock = asyncio.Lock()

        async def cleanup():
            nonlocal cleanup_proof
            async with cleanup_lock:
                if cleanup_proof is not None:
                    return
                assert witness
                cleanup_proof = await supervisor.cleanup(witness)
                evidence.write("cleanup.json", asdict(cleanup_proof))

        async def authorize(command, current_pins):
            nonlocal reservation
            if name == "deadline":
                lab.trust.probe_window.valid_until = datetime.now(UTC) + timedelta(seconds=0.75)
            ep = await lab.episode()
            admission = await lab.authority.reserve_dispatch(
                lab.scope(revision=ep.revision),
                episode_id=ep.id,
                request=command,
                pins=current_pins,
            )
            assert admission.fresh and admission.reservation
            reservation = admission.reservation
            admissions.append(reservation.id)
            # Exact expiry metadata comes from the real reservation. This probe
            # has no operational policy grant inventory; test trust is labelled.
            current_lease = await lab.authority.get_lease(lab.scope(), lease.id)
            assert lab.approval
            required_cap = min(
                current_lease.expires_at,
                current_lease.heartbeat_deadline,
                lab.approval.expires_at,
                binding.valid_until,
            )
            assert reservation.authority_valid_until is not None
            assert reservation.authority_valid_until <= required_cap
            deadline = reservation.authority_valid_until
            evidence.write("reservation.json", reservation)
            return AuthorityGrant(reservation_id=reservation.id, valid_until=deadline)

        async def complete(value):
            assert reservation and value.response_bytes
            response = ProtocolResponse.model_validate_json(value.response_bytes)
            await lab.authority.finish_dispatch(
                lab.scope(revision=reservation.revision),
                episode_id=lease.episode_id,
                reservation_id=reservation.id,
                response=response,
                live=lab.live(lab.batch()),
            )
            completions.append(asdict(value))

        async def failed(value, code):
            failures.append(
                dict(
                    code=code,
                    observed_at=datetime.now(UTC),
                    evidence=None if value is None else asdict(value),
                )
            )
            if request is not None:
                await lab.authority.fence_uncertain(
                    lab.scope(),
                    episode_id=lease.episode_id,
                    lease_binding=binding_pin,
                    request_digest=request.request_digest,
                )

        try:
            # Exact profile/endpoint substitutions must be denied before creating
            # an extra container. No global image/tag/name fallback is allowed.
            for field, value in (
                ("resource_id", new_id("principal")),
                ("image_id", "sha256:" + "0" * 64),
                ("adapter_artifact_digest", "0" * 64),
            ):
                with pytest.raises(RoboticsError) as refused:
                    await supervisor.prepare(
                        profile.model_copy(update={field: value}),
                        episode_id=lease.episode_id,
                        lease=binding_pin,
                        bootstrap_directory=bootstrap,
                    )
                assert refused.value.code is Code.PHYSICAL_ENDPOINT_DENIED
            evidence.write("substitutions.json", dict(denied=3, additional_creates=0))
            witness = await supervisor.prepare(
                profile,
                episode_id=lease.episode_id,
                lease=binding_pin,
                bootstrap_directory=bootstrap,
            )
            evidence.write("created.json", asdict(witness))
            journal_row = await row(lab, RuntimeHostCreation, creation_identity(lease.id))
            evidence.write("created-journal.json", journal_row)
            for field, replacement in (
                ("container_id", "0" * 64),
                ("bootstrap_digest", "0" * 64),
                (
                    "profile_bytes",
                    canonical_json(profile.model_copy(update={"image_id": "sha256:" + "0" * 64})),
                ),
            ):
                with pytest.raises(RoboticsError) as refused:
                    await supervisor.start(replace(witness, **{field: replacement}))
                assert refused.value.code is Code.LEASE_INVALID
            inspected, raw_inspection = await supervisor._json(
                ["container", "inspect", witness.container_id]
            )
            assert inspected[0]["State"]["Status"] == "created"
            assert inspected[0]["State"]["Pid"] == 0
            evidence.write("substituted-start-still-created.json", raw_inspection)
            if name == "recovery":
                await lab.authority.end_lease(
                    lab.scope(revision=lease.revision),
                    episode_id=lease.episode_id,
                    generation=lease.generation,
                    disposition="REVOKED",
                )
                with pytest.raises(RoboticsError):
                    await supervisor.start(witness)
                inspected, _ = await supervisor._json(
                    ["container", "inspect", witness.container_id]
                )
                assert inspected[0]["State"]["Status"] == "created"
                assert inspected[0]["State"]["Pid"] == 0
                evidence.write("revoked-before-start.json", inspected)
                supervisor, journal = supervisor_for(lab, profile, binding)
                recovery = creation_attempt(journal_row)
                original_cleaned = supervisor._journal.cleaned
                cleanup_calls = 0

                async def lost_publication(attempt, proof):
                    nonlocal cleanup_calls
                    cleanup_calls += 1
                    if cleanup_calls == 1:
                        # Explicitly test-owned persistence outage after actual
                        # observed removal, never a forged cleanup witness.
                        raise RuntimeError("CONSTRUCTION_CLEANUP_PUBLICATION_OUTAGE")
                    await original_cleaned(attempt, proof)

                supervisor._journal.cleaned = lost_publication
                with pytest.raises(RuntimeError, match="PUBLICATION_OUTAGE"):
                    await supervisor.reconcile(recovery)
                retained = supervisor._cleanup_witnesses[lease.resource_id][1]
                cleanup_proof = await supervisor.reconcile(recovery)
                assert cleanup_calls == 2 and cleanup_proof is retained
                evidence.write(
                    "exact-cleanup-redelivery.json",
                    dict(attempts=cleanup_calls, identical_issued_object=True),
                )
                evidence.write("cleanup.json", asdict(cleanup_proof))
                resource = await row(lab, RuntimeResource, lease.resource_id)
                await lab.authority.confirm_cleanup(
                    lab.scope(lab.host, resource.revision),
                    episode_id=lease.episode_id,
                    generation=lease.generation,
                )
                newer = another_episode(lab)
                new_ep = await newer.bind()
                new_lease = await newer.authority.acquire_lease(
                    newer.scope(revision=new_ep.revision),
                    episode_id=new_ep.id,
                    resource_id=lease.resource_id,
                    lifetime_seconds=60,
                    heartbeat_timeout_seconds=30,
                )
                before = await row(lab, RuntimeResource, lease.resource_id)
                with pytest.raises(RoboticsError):
                    await supervisor.reconcile(recovery)
                assert await row(lab, RuntimeResource, lease.resource_id) == before
                assert await row(lab, RuntimeLease, new_lease.id) == new_lease
                evidence.write(
                    "new-generation-retained.json", dict(resource=before, lease=new_lease)
                )
                # No second container is created for this new-generation check.
                await newer.authority.end_lease(
                    newer.scope(revision=new_lease.revision),
                    episode_id=new_ep.id,
                    generation=new_lease.generation,
                    disposition="REVOKED",
                )
                return

            channel = LeaseAuthorityChannel(bootstrap, authorize, timeout_seconds=2)
            await channel.start()
            process = await supervisor.start(witness)
            assert process.stdin and process.stdout
            ready_raw = await asyncio.wait_for(process.stdout.readline(), 5)
            assert 0 < len(ready_raw) <= 4096 and ready_raw.endswith(b"\n")
            evidence.write("ready-wire.jsonl", ready_raw)
            ready = json.loads(ready_raw)
            assert ready["scope"] == SCOPE and ready["ready"] is True
            evidence.write("ready.json", ready)
            # This is explicitly the fixture preflight/approval, after actual
            # startup, not a manufactured production bootstrap authority.
            ep = await lab.admitted()
            assert lab.approval
            pins = lab.authority.execution_pins(ep, lab.approval)
            request = ProtocolRequest.create(
                request_id="construction-reset",
                sequence=0,
                scope=pins.command_scope(),
                payload=ResetRequest(
                    seed=lab.setup.seed,
                    randomization_sample_hash=lab.setup.randomization_sample_hash,
                ),
            )
            response = ProtocolResponse.create(
                request,
                SuccessOutcome(payload=ObservationResult(op="RESET", observation=lab.batch())),
            )
            setup_raw = (
                canonical_json(
                    dict(
                        scope=SCOPE,
                        pins=pins,
                        request_digest=request.request_digest,
                        response_json=canonical_json(response).decode(),
                    )
                )
                + b"\n"
            )
            assert len(setup_raw) <= 1024 * 1024
            evidence.write("construction-setup-wire.jsonl", setup_raw)
            process.stdin.write(setup_raw)
            await process.stdin.drain()
            transport = AdapterTransport(
                process,
                channel,
                complete=complete,
                failed=failed,
                cleanup=cleanup,
                wall_seconds=12,
                request_seconds=4,
                heartbeat_seconds=2,
                stderr_bytes=4096,
            )
            if name == "ownership":
                assert await transport.request(request, pins) == response
                assert len(admissions) == len(completions) == 1
                # Explicit liveness is absent: the real transport heartbeat
                # deadline fences/quarantines and kills the actual cgroup.
                await asyncio.wait_for(transport._failure, 5)
                assert transport._failure.result() is Code.HEARTBEAT_LOST
            else:
                with pytest.raises(RoboticsError):
                    await transport.request(request, pins)
            await transport.close()
            with pytest.raises(RoboticsError):
                await transport.request(request, pins)
            assert len(admissions) == 1
            assert (await lab.episode()).status == "ABORTED"
            assert (await row(lab, RuntimeResource, lease.resource_id)).quarantined
            assert reservation
            persisted = await row(lab, DispatchReservation, reservation.id)
            assert persisted.status == ("COMPLETED" if name == "ownership" else "UNCERTAIN")
            evidence.write("final-dispatch.json", persisted)
            diagnostics = transport.stderr
            mutations = [
                json.loads(line) for line in diagnostics.splitlines() if b'"MUTATION"' in line
            ]
            assert len(mutations) == (0 if name == "deadline" else 1)
            if name == "lost_ack":
                assert transport.failure_evidence and transport.failure_evidence.response_truncated
                assert transport.failure_evidence.response_bytes == b'{"kind":"response"'
            if name == "deadline":
                final = transport.failure_evidence
                assert final and final.failure is Code.LEASE_INVALID
                assert final.authority_valid_until == reservation.authority_valid_until
                assert failures and failures[0]["observed_at"] >= final.authority_valid_until
                assert diagnostics.count(b'"PERMIT_RECEIVED"') == 1
            if name == "ownership":
                assert diagnostics.count(b'"SUBSTITUTION_DENIED"') == 2
                assert diagnostics.count(b'"DUPLICATE_DENIED"') == 1
        finally:
            primary_error = sys.exc_info()[1]
            cleanup_errors = []

            async def retain_error(operation, awaitable):
                try:
                    return await awaitable
                except BaseException as error:
                    cleanup_errors.append(
                        dict(
                            operation=operation,
                            type=type(error).__name__,
                            code=getattr(error, "code", None),
                        )
                    )
                    return None

            try:
                if transport is not None:
                    await retain_error("transport.close", transport.close())
                elif witness is not None and cleanup_proof is None:
                    await retain_error("supervisor.cleanup", cleanup())
            finally:
                if channel is not None:
                    await retain_error("authority.close", channel.close())
                if process is not None:
                    await retain_error("attach.wait", asyncio.wait_for(process.wait(), 5))
                evidence.write(
                    "transport.json",
                    dict(admissions=admissions, failures=failures, completions=completions),
                )
                if transport is not None:
                    evidence.write("stderr.log", transport.stderr)
                    evidence.write(
                        "final-transport-failure.json",
                        None
                        if transport.failure_evidence is None
                        else asdict(transport.failure_evidence),
                    )
                evidence.write(
                    "final-episode.json", await retain_error("episode.read", lab.episode())
                )
                journal_state = await retain_error(
                    "journal.read", journal_observation(lab, creation_identity(lease.id))
                )
                evidence.write("final-journal.json", journal_state or {"status": "UNAVAILABLE"})
                ipc_removed = False
                if cleanup_proof is not None or (
                    journal_state is not None
                    and journal_state["status"] == "NOT_CREATED"
                    and not supervisor.unresolved_creations
                    and not supervisor._owned
                ):
                    # Never remove a mount needed by a possibly existing host.
                    # A missing row alone is insufficient when create is uncertain.
                    try:
                        DockerSupervisor._private_directory(bootstrap)
                        assert bootstrap.parent == Path("/tmp")
                        assert bootstrap.name.startswith("accretion-v05-host-probe-")
                        shutil.rmtree(bootstrap)
                        ipc_removed = True
                    except Exception as error:
                        cleanup_errors.append(
                            dict(operation="ipc.remove", type=type(error).__name__)
                        )
                evidence.write(
                    "summary.json",
                    dict(
                        scope=SCOPE,
                        case=name,
                        wall_seconds=time.monotonic() - started,
                        cleanup_observed=cleanup_proof is not None,
                        cleanup_errors=cleanup_errors,
                        primary_failure=None
                        if primary_error is None
                        else dict(
                            type=type(primary_error).__name__,
                            code=getattr(primary_error, "code", None),
                        ),
                        ipc_directory=bootstrap,
                        ipc_removed=ipc_removed,
                        manifest=evidence.events,
                    ),
                )
                if primary_error is None:
                    assert not cleanup_errors, (
                        "cleanup incomplete; retained evidence requires review"
                    )


async def test_actual_host_lease_construction_packet():
    image = os.environ["ACCRETION_HOST_LEASE_PROBE_IMAGE_ID"]
    output = Path(os.environ["ACCRETION_HOST_LEASE_PROBE_OUTPUT"])
    assert image.startswith("sha256:") and len(image) == 71
    assert output.is_absolute() and output.is_dir() and not tuple(output.iterdir())
    assert "accretion_v05_host_probe_20260909" in os.environ["ACCRETION_TEST_POSTGRES_URL"]
    # Exactly four serial containers, at most one per case. An earlier failure
    # stops this packet; pytest failures/partial evidence remain in the output.
    async with asyncio.timeout(300):
        for name in ("ownership", "recovery", "lost_ack", "deadline"):
            await case(name, output, image)
