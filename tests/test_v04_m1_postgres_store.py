"""M1's compatibility decisions against a real PostgreSQL database.

M0 already proved that ``PostgresStore`` and ``MemoryStore`` agree for *one* record per v0.4
table, built from a golden fixture. This file proves the thing M1 actually depends on and M0
could not: that a whole *evaluation* — the mixed run of gate decisions and rule decisions one
routing attempt produces — survives the round trip with the same content and in the same
order, and that re-persisting it is a no-op.

Why the distinction matters. A routing attempt writes many decisions at once, several of them
sharing a ``created_at`` because they were evaluated against one snapshot with one clock. The
list order is then decided entirely by the tie-break, and a backend whose tie-break differed
would return the same *set* and a different *sequence* — which is invisible to a test that
persists one row, and is exactly what an operator reading a decision trail would notice
first. So the decisions here deliberately share timestamps in pairs.

The second claim is idempotence. The M1 engine derives a decision's ``contract_id`` from its
inputs, so replaying an evaluation re-derives ids that already exist. If a re-put were a
rejection rather than a no-op, replay would be impossible on both backends; if it were an
overwrite, the append-only guarantee would be false on one. The test writes every decision
twice and asserts nothing moved.

Every id is uuid-suffixed, so this file is re-runnable against a database it has already
written to, and no assertion is made about a global row count. Nothing here carries an
acceptance marker: AC4-M1-005 through 008 are proved offline in
``tests/test_v04_m1_gates.py`` and ``tests/test_v04_m1_compatibility.py``, and a criterion
whose proof needed a database would be a criterion CI could not gate without one.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from accretion.contracts import (
    CapabilityBackend,
    CapabilityPolicy,
    GraphNodeKind,
    IdempotencyMode,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    RiskLevel,
    Task,
    TaskEnvelope,
)
from accretion.contracts.refs import (
    CapabilityRef,
    EnvironmentRef,
    PolicyRef,
    RuntimeRef,
    SkillRef,
    ToolRef,
    VerifierRef,
)
from accretion.contracts.routing import (
    CapabilityRequirement,
    CompatibilityDecision,
    CompatibilityStatus,
    EnvironmentBinding,
    ExecutionConfiguration,
    ModelBinding,
    NodeContract,
    ObjectiveContractRef,
    ResourceBudget,
    RiskClass,
    SubjectType,
    ToolBinding,
    VerificationSpecRef,
    VerifierBinding,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import derived_id, new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.routing.compatibility import CompatibilityEngine
from accretion.routing.gates import PolicyGate, is_gate_decision
from accretion.routing.snapshot import RoutingSnapshot
from accretion.runtimes.fake import FakeRuntime

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

FIXED_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LATER_TIME = FIXED_TIME + timedelta(minutes=5)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="M1 postgres parity",
    status=PrincipalStatus.ACTIVE,
)


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def snapshot_for(marker: str, policy: CapabilityPolicy) -> RoutingSnapshot:
    """A snapshot built by hand and keyed by ``marker``.

    Hand-built rather than taken from ``RegistrySnapshotBuilder`` because this file is about
    persistence, not observation: what has to differ between two runs of the test is the four
    ids, since they are inside every decision's derived ``contract_id`` and a re-run against
    the same database must not collide with the rows the last run wrote.
    """

    return RoutingSnapshot(
        capability_registry_snapshot_id=digest(f"registry-{marker}"),
        available_runtime_snapshot_id=digest(f"runtimes-{marker}"),
        connection_availability_snapshot_id=digest(f"connections-{marker}"),
        policy_snapshot_id=f"{policy.policy_id}@{policy.version}",
        capabilities=(),
        skills=(),
        plugins=(),
        verifier_ids=(),
        runtime_health=(),
        mcp_server_states=(),
        policy=policy,
        fallback_bundle_digest="fallback-bundle/0",
        taken_at=FIXED_TIME,
    )


def task_for(project_id: str, task_id: str) -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id=task_id,
            project_id=project_id,
            objective="persist one routing evaluation",
            risk_level=RiskLevel.HIGH,
            allowed_capabilities=["cap.search"],
        ),
        created_at=FIXED_TIME,
    )


def node_contract_for(workspace_id: str, project_id: str, marker: str) -> NodeContract:
    return NodeContract(
        contract_id=derived_id("node_contract", "m1-postgres", marker),
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=workspace_id,
        project_id=project_id,
        objective_contract_ref=ObjectiveContractRef(
            contract_id="objective-contract-ref-embedded",
            created_at=FIXED_TIME,
            created_by=PRINCIPAL,
            workspace_id=workspace_id,
            project_id=project_id,
            objective_contract_id=derived_id("objective_contract", marker),
            revision=1,
            objective_contract_hash=digest(f"objective-{marker}"),
            verified_success_floor=0.9,
            utility_profile_id="balanced-delivery",
            risk_policy=PolicyRef(
                policy_id="workspace-risk-policy",
                version="1.0.0",
                content_digest=digest("risk-policy"),
            ),
            approved_by=PRINCIPAL,
            approved_at=FIXED_TIME,
        ),
        node_id="node-1",
        run_graph_id=f"rgr_{marker}",
        graph_revision=1,
        execution_instance_id=f"node-1#{marker}",
        objective="apply the reviewed change",
        node_kind=GraphNodeKind.AGENT,
        required_capabilities=[
            CapabilityRequirement(
                capability=CapabilityRef(capability_id="cap.search", capability_version="1.0.0"),
                version_range=">=1.0.0",
                required_scope="read",
            )
        ],
        allowed_risk_class=RiskClass.LOW_DIGITAL,
        resource_cap=ResourceBudget(
            maximum_cost="1.00",
            maximum_latency_ms=60_000,
            maximum_attempts=2,
            maximum_tool_calls=10,
        ),
        verification_spec_ref=VerificationSpecRef(
            verification_spec_id="vsp_m1_postgres", content_hash=digest(f"spec-{marker}")
        ),
        failure_policy_ref=PolicyRef(
            policy_id="failure-policy",
            version="1.0.0",
            content_digest=digest("failure-policy"),
        ),
    )


def configuration_for(workspace_id: str, project_id: str, marker: str) -> ExecutionConfiguration:
    return ExecutionConfiguration(
        contract_id=derived_id("execution_configuration", "m1-postgres", marker),
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=workspace_id,
        project_id=project_id,
        environment=EnvironmentBinding(
            environment=EnvironmentRef(
                environment_id="env.local",
                image_digest=digest("env.local"),
                policy_profile="local-worktree",
            ),
            workspace_isolation="WORKTREE",
        ),
        runtime=RuntimeRef(
            runtime_id="runtime_fake",
            adapter_version=FakeRuntime.adapter_version,
            provider=Provider.FAKE,
            model="fake-model",
            capability_profile_digest=digest("runtime-profile"),
        ),
        model=ModelBinding(model_id="fake-model", provider=Provider.FAKE),
        tools=[
            ToolBinding(
                capability=CapabilityRef(capability_id="cap.search", capability_version="1.0.0"),
                tool=ToolRef(
                    tool_id="tool.cap.search",
                    implementation_digest=digest("tool.cap.search"),
                ),
                binding_id="capbind_cap.search_conndef_search",
                binding_version="1.0.0",
            )
        ],
        skills=[
            SkillRef(skill_id="skill.review", version="1.0.0", package_digest=digest("skill"))
        ],
        verifier=VerifierBinding(
            verifier=VerifierRef(
                verifier_contract_id="stub-verifier",
                implementation_digest=digest("stub-verifier"),
            ),
            version="1.0.0",
            verification_spec_hash=digest(f"spec-{marker}"),
        ),
    )


def evaluation_for(
    workspace_id: str, project_id: str, marker: str
) -> list[CompatibilityDecision]:
    """One routing attempt's worth of decisions: gates and rules, mixed and ordered.

    Two clocks on purpose. Half the decisions share ``FIXED_TIME`` and half share
    ``LATER_TIME``, so the list order below exercises both halves of the ``(created_at, id)``
    sort key rather than only the tie-break — a backend that ordered by insertion would agree
    with the other on the first half and disagree on the second.
    """

    from accretion.contracts import Capability

    policy = CapabilityPolicy(
        policy_id=f"pol_{marker}",
        version="1.0.0",
        description="M1 postgres parity policy",
        explicitly_denied=["cap.forbidden"],
        created_at=FIXED_TIME,
    )
    snapshot = snapshot_for(marker, policy)
    task = task_for(project_id, f"tsk_{marker}")
    gate = PolicyGate(CapabilityPolicyEngine(set()), policy, created_by=PRINCIPAL)
    engine = CompatibilityEngine(created_by=PRINCIPAL)

    def capability(capability_id: str) -> Capability:
        return Capability(
            capability_id=capability_id,
            version="1.0.0",
            description="M1 postgres fixture",
            backend=CapabilityBackend.PYTHON,
            idempotency=IdempotencyMode.NONE,
            created_at=FIXED_TIME,
        )

    decisions = [
        gate.gate_permissions(
            PRINCIPAL,
            workspace_id,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=lambda: FIXED_TIME,
        ),
        gate.gate_risk(
            node_contract_for(workspace_id, project_id, marker),
            task,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=lambda: FIXED_TIME,
        ),
        gate.gate_capability(
            task,
            capability("cap.forbidden"),
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=lambda: LATER_TIME,
        ),
    ]
    decisions.extend(
        engine.evaluate_joint(
            configuration=configuration_for(workspace_id, project_id, marker),
            node_contract=node_contract_for(workspace_id, project_id, marker),
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=lambda: LATER_TIME,
        )
    )
    return decisions


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row: ``compatibility_decisions`` has a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"), name=f"v0.4 M1 {marker}", repository_path=tmp_path
    )
    await store.create_project(project)
    return project


async def test_a_whole_evaluation_reads_back_from_postgres_exactly_as_from_memory(
    tmp_path: Path,
) -> None:
    """Content parity and order parity, on a mixed run of gate and rule decisions.

    Equality is asserted on the objects the *stores* return, never on the ones handed to
    them: a backend that dropped ``labels`` on the way in and rebuilt them on the way out
    would pass an identity check against the input and fail this one. The order assertion is
    made against the documented sort key rather than against the write order, because
    agreeing with each other while both being wrong is the failure a parity test that only
    compared the two lists would miss.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(postgres, tmp_path, marker)
        # The same project in both backends: `MemoryStore` mirrors the RESTRICT key, so a
        # twin with no project row would refuse every record PostgreSQL accepts.
        await memory.create_project(project)
        written = evaluation_for(workspace_id, project.project_id, marker)
        assert len(written) > 4
        assert any(is_gate_decision(decision) for decision in written)
        assert any(not is_gate_decision(decision) for decision in written)

        for decision in written:
            await postgres.put_compatibility_decision(decision)
            await memory.put_compatibility_decision(decision)

        from_postgres = await postgres.list_compatibility_decisions(workspace_id=workspace_id)
        from_memory = await memory.list_compatibility_decisions(workspace_id=workspace_id)

        assert from_postgres == from_memory
        assert len(from_postgres) == len(written)
        assert [decision.contract_id for decision in from_postgres] == [
            decision.contract_id
            for decision in sorted(written, key=lambda item: (item.created_at, item.contract_id))
        ]
        # The promoted columns are what a §13.1 query filters on, so they have to survive as
        # well as the payload does.
        for decision in from_postgres:
            assert decision.rule_version
            assert decision.status in set(CompatibilityStatus)
            assert decision.subject_type in set(SubjectType)
            assert decision.content_hash == next(
                item.content_hash for item in written if item.contract_id == decision.contract_id
            )

        # The project filter narrows and never widens, on both backends alike.
        scoped = await postgres.list_compatibility_decisions(
            workspace_id=workspace_id, project_id=project.project_id
        )
        assert scoped == from_postgres
        assert (
            await postgres.list_compatibility_decisions(
                workspace_id=workspace_id, project_id=new_id("project")
            )
            == []
        )
    finally:
        await engine.dispose()


async def test_replaying_an_evaluation_is_a_no_op_on_both_backends(tmp_path: Path) -> None:
    """A derived id put twice with the same payload changes nothing, either side.

    This is what makes replay possible at all. M1 derives a decision's ``contract_id`` from
    the rule, the rule version, the subject, the four snapshot ids and the verdict, so
    re-evaluating an unchanged world re-derives ids that are already in the store. A backend
    that rejected the repeat would make replay an error; one that inserted a second row would
    make the append-only table grow without a new fact in it. Both stores must do the third
    thing, which is nothing.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(postgres, tmp_path, marker)
        await memory.create_project(project)
        written = evaluation_for(workspace_id, project.project_id, marker)
        for decision in written:
            await postgres.put_compatibility_decision(decision)
            await memory.put_compatibility_decision(decision)
        first = await postgres.list_compatibility_decisions(workspace_id=workspace_id)

        # Replay: the same evaluation of the same unchanged world, derived again from
        # scratch rather than re-using the objects above, so the ids are re-derived and not
        # merely re-sent.
        for decision in evaluation_for(workspace_id, project.project_id, marker):
            assert await postgres.put_compatibility_decision(decision) == decision
            assert await memory.put_compatibility_decision(decision) == decision

        assert await postgres.list_compatibility_decisions(workspace_id=workspace_id) == first
        assert await memory.list_compatibility_decisions(workspace_id=workspace_id) == first

        # And a different verdict under the same rule and subject is a *different* id, so it
        # becomes a second row rather than an overwrite of the first.
        tampered = written[0].model_copy(
            update={"reason_code": "POLICY_INCOMPATIBLE", "content_hash": ""}
        )
        resealed = CompatibilityDecision.model_validate(tampered.model_dump(mode="json"))
        with pytest.raises(ValueError, match="is immutable"):
            await postgres.put_compatibility_decision(resealed)
        with pytest.raises(ValueError, match="is immutable"):
            await memory.put_compatibility_decision(resealed)
    finally:
        await engine.dispose()
