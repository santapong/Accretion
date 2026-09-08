"""M7's two readers against a real PostgreSQL database: parity, and only parity.

The twin of ``tests/test_v04_m7_bandit.py`` and ``tests/test_v04_m7_breaker_inputs.py``, and it
does not repeat either. Every claim about what exploration *means* is proved there against
``MemoryStore``, where the acceptance markers live. What cannot be proved there is what this
file exists for: M7 adds no store method, but it adds two **derivations** over rows that both
backends have to produce identically, and each of them reduces a list to a single number that a
routing decision then turns on.

* :class:`~accretion.routing.bandit.LedgerRegistry` rebuilds a workspace's exploration budget by
  replaying ``EXPLORE`` receipts (ADR4-M7-003). ``list_routing_receipts`` returns rows in each
  backend's own order — a dictionary's insertion order in memory, an ``ORDER BY`` in
  PostgreSQL — and the ledger sums floats over them. Two backends that returned the same rows
  in different orders would produce budgets that differ in the last bits, and the conservative
  inequality is an ``<=`` against exactly that sum.
* :class:`~accretion.routing.breaker_inputs.BreakerSampler` joins experience records to
  verification results, picks the *latest* promotion report and takes the most recent slice of
  a node class's history. Three orderings, one frozen :class:`BreakerInput`, compared field by
  field.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already written
to, and no test asserts on a global row count. Nothing here carries an acceptance marker.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import (
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TaskType,
)
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    DecisionType,
    ExperienceRecord,
    IndependentVerificationResult,
    NodeContract,
    RouterModelVersion,
    RouterPromotionReport,
    RouterTrainingSnapshot,
    RoutingDecisionReceipt,
    VerificationState,
)
from accretion.experience.models import (
    Experience,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore, StateStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.bandit import (
    BASELINE_COST_LCB_LABEL,
    COST_UCB_LABEL,
    NODE_CLASS_LABEL,
    LedgerRegistry,
)
from accretion.routing.breaker_inputs import BreakerSampler, BreakerSamplerConfig
from accretion.routing.train import LearnedPredictorLoader

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"

NODE_CLASS = "AGENT"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted to this run's ids and re-sealed."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def config(router_version_id: str | None) -> BreakerSamplerConfig:
    return BreakerSamplerConfig(
        router_version_id=router_version_id,
        serving_versions={"FAKE": "2.0.0"},
        policy_snapshot_resolved=True,
        false_acceptance_ceiling=0.5,
        coverage_floor=0.5,
        max_ece=0.1,
        delta_ni=0.0,
        recent_window=4,
    )


async def seed_project(store: StateStore, project_id: str) -> None:
    await store.create_project(
        Project(
            project_id=project_id,
            name=f"M7 parity {project_id}",
            repository_path=Path("/tmp/accretion-v04-m7-parity"),
        )
    )


async def seed_run(store: StateStore, *, project_id: str, task_id: str, run_id: str) -> None:
    """The task and run rows ``experiences`` keys into under PostgreSQL's foreign keys."""

    await store.create_task(
        Task(
            envelope=TaskEnvelope(
                task_id=task_id,
                project_id=project_id,
                objective="Anchor the M7 parity projections.",
                task_type=TaskType.IMPLEMENT,
            )
        )
    )
    await store.create_run(
        Run(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            provider=Provider.FAKE,
            state=RunState.SUCCEEDED,
        )
    )


async def seed_experience(
    store: StateStore, *, experience_id: str, project_id: str, task_id: str, run_id: str
) -> None:
    """The v0.2 P7 experience an ``ExperienceRecord`` of that id projects (ADR-054 b).

    Written here rather than imported from ``tests/test_v04_m0_store.py`` because that helper
    pins the fixture project, and every id in this file is uuid-suffixed so the same rows can
    be written twice into one database. The task and the run are passed in because
    ``experiences`` keys into both in PostgreSQL and the two backends must be handed the same
    ids for the projections to compare equal.
    """

    await store.save_experience(
        Experience(
            experience_id=experience_id,
            project_id=project_id,
            repository_identity=digest(project_id),
            task_id=task_id,
            task_type=TaskType.IMPLEMENT,
            task_family="python-service",
            source_kind=ExperienceSourceKind.RUN,
            source_run_id=run_id,
            source_commit="b" * 40,
            architecture_version="2.0",
            manifest_digest=digest(f"manifest-{experience_id}"),
            policy_digest=digest(f"policy-{experience_id}"),
            verifier_digest=digest(f"verifier-{experience_id}"),
            prompt_digest=digest(f"prompt-{experience_id}"),
            context_digest=digest(f"context-{experience_id}"),
            tool_profile_digest=digest(f"tools-{experience_id}"),
            provider=Provider.FAKE,
            runtime_model="fake",
            runtime_version="test",
            trust=ExperienceTrust.HIGH,
            polarity=ExperiencePolarity.POSITIVE,
            outcome="VERIFIED_SUCCESS",
            content_digest=digest(f"experience-{experience_id}"),
        ),
        (
            TrajectorySegment(
                segment_id=new_id("trajectory_segment"),
                experience_id=experience_id,
                ordinal=1,
                kind=TrajectorySegmentKind.WORKFLOW_PATH,
                content={"nodes": ["plan", "act", "verify"]},
                content_digest=digest(f"segment-{experience_id}"),
            ),
        ),
        ExperienceEmbedding(
            embedding_id=new_id("experience_embedding"),
            experience_id=experience_id,
            input_digest=digest(f"embedding-{experience_id}"),
            vector=[1.0] + [0.0] * 383,
        ),
    )


def explore_receipt(
    *,
    workspace_id: str,
    project_id: str,
    cost_ucb: str,
    baseline_cost_lcb: str,
    node_class: str = NODE_CLASS,
    decision_type: DecisionType = DecisionType.EXPLORE,
) -> RoutingDecisionReceipt:
    """A committed decision carrying the three labels the ledger is rebuilt from."""

    return build(
        RoutingDecisionReceipt,
        workspace_id=workspace_id,
        project_id=project_id,
        routing_request_id=new_id("routing_request"),
        decision_type=decision_type.value,
        selected_configuration_id=new_id("execution_configuration"),
        selected_configuration_hash=digest(f"configuration-{cost_ucb}-{node_class}"),
        selection_propensity=0.4,
        labels={
            NODE_CLASS_LABEL: node_class,
            COST_UCB_LABEL: cost_ucb,
            BASELINE_COST_LCB_LABEL: baseline_cost_lcb,
        },
    )


# ------------------------------------------------------------------- parity


async def test_both_backends_rebuild_the_same_exploration_ledger_from_receipts() -> None:
    """ADR4-M7-003's replay, over rows each backend returns in its own order.

    The receipts are written in *descending* id order on purpose, so "the backend's order" and
    "the ledger's order" are demonstrably different: a registry that summed in store order
    would still produce the right count here and could produce a different float, and the
    conservative inequality is an ``<=`` against that float. Compared as whole snapshots — the
    entries, their charges and both cumulative sums — rather than on the totals alone, because
    two ledgers can agree on a sum while disagreeing about which exploration cost what.

    Exploitation and a valid independently scoped receipt do not charge this ledger.
    A receipt with unreadable charge labels instead makes accounting unavailable on both
    backends; it must not become an empty budget by being skipped.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    project_id = f"prj_{marker}"
    try:
        charged = [
            explore_receipt(
                workspace_id=workspace_id,
                project_id=project_id,
                cost_ucb=str(0.10 + index / 100),
                baseline_cost_lcb=str(0.30 + index / 100),
            )
            for index in range(4)
        ]
        skipped = [
            explore_receipt(
                workspace_id=workspace_id,
                project_id=project_id,
                cost_ucb="0.9",
                baseline_cost_lcb="0.9",
                decision_type=DecisionType.EXPLOIT,
            ),
            explore_receipt(
                workspace_id=workspace_id,
                project_id=project_id,
                cost_ucb="0.9",
                baseline_cost_lcb="0.9",
                node_class="VERIFIER",
            ),
            explore_receipt(
                workspace_id=workspace_id,
                project_id=project_id,
                cost_ucb="not-a-number",
                baseline_cost_lcb="0.9",
            ),
        ]
        unreadable = skipped.pop()
        node = build(NodeContract, workspace_id=workspace_id,
                     project_id=project_id, node_kind=NODE_CLASS)
        other_node = build(NodeContract, workspace_id=workspace_id,
                           project_id=project_id, node_kind="VERIFIER")
        receipts = []
        for row in [*charged, *skipped]:
            payload = row.model_dump(mode="python")
            payload["node_contract_hash"] = (
                other_node.immutable_hash if row.labels.get(NODE_CLASS_LABEL) == "VERIFIER"
                else node.immutable_hash
            )
            payload["content_hash"] = ""
            receipts.append(RoutingDecisionReceipt.model_validate(payload))

        snapshots: list[dict[str, object]] = []
        for store in (memory, postgres):
            await seed_project(store, project_id)
            await store.put_node_contract(node)
            await store.put_node_contract(other_node)
            # Descending, so neither backend's natural order is the ledger's.
            for receipt in sorted(receipts, key=lambda item: item.contract_id, reverse=True):
                await store.put_routing_receipt(receipt)
            ledger = await LedgerRegistry(store).ledger(
                workspace_id=workspace_id, node_class=NODE_CLASS
            )
            snapshots.append(ledger.snapshot())
            await store.put_routing_receipt(unreadable)
            with pytest.raises(ValueError, match="EXPLORATION_ACCOUNTING_UNAVAILABLE"):
                await LedgerRegistry(store).ledger(
                    workspace_id=workspace_id, node_class=NODE_CLASS
                )

        assert snapshots[0] == snapshots[1]
        assert snapshots[0]["explore_count"] == 4
        assert [entry["receipt_id"] for entry in snapshots[0]["entries"]] == sorted(  # type: ignore[index,union-attr]
            item.contract_id for item in charged
        )
    finally:
        await engine.dispose()


async def test_both_backends_sample_the_same_breaker_input_from_the_same_rows() -> None:
    """One frozen ``BreakerInput`` out of four lists, compared field by field.

    Every field in it is a reduction over rows the two backends order differently: the
    false-acceptance rate and the coverage come from a *window* of a node class's most recent
    executions, the cohorts from the *latest* of two promotion reports, and the validated
    windows from the training snapshot the active version pins. Comparing the whole dataclass
    rather than a chosen field is what makes this a parity test rather than a spot check —
    ``BreakerInput`` is frozen and its equality is structural, so a mapping that came back in
    another order with another content would fail here without anyone having had to predict
    which one it would be.

    Six executions with a window of four: the two oldest were conflicted and must not be in
    the sample on either backend, which is the assertion that the ordering is a *sort* and not
    a slice of whatever came back.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    project_id = f"prj_{marker}"
    artifacts = ArtifactStore(Path("/tmp") / f"accretion-m7-parity-{marker}")
    try:
        snapshot = build(
            RouterTrainingSnapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            provider_version_boundaries={"FAKE": "2.0.0"},
        )
        version = build(
            RouterModelVersion,
            workspace_id=workspace_id,
            project_id=None,
            scope="TEAM_WORKSPACE",
            status="ACTIVE",
            training_snapshot_id=snapshot.contract_id,
        )
        reports = [
            build(
                RouterPromotionReport,
                workspace_id=workspace_id,
                project_id=None,
                created_at=created_at,
                candidate_version=version.contract_id,
                cohort_results=[
                    {
                        "cohort_id": cohort_id,
                        "description": f"cohort {cohort_id}",
                        "sample_size": 12,
                        "critical": True,
                        "comparison": {
                            "metric_id": "verified_success",
                            "baseline_value": 0.75,
                            "candidate_value": 0.7,
                            "delta": -0.05,
                            "delta_lower_bound": -0.25,
                            "delta_upper_bound": 0.0,
                            "passed": False,
                        },
                    }
                ],
            )
            for created_at, cohort_id in (
                (datetime(2026, 6, 1, tzinfo=UTC), "stale"),
                (datetime(2026, 7, 1, tzinfo=UTC), "correctness"),
            )
        ]
        # One task and one run per projection: `experiences` is keyed by
        # `(source_run_id, source_kind)`, so six projections of one run would be six
        # conflicting claims about one terminal outcome and the store refuses them.
        executions = [
            (
                new_id("execution_instance"),
                new_id("experience"),
                new_id("task"),
                new_id("run"),
                index < 2,
                datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=index),
            )
            for index in range(6)
        ]
        conflict_id = new_id("independent_verification_result")

        sampled = []
        for store in (memory, postgres):
            await seed_project(store, project_id)
            await store.put_router_training_snapshot(snapshot)
            await store.put_router_model_version(version)
            for report in reversed(reports):
                await store.put_router_promotion_report(report)
            for instance, record_id, task_id, run_id, conflicted, created_at in reversed(
                executions
            ):
                await seed_run(
                    store, project_id=project_id, task_id=task_id, run_id=run_id
                )
                await seed_experience(
                    store,
                    experience_id=record_id,
                    project_id=project_id,
                    task_id=task_id,
                    run_id=run_id,
                )
                await store.put_experience_record(
                    build(
                        ExperienceRecord,
                        contract_id=record_id,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        created_at=created_at,
                        source_node_execution_id=instance,
                        local_verification_status=VerificationState.PASS.value,
                    )
                )
                await store.put_verification_result(
                    build(
                        IndependentVerificationResult,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        execution_instance_id=instance,
                        status=VerificationState.PASS.value,
                        conflict_refs=[conflict_id] if conflicted else [],
                    )
                )
            sampler = BreakerSampler(store, LearnedPredictorLoader(store, artifacts))
            sampled.append(
                await sampler.sample(
                    workspace_id=workspace_id,
                    node_class=NODE_CLASS,
                    config=config(version.contract_id),
                )
            )

        assert sampled[0] == sampled[1]
        assert sampled[0].false_acceptance_rate_recent == 0.0
        assert sampled[0].cohort_lcbs == {"correctness": 0.5}
        assert sampled[0].version_boundaries == {"FAKE": ("2.0.0", "2.0.0")}
    finally:
        await engine.dispose()
