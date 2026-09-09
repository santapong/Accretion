"""Staged bootstrap and watchdog parser construction; no simulator or approval."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from v05_sdk_fixtures import Harness, editable, replace_contract
from v05_sdk_fixtures import fixture as contract_fixture

from accretion.contracts import PrincipalStatus
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import SimulationEpisodeApproval, SimulationPreflightReceipt
from accretion.contracts.robotics.values import EpisodePins
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.watchdog import parse_cpu, validate_namespace
from accretion.robotics.host.worker import (
    DeadlineGuard,
    PlannedEpisodePins,
    WorkerActivation,
    WorkerBootstrap,
    WorkerReady,
    read_frame,
    serve,
)
from accretion.robotics.protocol import (
    DescriptorRecord,
    HeartbeatRequest,
    TerminateRequest,
    WriterRecord,
)
from accretion.robotics.sdk import InitializationPins
from accretion.robotics.testing.fault_adapter import ScriptedFaultAdapter


class SyntheticInitialAdapter(ScriptedFaultAdapter):
    """No host/physics: records one explicit bind after initialization-only setup."""

    bind_calls = 0
    bound_episode = None

    def bind_episode(self, episode):
        if self.bound_episode is not None:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        assert InitializationPins.from_episode(episode).episode_id == self.observe().episode_id
        self.bind_calls += 1
        self.bound_episode = EpisodePins.model_validate_json(canonical_json(episode))


def fixture():
    harness = Harness()
    descriptor = replace_contract(
        harness.descriptor,
        created_by={"principal_id": "test-only-adapter", "status": "ACTIVE"},
    )
    description = harness.description.model_copy(
        update={"descriptor": DescriptorRecord.from_contract(descriptor)}
    )
    adapter = SyntheticInitialAdapter(
        description, harness.initial, harness.make_prepared, harness.artifacts
    )
    bootstrap = WorkerBootstrap(
        worker_kind="UR5E",
        episode=PlannedEpisodePins.from_full(harness.episode),
        description=description,
        orchestrator_principal_id="test-only-orchestrator",
        adapter_principal_id="test-only-adapter",
        evaluator_principal_id=harness.pins.evaluator_principal_id,
        policy_ref=harness.pins.policy_ref,
        evaluator=harness.pins.evaluator,
        conformance_report_hash=contract_fixture("SimulationPreflightReceipt")[
            "conformance_report_hash"
        ],
        public_keys=(
            dict(
                key_id="test-only",
                principal_id=harness.pins.evaluator_principal_id,
                public_key_hex=harness.key.public_key().public_bytes_raw().hex(),
            ),
        ),
        authority_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        wall_seconds=10,
        cpu_seconds=5,
    )
    return harness, adapter, bootstrap


def activation_for(bootstrap, ready, artifacts):
    """Fabricated records created only after actual synthetic ready; no human approval."""
    evidence_ref = artifacts.put(canonical_json(ready), media_type="application/json")
    preflight = SimulationPreflightReceipt.model_validate(
        {
            **editable("SimulationPreflightReceipt"),
            **bootstrap.episode.model_dump(mode="python"),
            "created_at": ready.observed_at,
            "created_by": {"principal_id": bootstrap.orchestrator_principal_id, "status": "ACTIVE"},
            "valid_until": ready.observed_at + timedelta(minutes=2),
            "conformance_report_hash": bootstrap.conformance_report_hash,
            "result": "PASS",
            "checks": [
                dict(
                    check,
                    passed=True,
                    evidence_ref=evidence_ref
                    if check["name"] == "OBSERVATIONS"
                    else check["evidence_ref"],
                )
                for check in editable("SimulationPreflightReceipt")["checks"]
            ],
        }
    )
    episode = EpisodePins.model_validate(
        {
            **bootstrap.episode.model_dump(mode="python"),
            "preflight_receipt_hash": preflight.content_hash,
        }
    )
    approval = SimulationEpisodeApproval.model_validate(
        {
            **editable("SimulationEpisodeApproval"),
            "pins": episode,
            "policy_ref": bootstrap.policy_ref,
            "created_at": ready.observed_at,
            "expires_at": ready.observed_at + timedelta(minutes=1),
        }
    )
    preflight_writer, approval_writer = (
        WriterRecord.from_contract(preflight),
        WriterRecord.from_contract(approval),
    )
    return WorkerActivation(
        ready_hash=content_hash(ready, exclude=()),
        pins=bootstrap.pins(ready.initial_observation, preflight_writer, approval_writer),
        preflight=preflight_writer,
        approval=approval_writer,
    )


class ActivationSource:
    """A supervisor reads the actual ready frame before selecting activation."""

    def __init__(self, output, bootstrap, harness, *, change=None):
        self.output, self.bootstrap, self.harness = output, bootstrap, harness
        self.change = change
        self.initialized = False
        self.rest = io.BytesIO()

    def readline(self, size):
        if not self.initialized:
            self.initialized = True
            ready = WorkerReady.model_validate_json(self.output.getvalue().splitlines()[0])
            assert not self.harness.guard.calls
            activation = activation_for(self.bootstrap, ready, self.harness.artifacts)
            pins = activation.pins
            if self.change is not None:
                return self.change(activation)
            from accretion.robotics.protocol import ProtocolRequest

            self.rest.write(
                canonical_json(
                    ProtocolRequest.create(
                        request_id="terminate-after-explicit-activation",
                        sequence=0,
                        scope=pins.command_scope(),
                        payload=TerminateRequest(),
                    )
                )
                + b"\n"
            )
            self.rest.seek(0)
            return canonical_json(activation) + b"\n"
        return self.rest.readline(size)


def test_fixed_image_selects_ur5e_without_importing_parked_panda(monkeypatch):
    import builtins

    from accretion.robotics.adapters import ur5e
    from accretion.robotics.host import worker

    harness, adapter, bootstrap = fixture()
    profile = ur5e.UR5eProfile(canonical_json(bootstrap.description), None)
    original_import = builtins.__import__

    def admitted_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert not name.endswith(".panda") and "panda" not in (fromlist or ())
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", admitted_import)
    calls = []

    def build(models, artifacts, **kwargs):
        assert models == worker.MODELS_PATH
        assert (
            kwargs["simulator_image_digest"]
            == bootstrap.description.dependencies.simulator_image_digest
        )
        calls.append("build")
        return profile

    def construct(selected, *, episode, **kwargs):
        assert selected is profile
        assert type(episode) is InitializationPins
        assert episode == bootstrap.episode.initialization()
        # The real image factory never constructs a controller without its
        # mandatory current per-request permit check.
        with pytest.raises(RoboticsError):
            kwargs["execution_guard"]()
        calls.append("construct")
        return adapter

    monkeypatch.setattr(ur5e, "build_profile", build)
    monkeypatch.setattr(ur5e, "UR5eAdapter", construct)

    def inspect_serve(values, *, factory, **kwargs):
        assert factory(values) is adapter
        assert adapter.bind_calls == 0 and not harness.guard.calls

    monkeypatch.setattr(worker, "serve", inspect_serve)
    worker.run_worker(bootstrap, io.BytesIO(), io.BytesIO())
    assert calls == ["build", "construct"]


@pytest.mark.parametrize("kind", ["PANDA", "OTHER"])
def test_wave2_worker_refuses_unsupported_kind_before_factory_or_channel(monkeypatch, kind):
    from pydantic import ValidationError

    from accretion.robotics.host import worker

    _, _, bootstrap = fixture()
    payload = bootstrap.model_dump(mode="json")
    payload["worker_kind"] = kind
    with pytest.raises(ValidationError):
        WorkerBootstrap.model_validate(payload)

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported worker must not initialize channels or invoke its factory")

    monkeypatch.setattr(worker, "UnixArtifacts", forbidden)
    monkeypatch.setattr(worker, "UnixAdmissionGuard", forbidden)
    monkeypatch.setattr(worker, "serve", forbidden)
    with pytest.raises(RoboticsError) as error:
        worker.run_worker(
            bootstrap.model_copy(update={"worker_kind": kind}), io.BytesIO(), io.BytesIO()
        )
    assert error.value.code is Code.INVALID_CONTRACT


def test_actual_initial_observation_requires_explicit_exact_activation() -> None:
    harness, adapter, bootstrap = fixture()
    output = io.BytesIO()
    serve(
        bootstrap,
        factory=lambda _: adapter,
        artifacts=harness.artifacts,
        authority=harness.guard,
        source=ActivationSource(output, bootstrap, harness),
        destination=output,
    )
    assert harness.guard.calls == ["TERMINATE"]
    assert adapter.bind_calls == 1
    assert adapter.reset_calls == adapter.execute_calls == 0
    assert adapter.terminated
    lines = output.getvalue().splitlines()
    assert len(lines) == 2 and b'"TERMINATE"' in lines[1]
    ready = WorkerReady.model_validate_json(lines[0])
    assert ready.initial_observation == harness.initial
    assert ready.bootstrap_hash == content_hash(bootstrap, exclude=())


@pytest.mark.parametrize(
    "field", ["approval", "episode", "ready", "budget", "state", "key", "closure", "descriptor"]
)
def test_changed_activation_never_reaches_admission(field: str) -> None:
    harness, adapter, bootstrap = fixture()
    output = io.BytesIO()

    def change(activation):
        values = activation.model_dump(mode="json")
        if field == "approval":
            values["pins"]["approval_hash"] = "0" * 64
        elif field == "episode":
            values["pins"]["episode"]["seed"] += 1
        elif field == "ready":
            values["ready_hash"] = "0" * 64
        elif field == "budget":
            values["pins"]["budget"]["actions"] = 1
        elif field == "state":
            values["pins"]["state"]["observation_digest"] = "0" * 64
        elif field == "closure":
            values["pins"]["dependencies"]["controller_digest"] = "0" * 64
        elif field == "descriptor":
            values["pins"]["descriptor_hash"] = "0" * 64
        else:
            values["pins"]["evaluator_principal_id"] = "untrusted-evaluator"
        return canonical_json(values) + b"\n"

    with pytest.raises((RoboticsError, ValueError)):
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=ActivationSource(output, bootstrap, harness, change=change),
            destination=output,
        )
    assert not harness.guard.calls and adapter.terminated
    assert adapter.reset_calls == adapter.execute_calls == 0


def test_factory_cannot_change_trusted_bootstrap_after_snapshot() -> None:
    harness, adapter, bootstrap = fixture()
    output = io.BytesIO()

    def factory(value):
        value.episode.lease.generation += 1
        return adapter

    serve(
        bootstrap,
        factory=factory,
        artifacts=harness.artifacts,
        authority=harness.guard,
        source=ActivationSource(output, bootstrap, harness),
        destination=output,
    )
    assert harness.guard.calls == ["TERMINATE"] and adapter.terminated


def test_eof_before_activation_terminates_without_authority() -> None:
    harness, adapter, bootstrap = fixture()
    with pytest.raises(RoboticsError):
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=io.BytesIO(),
            destination=io.BytesIO(),
        )
    assert adapter.terminated and not harness.guard.calls


def test_missing_raw_initial_tensor_refuses_ready() -> None:
    harness, adapter, bootstrap = fixture()
    harness.artifacts.blobs.clear()
    output = io.BytesIO()
    with pytest.raises(RoboticsError):
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=io.BytesIO(),
            destination=output,
        )
    assert not output.getvalue() and adapter.terminated


def test_expired_bootstrap_does_not_construct_adapter() -> None:
    harness, _, bootstrap = fixture()
    expired = bootstrap.model_copy(
        update={"authority_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )

    def never(_):
        raise AssertionError("expired bootstrap constructed a worker")

    with pytest.raises(RoboticsError, match="lease"):
        serve(
            expired,
            factory=never,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=io.BytesIO(),
            destination=io.BytesIO(),
        )


def test_permit_delayed_across_deadline_is_uncertain(monkeypatch) -> None:
    harness = Harness()
    now = datetime.now(UTC)
    values = {"clock": now}

    class Clock:
        @staticmethod
        def now(tz):
            return values["clock"]

    class DelayedGuard:
        def authorize(self, request, *, pins):
            values["clock"] += timedelta(seconds=2)

    monkeypatch.setattr("accretion.robotics.host.worker.datetime", Clock)
    guard = DeadlineGuard(DelayedGuard(), now + timedelta(seconds=1))
    with pytest.raises(RoboticsError) as exc:
        guard.authorize(harness.request(HeartbeatRequest()), pins=harness.pins)
    assert exc.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN


@pytest.mark.parametrize("raw", [b"", b"{}", b"x" * (1024 * 1024 + 1) + b"\n"])
def test_control_frame_is_bounded_and_complete(raw: bytes) -> None:
    with pytest.raises(RoboticsError):
        read_frame(io.BytesIO(raw))


@pytest.mark.parametrize(
    "raw",
    [b"", b"usage_usec -1\n", b"user_usec 5\n", b"usage_usec 1\nusage_usec 2\n", b"x " * 5000],
)
def test_invalid_cumulative_cpu_counter_fails_closed(raw: bytes) -> None:
    with pytest.raises(RoboticsError):
        parse_cpu(raw)


def test_cpu_counter_includes_cgroup_total_and_host_execution_is_refused() -> None:
    assert parse_cpu(b"usage_usec 1234\nuser_usec 900\nsystem_usec 334\n") == 1234
    with pytest.raises(RoboticsError):
        validate_namespace()


def ready_fixture():
    harness, adapter, bootstrap = fixture()
    ready = WorkerReady(
        bootstrap_hash=content_hash(bootstrap, exclude=()),
        description=bootstrap.description,
        initial_observation=harness.initial,
        observed_at=datetime.now(UTC),
    )
    return harness, adapter, bootstrap, ready, activation_for(bootstrap, ready, harness.artifacts)


def repackage(activation, *, preflight=None, approval=None):
    old_preflight = activation.preflight.for_execution(SimulationPreflightReceipt)
    old_approval = activation.approval.for_execution(SimulationEpisodeApproval)
    approval = old_approval if approval is None else approval
    if preflight is not None:
        episode = EpisodePins.model_validate(
            {
                name: preflight.content_hash
                if name == "preflight_receipt_hash"
                else getattr(preflight, name)
                for name in EpisodePins.model_fields
            }
        )
        approval = replace_contract(approval, pins=episode)
    else:
        preflight = old_preflight
    return WorkerActivation(
        ready_hash=activation.ready_hash,
        pins=activation.pins.model_copy(
            update={"episode": approval.pins, "approval_hash": approval.content_hash}
        ),
        preflight=WriterRecord.from_contract(preflight),
        approval=WriterRecord.from_contract(approval),
    )


def test_initialization_and_ready_do_not_require_future_approval_records():
    harness, adapter, bootstrap = fixture()
    assert set(bootstrap.episode.initialization().model_dump()) == {
        "episode_id",
        "lease",
        "seed",
        "randomization_sample_hash",
    }
    assert "preflight_receipt_hash" not in bootstrap.episode.model_dump()
    assert "approval_hash" not in bootstrap.model_dump()
    assert "approved_by" not in canonical_json(bootstrap).decode()
    output = io.BytesIO()
    with pytest.raises(RoboticsError):
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=io.BytesIO(),
            destination=output,
        )
    ready = WorkerReady.model_validate_json(output.getvalue().splitlines()[0])
    assert ready.initial_observation == harness.initial
    assert adapter.bind_calls == 0 and not harness.guard.calls
    # Existing direct full-pins development construction remains unchanged.
    assert harness.session.pins.episode == harness.episode


@pytest.mark.parametrize("field", ["approval_hash", "preflight_receipt_hash"])
def test_bootstrap_refuses_future_authority_shortcut_fields(field):
    _, _, bootstrap = fixture()
    data = bootstrap.model_dump(mode="json")
    if field == "approval_hash":
        data[field] = "0" * 64
    else:
        data["episode"][field] = "0" * 64
    with pytest.raises(ValueError):
        WorkerBootstrap.model_validate(data)


@pytest.mark.parametrize("field", ["preflight", "approval"])
@pytest.mark.parametrize(
    "change", ["missing_record", "missing_seal", "changed_original", "future_version"]
)
def test_activation_requires_supported_original_sealed_records(field, change):
    import json

    _, _, _, _, activation = ready_fixture()
    values = activation.model_dump(mode="json")
    if change == "missing_record":
        values.pop(field)
    else:
        original = json.loads(values[field]["original_json"])
        if change == "missing_seal":
            original.pop("content_hash")
        elif change == "changed_original":
            original["created_by"]["principal_id"] = "different-original-writer"
        else:
            original["schema_version"] = "1.1.0"
            original["content_hash"] = content_hash(original)
        values[field]["original_json"] = canonical_json(original).decode()
    with pytest.raises((RoboticsError, ValueError)):
        WorkerActivation.model_validate(values)


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "creator",
        "disabled",
        "conformance",
        "seed",
        "before_ready",
        "future",
        "lease_deadline",
        "rejected",
    ],
)
def test_resealed_preflight_still_requires_real_ready_plan_scope_identity_and_time(change):
    from accretion.robotics.host.worker import validate_activation

    _, _, bootstrap, ready, activation = ready_fixture()
    preflight = activation.preflight.for_execution(SimulationPreflightReceipt)
    edits = {}
    if change == "scope":
        edits["project_id"] = "other-project"
    elif change == "creator":
        edits["created_by"] = preflight.created_by.model_copy(
            update={"principal_id": "other-orchestrator"}
        )
    elif change == "disabled":
        edits["created_by"] = preflight.created_by.model_copy(
            update={"status": PrincipalStatus.DISABLED}
        )
    elif change == "conformance":
        edits["conformance_report_hash"] = "0" * 64
    elif change == "seed":
        edits["seed"] = preflight.seed + 1
    elif change == "before_ready":
        edits["created_at"] = ready.observed_at - timedelta(seconds=1)
    elif change == "future":
        edits["created_at"] = ready.observed_at + timedelta(seconds=1)
    elif change == "lease_deadline":
        edits["valid_until"] = bootstrap.authority_expires_at + timedelta(seconds=1)
    else:
        edits.update(
            result="REJECTED",
            checks=[check.model_copy(update={"passed": False}) for check in preflight.checks],
        )
    changed = repackage(activation, preflight=replace_contract(preflight, **edits))
    with pytest.raises(RoboticsError):
        validate_activation(bootstrap, ready, changed, now=ready.observed_at)


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "disabled",
        "service_actor",
        "policy",
        "before_preflight",
        "future",
        "preflight_deadline",
        "episode_pin",
    ],
)
def test_resealed_approval_does_not_override_exact_human_plan_policy_and_time(change):
    from accretion.robotics.host.worker import validate_activation

    _, _, bootstrap, ready, activation = ready_fixture()
    approval = activation.approval.for_execution(SimulationEpisodeApproval)
    edits = {}
    if change == "scope":
        edits["workspace_id"] = "other-workspace"
    elif change in {"disabled", "service_actor"}:
        actor = approval.approved_by.model_copy(
            update={
                "status": PrincipalStatus.DISABLED
                if change == "disabled"
                else PrincipalStatus.ACTIVE,
                "principal_id": bootstrap.orchestrator_principal_id
                if change == "service_actor"
                else approval.approved_by.principal_id,
            }
        )
        edits.update(approved_by=actor, created_by=actor)
    elif change == "policy":
        edits["policy_ref"] = approval.policy_ref.model_copy(update={"content_digest": "0" * 64})
    elif change == "before_preflight":
        edits["created_at"] = ready.observed_at - timedelta(seconds=1)
    elif change == "future":
        edits["created_at"] = ready.observed_at + timedelta(seconds=1)
    elif change == "preflight_deadline":
        edits["expires_at"] = activation.preflight.for_execution(
            SimulationPreflightReceipt
        ).valid_until + timedelta(seconds=1)
    else:
        edits["pins"] = approval.pins.model_copy(update={"environment_snapshot_hash": "0" * 64})
    changed = repackage(activation, approval=replace_contract(approval, **edits))
    with pytest.raises(RoboticsError):
        validate_activation(bootstrap, ready, changed, now=ready.observed_at)


@pytest.mark.parametrize("change", ["digest", "length", "media", "class"])
def test_preflight_observations_must_reference_exact_ready_bytes(change):
    from accretion.robotics.host.worker import validate_activation

    _, _, bootstrap, ready, activation = ready_fixture()
    preflight = activation.preflight.for_execution(SimulationPreflightReceipt)
    checks = []
    for check in preflight.checks:
        if check.name == "OBSERVATIONS":
            ref = check.evidence_ref.model_dump(mode="python")
            if change == "digest":
                ref.update(digest="0" * 64, uri="artifact://sha256/" + "0" * 64)
            elif change == "length":
                ref["size_bytes"] += 1
            elif change == "media":
                ref["media_type"] = "text/plain"
            else:
                ref["evidence_class"] = "DIGITAL"
            check = check.model_copy(
                update={"evidence_ref": type(check.evidence_ref).model_validate(ref)}
            )
        checks.append(check)
    changed = repackage(activation, preflight=replace_contract(preflight, checks=checks))
    with pytest.raises(RoboticsError) as error:
        validate_activation(bootstrap, ready, changed, now=ready.observed_at)
    assert error.value.code is Code.PREFLIGHT_FAILED


def test_deadline_uses_earliest_approval_preflight_lease_limit():
    from accretion.robotics.host.worker import activation_deadline, validate_activation

    _, _, bootstrap, ready, activation = ready_fixture()
    pins = validate_activation(bootstrap, ready, activation, now=ready.observed_at)
    assert pins == activation.pins
    assert (
        activation_deadline(bootstrap, activation)
        == activation.approval.for_execution(SimulationEpisodeApproval).expires_at
    )
    with pytest.raises(RoboticsError) as error:
        validate_activation(
            bootstrap, ready, activation, now=activation_deadline(bootstrap, activation)
        )
    assert error.value.code is Code.APPROVAL_INVALID
    with pytest.raises(RoboticsError) as error:
        validate_activation(
            bootstrap,
            ready,
            activation,
            now=activation.preflight.for_execution(SimulationPreflightReceipt).valid_until,
        )
    assert error.value.code is Code.PREFLIGHT_FAILED
    with pytest.raises(RoboticsError) as error:
        validate_activation(bootstrap, ready, activation, now=bootstrap.authority_expires_at)
    assert error.value.code is Code.LEASE_INVALID


@pytest.mark.parametrize("stage", ["waiting", "binding"])
def test_initial_state_drift_before_sdk_never_reaches_authority(stage):
    harness, adapter, bootstrap = fixture()
    output = io.BytesIO()
    source = ActivationSource(output, bootstrap, harness)
    if stage == "waiting":

        def change(activation):
            adapter._advance()
            return canonical_json(activation) + b"\n"

        source.change = change
    else:
        bind = adapter.bind_episode

        def drifting_bind(episode):
            bind(episode)
            adapter._advance()

        adapter.bind_episode = drifting_bind
    with pytest.raises(RoboticsError):
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=source,
            destination=output,
        )
    assert not harness.guard.calls and adapter.terminated
    assert adapter.bind_calls == (0 if stage == "waiting" else 1)


def test_no_live_guard_refuses_before_worker_construction():
    harness, _, bootstrap = fixture()

    def never(_):
        raise AssertionError("authority-less worker was constructed")

    with pytest.raises(RoboticsError) as error:
        serve(
            bootstrap,
            factory=never,
            artifacts=harness.artifacts,
            authority=None,
            source=io.BytesIO(),
            destination=io.BytesIO(),
        )
    assert error.value.code is Code.ISOLATION_UNAVAILABLE


def test_valid_local_activation_does_not_override_live_authority_refusal():
    harness, adapter, bootstrap = fixture()
    harness.guard.allowed = False
    output = io.BytesIO()
    serve(
        bootstrap,
        factory=lambda _: adapter,
        artifacts=harness.artifacts,
        authority=harness.guard,
        source=ActivationSource(output, bootstrap, harness),
        destination=output,
    )
    assert not harness.guard.calls and adapter.bind_calls == 1
    assert b'"status":"ERROR"' in output.getvalue().splitlines()[-1]
    assert adapter.reset_calls == adapter.execute_calls == 0 and adapter.terminated


def test_missing_bind_hook_refuses_before_ready():
    harness, _, bootstrap = fixture()
    adapter = ScriptedFaultAdapter(
        bootstrap.description, harness.initial, harness.make_prepared, harness.artifacts
    )
    output = io.BytesIO()
    with pytest.raises(RoboticsError):
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=io.BytesIO(),
            destination=output,
        )
    assert not output.getvalue() and adapter.terminated and not harness.guard.calls


def test_approval_expiring_during_initial_recheck_refuses_binding(monkeypatch):
    harness, adapter, bootstrap = fixture()
    clock = {"now": datetime.now(UTC)}

    class Clock:
        @staticmethod
        def now(tz):
            return clock["now"]

    monkeypatch.setattr("accretion.robotics.host.worker.datetime", Clock)
    original_observe = adapter.observe
    observations = 0

    def delayed_observe():
        nonlocal observations
        observations += 1
        if observations == 2:
            clock["now"] += timedelta(minutes=1)
        return original_observe()

    adapter.observe = delayed_observe
    output = io.BytesIO()
    with pytest.raises(RoboticsError) as error:
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=ActivationSource(output, bootstrap, harness),
            destination=output,
        )
    assert error.value.code is Code.LEASE_INVALID
    assert adapter.bind_calls == 0 and not harness.guard.calls and adapter.terminated


def test_initial_transform_must_match_admitted_descriptor_before_ready():
    harness, adapter, bootstrap = fixture()
    actual = adapter.observe().model_copy(update={"frame_transform_digest": "0" * 64})
    adapter.observe = lambda: actual
    output = io.BytesIO()
    with pytest.raises(RoboticsError) as error:
        serve(
            bootstrap,
            factory=lambda _: adapter,
            artifacts=harness.artifacts,
            authority=harness.guard,
            source=io.BytesIO(),
            destination=output,
        )
    assert error.value.code is Code.EPISODE_STATE_CONFLICT
    assert not output.getvalue() and adapter.bind_calls == 0 and not harness.guard.calls
