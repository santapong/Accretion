"""Zero-provider, single-node instrumentation for the separately scoped pilot.

This is a local research instrument, not a production execution or benchmark API.
Only fixed fake hooks execute. Existing routing owns freezing/admission/receipts;
the existing deterministic verifier and M3 recorder own independent outcomes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

import accretion
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    EventType,
    EvidenceClass,
    IterationDirective,
    IterationDirectiveKind,
    Principal,
    PrincipalRef,
    Provider,
    Run,
    RunState,
    RuntimeExecutionRequest,
    SessionConfig,
    SessionRef,
    VerificationContext,
    VerificationResult,
    VerificationTarget,
    VerificationTargetKind,
    WorkspaceEntity,
    WorkspaceLease,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.refs import EvidenceRef
from accretion.contracts.routing import (
    ExecutionConfiguration,
    IndependentVerificationResult,
    NodeContract,
    RoutingDecisionReceipt,
    VerificationSpec,
)
from accretion.feedback.verification import IndependentVerificationRecorder
from accretion.governance import seed_governance
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.bootstrap import build_node_routing
from accretion.routing.protocols import RoutingMode
from accretion.routing.stages import DeterministicBehavior
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.templates import instantiate_run_graph, seed_templates
from accretion.verifiers.output_contract import OutputContractVerifier
from accretion.verifiers.registry import VerifierRegistry
from accretion.workspace import WorktreeManager

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "docs/research/v0.4/provider-pilot-2026-09-08"
STUDY_ID = "ACR-ROUTER-DEV-20260908"
CASES = ("valid-output", "incorrect-output", "missing-required-evidence")
GOOD = b'{"ok": true}\n'


class PilotRefused(ValueError):
    """The draft fake instrument cannot authorize this request."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PilotRefused(f"expected an object: {path.name}")
    return value


def validate_manifest(manifest: Mapping[str, Any], environment: Mapping[str, str]) -> None:
    """Refuse every execution-affecting change before constructing a runtime."""
    expected = _read(STUDY / "fake-instrumentation-manifest.json")
    # The committed recipe is not a permission source: these checks survive a changed file.
    fixed = {
        "study_id": STUDY_ID,
        "execution_source": "FAKE_INSTRUMENTATION",
        "allowed_runtime_providers": ["FAKE"],
        "routing_mode": "BASELINE_ONLY",
        "allowed_decision_kinds": ["EXPLOIT", "FALLBACK"],
        "allowed_risk_class": "LOW_DIGITAL",
        "selected_agent_tools": [],
        "live_execution_authority": "NOT_AUTHORIZED",
    }
    for key, value in fixed.items():
        if manifest.get(key) != value:
            raise PilotRefused(f"fake instrumentation refuses {key}")
    for key in (
        "live_providers_enabled",
        "external_network_enabled",
        "learned_scorer_enabled",
        "shadow_enabled",
        "exploration_enabled",
        "promotion_enabled",
    ):
        if manifest.get(key) is not False:
            raise PilotRefused(f"fake instrumentation refuses {key}")
    if dict(manifest) != expected:
        raise PilotRefused("only the reviewed committed fake recipe is accepted")
    limits = manifest["instrumentation_limits"]
    if limits != {
        "maximum_cases": 3,
        "maximum_attempts_per_case": 1,
        "maximum_concurrent_executions": 1,
        "maximum_case_seconds": 30,
        "maximum_total_seconds": 120,
        "maximum_artifact_bytes": 65536,
        "maximum_provider_calls": 0,
    }:
        raise PilotRefused("fake instrumentation limits changed")
    if manifest.get("fixture_cases") != list(CASES):
        raise PilotRefused("only the three fixed fixture cases are accepted")
    mode = environment.get("ACCRETION_NODE_ROUTING_MODE", "BASELINE_ONLY")
    if mode != "BASELINE_ONLY":
        raise PilotRefused("ambient routing mode is not BASELINE_ONLY")
    for key in ("ACCRETION_ENABLE_LIVE_PROVIDERS", "ACCRETION_LIVE_PROVIDERS"):
        if environment.get(key, "false").lower() not in {"", "false", "0", "no", "off"}:
            raise PilotRefused("ambient live-provider opt-in must be disabled")


async def _git(path: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            stdout, _stderr = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise PilotRefused("local fixture git command failed")
    return stdout.decode().strip()


def _fixture_hook(case: str, session: SessionRef) -> None:
    # Fixed local action. No caller-supplied path, command, network or native tools.
    (session.workspace / "result.json").write_bytes(
        b'{"ok": false}\n' if case == "incorrect-output" else GOOD
    )


class BoundedWorktreeManager(WorktreeManager):
    """Keep the existing worktree API; bound and reap its local git subprocesses."""

    @staticmethod
    async def _git(cwd: Path, *args: str) -> str:
        return await _git(cwd, *args)


async def _case(case: str, work: Path, output: Path, seconds: float) -> dict[str, Any]:
    store = MemoryStore()
    verifier = OutputContractVerifier()
    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(
                hook=lambda session, _request: _fixture_hook(case, session),
            )
        ]
    )
    manager = RunManager(
        store=store,
        worktrees=BoundedWorktreeManager(work / "worktrees", work / "artifacts"),
        runtimes={Provider.FAKE: runtime},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
        verifier_registry=VerifierRegistry((verifier,)),
    )
    service = build_node_routing(
        manager,
        policy_id="local-capability-policy",
        granted_permissions=set(),
        mode=RoutingMode.BASELINE_ONLY,
    )
    if (
        set(manager.runtimes) != {Provider.FAKE}
        or service.default_mode is not RoutingMode.BASELINE_ONLY
        or service.scorer is not None
        or service.post_route
        or service.post_node
        or type(service.behavior) is not DeterministicBehavior
    ):
        raise PilotRefused("learned or budget-bearing collaborator entered the fake stack")
    manager.routing_service = service
    repository = work / "repository"
    repository.mkdir()
    await _git(repository, "init", "-q")
    await _git(repository, "config", "core.hooksPath", "/dev/null")
    await _git(repository, "config", "commit.gpgsign", "false")
    (repository / "result.json").write_bytes(b'{"ok": false}\n')
    await _git(repository, "add", "result.json")
    await _git(
        repository,
        "-c",
        "user.name=Accretion Instrumentation",
        "-c",
        "user.email=instrumentation@accretion.local",
        "commit",
        "-qm",
        "fake fixture",
    )
    await seed_templates(store)
    await seed_governance(store)
    principal = Principal(
        principal_id=new_id("principal"),
        issuer="local-fake-instrumentation",
        subject=STUDY_ID,
        display_name="Fake instrumentation",
    )
    await store.upsert_principal(principal)
    workspace_id = new_id("workspace_entity")
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name=case))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=workspace_id,
            principal_id=principal.principal_id,
            role=WorkspaceRole.ADMIN,
        )
    )
    project = await manager.create_project("Fake instrumentation", repository)
    required_outputs: list[dict[str, Any]] = [
        {"path": "result.json", "kind": "json", "sha256": _sha(GOOD)},
    ]
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Write the fixed local fixture artifact.",
        task_patch={
            "risk_level": "LOW",
            "allowed_capabilities": [],
            "required_outputs": required_outputs,
            "budgets": {"wall_time_seconds": 30, "max_turns": 1, "max_tool_calls": 1},
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    template = await store.get_workflow_template(planning.current_decision.selected_template_id)
    if template is None:
        raise PilotRefused("no validated fixture template")
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=principal.principal_id,
    )
    await store.create_run(run)
    graph = instantiate_run_graph(
        template,
        run_id=run.run_id,
        task_id=run.task_id,
        budgets=task.envelope.budgets,
    )
    await store.create_run_graph(graph)
    node = next(item for item in graph.nodes if item.kind.value == "AGENT")
    spec = next(item for item in template.nodes if item.key == node.key)
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=[verifier.verifier_id],
        outcome_check="The fixed output digest must match independently.",
    )
    await store.save_acceptance_policy(policy)
    frozen = await service.freeze(
        run=run,
        task=task,
        node=node,
        spec=spec,
        template=template,
        policy=policy,
        graph_revision=graph.graph_revision,
        attempt=1,
    )
    if frozen.node_contract.allowed_risk_class.value != "LOW_DIGITAL":
        raise PilotRefused("fixture must remain LOW_DIGITAL")
    snapshot = await service.snapshot(
        workspace_id=workspace_id,
        project_id=project.project_id,
        task=task,
    )
    receipt = await service.route(
        frozen=frozen,
        snapshot=snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=run,
    )
    configuration = await service.configuration_for(receipt)
    if (
        configuration.runtime.provider is not Provider.FAKE
        or configuration.model.provider is not Provider.FAKE
        or configuration.tools
        or configuration.skills
        or receipt.decision_type.value not in {"EXPLOIT", "FALLBACK"}
    ):
        raise PilotRefused("selected configuration cannot enter fake instrumentation")
    configuration = await service.claim_dispatch(receipt=receipt, run=run)
    lease = await manager.worktrees.acquire(
        project_id=project.project_id,
        run_id=run.run_id,
        repository=repository,
    )
    session_config = SessionConfig(
        run_id=run.run_id,
        workspace=lease.path,
        model=configuration.model.model_id,
        allowed_tools=[],
        denied_tools=["*"],
    )
    session = await runtime.create_session(session_config)
    if runtime.session_models[session.session_id] != configuration.model.model_id:
        raise PilotRefused("observed fake model differs from the selected model")
    started = datetime.now(UTC)
    clock = time.monotonic()
    request = RuntimeExecutionRequest(
        runtime_call_id=new_id("runtime_call"),
        run_id=run.run_id,
        task=task.envelope,
        directive=IterationDirective(
            kind=IterationDirectiveKind.INITIAL,
            objective="Write only the fixed fixture.",
        ),
        deadline=started + timedelta(seconds=seconds),
        max_turns=1,
        max_tool_calls=1,
    )
    native = output / "native" / case
    for name, value in {
        "configuration": configuration,
        "receipt": receipt,
        "node-contract": frozen.node_contract,
        "verification-spec": frozen.verification_spec,
        "request": request,
        "session": session,
        "session-config": session_config,
        "workspace-lease": lease,
    }.items():
        _write(native / f"{name}.json", value.model_dump(mode="json"))
    dispatches = await store.list_events(run.run_id)
    if not any(
        event.native_type == "accretion/routing/dispatch"
        and event.payload.get("receipt_id") == receipt.contract_id
        for event in dispatches
    ):
        raise PilotRefused("no persisted dispatch claim before submit")
    call = await runtime.submit(session, request)
    events = []
    try:
        async with asyncio.timeout(seconds):
            async for event in runtime.events(call):
                events.append(await store.append_event(event))
    except BaseException:
        await runtime.interrupt(call)
        _write(
            native / "interrupted.json",
            {
                "execution_instance_id": frozen.execution_instance_id,
                "runtime_call_id": request.runtime_call_id,
                "state": "INTERRUPTED",
                "provider_calls": 0,
                "automatic_retry": False,
            },
        )
        raise
    finally:
        await runtime.terminate(call)
    completed = datetime.now(UTC)
    if not events or events[-1].normalized_type is not EventType.RUNTIME_CALL_COMPLETED:
        raise PilotRefused("fake execution did not complete; no retry authorized")
    artifact = (lease.path / "result.json").read_bytes()
    if len(artifact) > 65536:
        raise PilotRefused("artifact exceeds the fixed byte cap")
    _write(native / "events.json", [event.model_dump(mode="json") for event in events])
    _write(native / "dispatch-events.json", [event.model_dump(mode="json") for event in dispatches])
    artifact_path = output / "artifacts" / f"{case}.json"
    artifact_path.parent.mkdir(exist_ok=True)
    with artifact_path.open("xb") as handle:
        handle.write(artifact)
    source = await verifier.verify(
        VerificationTarget(
            target_ref=frozen.execution_instance_id,
            kind=VerificationTargetKind.OUTPUT_CONTRACT,
            run_id=run.run_id,
            required_outputs=[] if case == "missing-required-evidence" else required_outputs,
        ),
        VerificationContext(
            task_id=run.task_id,
            project_id=project.project_id,
            workspace=lease.path,
            timeout_seconds=seconds,
            max_output_bytes=65536,
        ),
    )
    evidence = {
        ref: EvidenceRef(
            evidence_id=ref, evidence_class=EvidenceClass.DIGITAL, content_digest=_sha(artifact)
        )
        for ref in source.evidence_refs
    }
    verified_at = datetime.now(UTC)
    independent = IndependentVerificationRecorder().record(
        spec=frozen.verification_spec,
        results=[source],
        execution_instance_id=frozen.execution_instance_id,
        producer_session_id=session.session_id,
        verifier_session_ids={verifier.verifier_id: None},
        verification_spec_hash=frozen.verification_spec.content_hash,
        verifier=configuration.verifier.verifier,
        workspace_id=workspace_id,
        project_id=project.project_id,
        clock=lambda: verified_at,
        created_by=PrincipalRef(
            principal_id=principal.principal_id,
            display_name=principal.display_name,
            status=principal.status,
        ),
        evidence=evidence,
        producer_runtime=runtime.adapter_version,
        verifier_runtimes={verifier.verifier_id: None},
    )
    await store.put_verification_result(independent)
    persisted = await store.get_verification_result(independent.contract_id)
    if persisted != independent:
        raise PilotRefused("independent verification did not persist")
    _write(native / "source-verification.json", source.model_dump(mode="json"))
    _write(native / "independent-verification.json", independent.model_dump(mode="json"))
    _write(
        native / "verification-input.json",
        {
            "verifier_session_ids": {verifier.verifier_id: None},
            "verifier_runtimes": {verifier.verifier_id: None},
            "evidence": {key: value.model_dump(mode="json") for key, value in evidence.items()},
        },
    )
    record: dict[str, Any] = {
        "schema_version": "0.1.0",
        "study_id": STUDY_ID,
        "artifact_kind": "DRY_RUN_RECORD",
        "execution_source": "FAKE_INSTRUMENTATION",
        "data_role": "FAKE_INSTRUMENTATION",
        "live_execution_authority": "NOT_AUTHORIZED",
        "trial_id": case,
        "pair_id": None,
        "task_id": run.task_id,
        "project_lineage": f"fake-instrumentation:{case}",
        "input_revision": lease.base_revision,
        "configuration": {
            "id": configuration.contract_id,
            "content_hash": configuration.content_hash,
            "configuration_hash": configuration.configuration_hash,
            "provider": "FAKE",
            "model": configuration.model.model_id,
            "runtime_version": configuration.runtime.adapter_version,
            "selected_agent_tools": [],
            "requested_model": session_config.model,
            "accepted_model": runtime.session_models[session.session_id],
            "observed_model": runtime.session_models[session.session_id],
        },
        "routing": {
            "mode": "BASELINE_ONLY",
            "decision_kind": receipt.decision_type.value,
            "receipt_id": receipt.contract_id,
            "receipt_hash": receipt.content_hash,
            "node_contract_hash": receipt.node_contract_hash,
            "shadow_enabled": False,
            "exploration_enabled": False,
            "promotion_enabled": False,
        },
        "execution": {
            "execution_instance_id": frozen.execution_instance_id,
            "producer_session_id": session.session_id,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "status": "COMPLETED",
        },
        "verification": {
            "record_id": independent.contract_id,
            "execution_instance_id": independent.execution_instance_id,
            "spec_hash": independent.verification_spec_hash,
            "status": independent.status.value,
            "verifier_id": verifier.verifier_id,
            "verifier_version": verifier.verifier_version,
            "verifier_session_id": None,
            "deterministic": True,
            "independent": True,
            "required_claim_coverage": min(item.coverage for item in independent.claim_results),
            "evidence_hashes": sorted({item.content_digest for item in evidence.values()}),
            "adjudication_status": "NOT_NEEDED_FAKE",
        },
        "accounting": {
            "provider_calls": 0,
            "external_tokens": 0,
            "provider_charge": 0,
            "currency": None,
            "provider_cost_basis": "NO_PROVIDER_CALLS",
            "elapsed_ms": round((time.monotonic() - clock) * 1000, 3),
            "provider_wait_ms": 0,
            "local_compute_ms": None,
            "human_adjudication_ms": 0,
            "executed_tool_calls": 0,
            "usage_source": "LOCAL_FAKE_INSTRUMENTATION",
        },
        "measurement_status": "INCONCLUSIVE" if not evidence else "COMPLETE_FAKE_RECORD",
        "limitations": [
            "Fake single-node instrumentation; no hosted or learned-router result.",
            "Zero provider charge means no provider calls; no prices were measured.",
            "Local compute and full production scheduler/feedback integration are not measured.",
        ],
    }
    _write(output / "records" / f"{case}.json", record)
    validate_record(record, native, artifact_path)
    return record


def validate_record(record: Mapping[str, Any], native: Path, artifact_path: Path) -> None:
    """Validate schema, native seals, record joins and independently captured bytes."""
    schema = _read(STUDY / "trial-record.schema.json")
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(record))
    raw = {
        name: _read(native / f"{name}.json")
        for name in (
            "configuration",
            "receipt",
            "node-contract",
            "verification-spec",
            "independent-verification",
            "source-verification",
            "session",
            "session-config",
            "workspace-lease",
            "request",
            "verification-input",
        )
    }
    for name in (
        "configuration",
        "receipt",
        "node-contract",
        "verification-spec",
        "independent-verification",
    ):
        if not raw[name].get("content_hash"):
            raise PilotRefused("native contract is missing its writer seal")
    configuration = ExecutionConfiguration.model_validate(raw["configuration"])
    receipt = RoutingDecisionReceipt.model_validate(raw["receipt"])
    node = NodeContract.model_validate(raw["node-contract"])
    spec = VerificationSpec.model_validate(raw["verification-spec"])
    result = IndependentVerificationResult.model_validate(raw["independent-verification"])
    source = VerificationResult.model_validate(raw["source-verification"])
    session = SessionRef.model_validate(raw["session"])
    session_config = SessionConfig.model_validate(raw["session-config"])
    lease = WorkspaceLease.model_validate(raw["workspace-lease"])
    request = RuntimeExecutionRequest.model_validate(raw["request"])
    events = [
        AgentEvent.model_validate(row) for row in json.loads((native / "events.json").read_text())
    ]
    dispatches = [
        AgentEvent.model_validate(row)
        for row in json.loads((native / "dispatch-events.json").read_text())
    ]
    if not events or events[-1].normalized_type is not EventType.RUNTIME_CALL_COMPLETED:
        raise PilotRefused("export has no completed native runtime terminal")
    claims = [
        event
        for event in dispatches
        if event.native_type == "accretion/routing/dispatch"
        and event.payload.get("receipt_id") == receipt.contract_id
    ]
    if len(claims) != 1 or claims[0].sequence >= events[0].sequence:
        raise PilotRefused("export has no unique dispatch claim before runtime events")
    inputs = raw["verification-input"]
    evidence = {key: EvidenceRef.model_validate(value) for key, value in inputs["evidence"].items()}
    artifact_digest = _sha(artifact_path.read_bytes())
    recomputed = IndependentVerificationRecorder().record(
        spec=spec,
        results=[source],
        execution_instance_id=node.execution_instance_id,
        producer_session_id=session.session_id,
        verifier_session_ids=inputs["verifier_session_ids"],
        verification_spec_hash=spec.content_hash,
        verifier=configuration.verifier.verifier,
        workspace_id=result.workspace_id,
        project_id=result.project_id or "",
        clock=lambda: result.signed_at,
        created_by=result.created_by,
        evidence=evidence,
        producer_runtime=configuration.runtime.adapter_version,
        verifier_runtimes=inputs["verifier_runtimes"],
    )
    native_hashes = sorted({item.content_digest for item in result.deterministic_evidence_refs})
    expected_refs = [f"file-sha256:result.json:{artifact_digest}"] if native_hashes else []
    expected_completeness = "COMPLETE_FAKE_RECORD" if native_hashes else "INCONCLUSIVE"
    checks = (
        recomputed == result,
        record["artifact_kind"] == "DRY_RUN_RECORD",
        record["configuration"]["id"]
        == configuration.contract_id
        == receipt.selected_configuration_id,
        record["configuration"]["configuration_hash"]
        == configuration.configuration_hash
        == receipt.selected_configuration_hash,
        record["configuration"]["content_hash"] == configuration.content_hash,
        record["configuration"]["provider"]
        == configuration.runtime.provider.value
        == configuration.model.provider.value
        == session.provider.value
        == "FAKE",
        record["configuration"]["runtime_version"] == configuration.runtime.adapter_version,
        record["configuration"]["selected_agent_tools"] == configuration.tools == [],
        configuration.skills == [],
        session_config.allowed_tools == [],
        session_config.denied_tools == ["*"],
        record["routing"]["receipt_id"] == receipt.contract_id,
        record["routing"]["receipt_hash"] == receipt.content_hash,
        record["routing"]["decision_kind"] == receipt.decision_type.value,
        record["routing"]["node_contract_hash"]
        == node.immutable_hash
        == receipt.node_contract_hash,
        record["execution"]["execution_instance_id"]
        == node.execution_instance_id
        == result.execution_instance_id
        == source.target_ref,
        record["verification"]["execution_instance_id"] == result.execution_instance_id,
        record["verification"]["record_id"] == result.contract_id,
        record["verification"]["spec_hash"]
        == result.verification_spec_hash
        == spec.content_hash
        == configuration.verifier.verification_spec_hash,
        record["verification"]["status"] == result.status.value,
        record["verification"]["verifier_id"]
        == source.verifier_id
        == configuration.verifier.verifier.verifier_contract_id,
        record["verification"]["verifier_version"] == source.verifier_version,
        record["verification"]["required_claim_coverage"]
        == min(item.coverage for item in result.claim_results),
        record["verification"]["evidence_hashes"] == native_hashes,
        set(source.evidence_refs) == set(evidence) == set(expected_refs),
        all(item.content_digest == artifact_digest for item in evidence.values()),
        record["verification"]["independent"] is True,
        record["verification"]["deterministic"] is True,
        record["verification"]["verifier_session_id"] is None,
        record["verification"]["adjudication_status"] == "NOT_NEEDED_FAKE",
        inputs["verifier_session_ids"] == {source.verifier_id: None},
        inputs["verifier_runtimes"] == {source.verifier_id: None},
        record["execution"]["producer_session_id"] == session.session_id,
        record["execution"]["status"] == "COMPLETED",
        source.run_id
        == session.run_id
        == session_config.run_id
        == request.run_id
        == lease.run_id
        == node.labels.get("run_id"),
        session.workspace == session_config.workspace == lease.path,
        record["input_revision"] == lease.base_revision,
        record["project_lineage"] == f"fake-instrumentation:{record['trial_id']}",
        record["task_id"] == request.task.task_id,
        record["configuration"]["model"]
        == record["configuration"]["requested_model"]
        == record["configuration"]["accepted_model"]
        == record["configuration"]["observed_model"]
        == configuration.model.model_id
        == session_config.model,
        request.max_turns == request.max_tool_calls == 1,
        request.task.allowed_capabilities == [],
        all(
            event.provider is Provider.FAKE
            and event.run_id == request.run_id
            and event.session_id == session.session_id
            and event.adapter_version == configuration.runtime.adapter_version
            and event.correlation_id == request.runtime_call_id
            and event.payload.get("runtime_call_id") == request.runtime_call_id
            for event in events
        ),
        all(
            event.run_id == request.run_id and event.provider is Provider.FAKE
            for event in dispatches
        ),
        len({event.sequence for event in events}) == len(events),
        [event.sequence for event in events] == sorted(event.sequence for event in events),
        events[0].normalized_type is EventType.RUNTIME_CALL_STARTED,
        events[0].timestamp >= claims[0].timestamp,
        datetime.fromisoformat(record["execution"]["completed_at"])
        >= events[-1].timestamp
        >= events[0].timestamp
        >= datetime.fromisoformat(record["execution"]["started_at"]),
        result.signed_at >= datetime.fromisoformat(record["execution"]["completed_at"]),
        record["accounting"]["local_compute_ms"] is None,
        record["accounting"]["human_adjudication_ms"] == 0,
        record["measurement_status"] == expected_completeness,
    )
    if not all(checks):
        raise PilotRefused("export identity, timing, verification or accounting join failed")


def validate_export(output: Path) -> dict[str, Any]:
    """Check complete-file integrity and semantic joins without executing anything."""
    manifest = _read(output / "manifest.json")
    actual = {
        str(path.relative_to(output)): _sha(path.read_bytes())
        for path in sorted(output.rglob("*"))
        if path.is_file() and path != output / "manifest.json"
    }
    if manifest != actual:
        raise PilotRefused("export file manifest changed or is incomplete")
    report = _read(output / "report.json")
    for case in CASES:
        record = _read(output / "records" / f"{case}.json")
        if record["trial_id"] != case:
            raise PilotRefused("record trial identity differs from its export case")
        validate_record(record, output / "native" / case, output / "artifacts" / f"{case}.json")
        if report["cases"][case] != record["verification"]["status"]:
            raise PilotRefused("report outcome differs from its native record")
    return report


async def run_dry_run(output: Path, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create new, immutable local evidence; never launch a provider or retry a case."""
    recipe = (
        dict(manifest)
        if manifest is not None
        else _read(STUDY / "fake-instrumentation-manifest.json")
    )
    output = output.resolve()
    if output.exists():
        raise FileExistsError("evidence destination already exists; refusing overwrite")
    try:
        validate_manifest(recipe, os.environ)
    except PilotRefused as exc:
        output.mkdir(parents=True, exist_ok=False)
        _write(
            output / "admission-denied.json",
            {
                "state": "REFUSED_BEFORE_RUNTIME_CONSTRUCTION",
                "reason": str(exc),
                "executions": 0,
                "provider_calls": 0,
                "live_execution_authority": "NOT_AUTHORIZED",
            },
        )
        raise
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "recipe.json", recipe)
    records = []
    try:
        async with asyncio.timeout(120):
            with tempfile.TemporaryDirectory(prefix="accretion-fake-pilot-") as directory:
                for case in CASES:
                    work = Path(directory) / case
                    work.mkdir()
                    async with asyncio.timeout(30):
                        records.append(await _case(case, work, output, 30))
    except BaseException as exc:
        _write(
            output / "failure.json",
            {
                "state": "STOPPED",
                "error_type": type(exc).__name__,
                "completed_case_count": len(records),
                "automatic_retry": False,
                "live_execution_authority": "NOT_AUTHORIZED",
            },
        )
        raise
    report = {
        "study_id": STUDY_ID,
        "status": "FAKE_INSTRUMENTATION_VERIFIED",
        "live_execution_authority": "NOT_AUTHORIZED",
        "provider_calls": 0,
        "cases": {row["trial_id"]: row["verification"]["status"] for row in records},
        "completed_at": datetime.now(UTC).isoformat(),
        "source_commit": await _git(ROOT, "rev-parse", "HEAD"),
        "source_status_porcelain": (
            await _git(
                ROOT,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
        ).splitlines(),
        "source_sha256": {
            str(path.relative_to(ROOT)): _sha(path.read_bytes())
            for path in (
                Path(__file__),
                ROOT / "scripts/provider_pilot_dry_run.py",
                ROOT / "src/accretion/routing/bootstrap.py",
                ROOT / "src/accretion/routing/service.py",
                ROOT / "src/accretion/feedback/verification.py",
                ROOT / "src/accretion/runtimes/fake.py",
                ROOT / "src/accretion/verifiers/output_contract.py",
            )
        },
        "runtime_environment": {
            "python_executable": sys.executable,
            "python_version": sys.version,
            "accretion_module": str(accretion.__file__),
            "instrumentation_module": str(Path(__file__).resolve()),
            "dependency_versions": {
                name: version(name) for name in ("pydantic", "jsonschema", "SQLAlchemy")
            },
            "uv_lock_sha256": _sha((ROOT / "uv.lock").read_bytes()),
            "pyproject_sha256": _sha((ROOT / "pyproject.toml").read_bytes()),
        },
        "limitations": records[0]["limitations"],
    }
    _write(output / "report.json", report)
    _write(
        output / "manifest.json",
        {
            str(path.relative_to(output)): _sha(path.read_bytes())
            for path in sorted(output.rglob("*"))
            if path.is_file()
        },
    )
    return validate_export(output)
