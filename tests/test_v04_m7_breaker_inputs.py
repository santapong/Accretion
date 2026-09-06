"""Sampling §15.3's six numbers out of a real store, and failing closed where they are absent.

``tests/test_v04_m7_breakers.py`` exercises the six predicates at both sides of every
threshold over frozen inputs. This file is the other half of the same claim: that the numbers
handed to those predicates are the workspace's own evidence and not a plausible default. A
sampler is the easiest place in this milestone to be quietly wrong — every failure mode here
produces a *valid* ``BreakerInput`` that reads healthy — so each test below states the row it
wrote and the field it expects to see move.

Two properties get most of the attention.

**A missing measurement is not a passing measurement.** An unreadable calibration report, a
version with no training snapshot and a store that will not answer are three different
absences, and all three have to come out on the tripping side of their own thresholds. The
temptation in each case is a tolerant default — ``0.0`` for an error rate, an empty window
that nothing is compared against — and a tolerant default is how exploration gets switched on
during exactly the incident that should have stopped it.

**The window is one node class's own work.** Verification results carry no node kind, so the
join goes through the experience records, and a sampler that skipped that join would let a
busy healthy class launder a sick one's false-acceptance rate. The first test writes two
classes with opposite histories into one workspace and asks for each.

There is no ``conftest.py``: the golden ``build`` helper and the experience seeding are
imported from ``tests/test_v04_m0_store.py`` and every row below is written through the real
store.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test_v04_m0_store import FIXTURE_PROJECT_ID, build, digest, new_store, seed_experience

from accretion.contracts import Project
from accretion.contracts.routing import (
    ExperienceRecord,
    IndependentVerificationResult,
    RouterModelVersion,
    RouterPromotionReport,
    RouterTrainingSnapshot,
    VerificationState,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.breaker_inputs import (
    UNCALIBRATED_ECE,
    BreakerSampler,
    BreakerSamplerConfig,
)
from accretion.routing.breakers import exploration_allowed
from accretion.routing.calibration import (
    BootstrapInterval,
    CalibrationMethod,
    CalibrationReport,
    ReliabilityBin,
)
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    CALIBRATION_REPORT_LABEL,
    LearnedPredictorLoader,
)

WORKSPACE_ID = "wsp_01M7BREAKERINPUTS0000000000"

CONFIG = BreakerSamplerConfig(
    router_version_id=None,
    serving_versions={"FAKE": "2.0.0"},
    policy_snapshot_resolved=True,
    false_acceptance_ceiling=0.5,
    coverage_floor=0.5,
    max_ece=0.1,
    delta_ni=0.0,
    recent_window=50,
)
"""The thresholds every test below varies from, so a case names only what it changed."""


async def setup_sampler(tmp_path: Path) -> tuple[MemoryStore, BreakerSampler, ArtifactStore]:
    """A store with the fixture project, and the sampler reading it through the real loader."""

    store = await new_store()
    artifacts = ArtifactStore(tmp_path / "router-artifacts")
    return store, BreakerSampler(store, LearnedPredictorLoader(store, artifacts)), artifacts


async def record_execution(
    store: MemoryStore,
    *,
    node_kind: str,
    status: VerificationState,
    conflicts: int = 0,
    coverage: float = 1.0,
    claims: int = 1,
    minute: int = 0,
) -> str:
    """One node execution of ``node_kind``, with the verification verdict it received.

    Both rows are written — the experience record that says which class the execution belonged
    to and the independent verification result that says how it was judged — because the join
    between them is the thing under test. Returns the execution instance id the two share.
    """

    project_id = new_id("project")
    await store.create_project(
        Project(
            project_id=project_id,
            name="M7 breaker inputs",
            repository_path=Path("/tmp/accretion-v04-m7"),
        )
    )
    instance = new_id("execution_instance")
    signature = build(ExperienceRecord).contract_signature.model_dump(mode="python")
    signature["node_kind"] = node_kind
    record = build(
        ExperienceRecord,
        workspace_id=WORKSPACE_ID,
        project_id=project_id,
        source_node_execution_id=instance,
        contract_signature=signature,
        local_verification_status=status.value,
        created_at=datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=minute),
    )
    await seed_experience(store, record.contract_id)
    await store.put_experience_record(record)
    await store.put_verification_result(
        build(
            IndependentVerificationResult,
            workspace_id=WORKSPACE_ID,
            project_id=project_id,
            execution_instance_id=instance,
            status=status.value,
            conflict_refs=[new_id("independent_verification_result") for _ in range(conflicts)],
            claim_results=[
                {
                    "claim_id": f"claim-{index}",
                    "status": status.value,
                    "coverage": coverage,
                }
                for index in range(claims)
            ],
        )
    )
    return instance


async def promoted_version(
    store: MemoryStore,
    artifacts: ArtifactStore,
    *,
    ece: float,
    boundaries: dict[str, str] | None = None,
    seal_report: bool = True,
) -> RouterModelVersion:
    """A router version with a sealed calibration report and a training snapshot behind it.

    Sealed through the artifact store under the digest the version pins, because
    ``LearnedPredictorLoader`` verifies that pin and the sampler goes through the loader on
    purpose: a report read straight out of the bytes could no longer hash to its digest and
    would still satisfy a safety breaker.
    """

    snapshot = await store.put_router_training_snapshot(
        build(
            RouterTrainingSnapshot,
            workspace_id=WORKSPACE_ID,
            project_id=FIXTURE_PROJECT_ID,
            provider_version_boundaries=boundaries if boundaries is not None else {},
        )
    )
    report = CalibrationReport(
        method=CalibrationMethod.ISOTONIC,
        alpha=0.05,
        conformal_quantile=0.9,
        ece_10bin=ece,
        brier=0.1,
        bins=[
            ReliabilityBin(
                lower=0.0, upper=1.0, count=40, mean_predicted=0.8, observed_rate=0.8
            )
        ],
        holdout_coverage=0.9,
        bootstrap_ece_interval=BootstrapInterval(
            lower=ece, upper=ece, level=0.95, bootstraps=200
        ),
        seed=7,
        feature_schema_version="1.0.0",
    )
    calibration_digest = (
        artifacts.save(report.model_dump_json().encode())
        if seal_report
        else digest("a calibration report nobody sealed")
    )
    return await store.put_router_model_version(
        build(
            RouterModelVersion,
            workspace_id=WORKSPACE_ID,
            project_id=None,
            scope="TEAM_WORKSPACE",
            status="ACTIVE",
            training_snapshot_id=snapshot.contract_id,
            calibration_artifact_digest=calibration_digest,
            labels={
                ACCEPTANCE_LABEL: digest("holdout"),
                CALIBRATION_REPORT_LABEL: calibration_digest,
            },
        )
    )


class RefusingStore:
    """A store that raises on the one read a test names, and counts what it was asked.

    Hand-written rather than mocked: it forwards every other call to a real
    :class:`~accretion.persistence.store.MemoryStore`, so what is under test is a sampler
    facing one broken read rather than a sampler facing an object with no behaviour at all.
    """

    def __init__(self, inner: MemoryStore, *, failing: str) -> None:
        self.inner = inner
        self.failing = failing
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.inner, name)
        if not callable(attribute):
            return attribute

        async def guarded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            if name == self.failing:
                raise RuntimeError(f"{name} is unavailable")
            return await attribute(*args, **kwargs)

        return guarded


# --------------------------------------------------------------------------------------


async def test_the_false_acceptance_rate_is_the_node_classs_own_and_not_the_workspaces(
    tmp_path: Path,
) -> None:
    """Two classes with opposite histories in one workspace, sampled one at a time.

    ``AGENT`` accepted three executions and a conflict was recorded against two of them;
    ``VERIFY`` accepted three and none were contradicted. A sampler that pooled the workspace
    would report 2/6 for both and would put ``AGENT`` under a ceiling of 0.5 that it is in fact
    over — the exact direction that matters, since the whole point of the breaker is to stop
    exploring a class that is being wrongly accepted.

    The denominator is asserted through the second class too: the *rejections* written for
    ``VERIFY`` do not dilute its rate, because a false acceptance is an acceptance that turned
    out wrong.
    """

    store, sampler, _ = await setup_sampler(tmp_path)
    for index in range(3):
        await record_execution(
            store,
            node_kind="AGENT",
            status=VerificationState.PASS,
            conflicts=1 if index < 2 else 0,
            minute=index,
        )
    for index in range(3):
        await record_execution(
            store, node_kind="VERIFIER", status=VerificationState.PASS, minute=10 + index
        )
    await record_execution(
        store, node_kind="VERIFIER", status=VerificationState.FAIL, minute=20
    )

    agent = await sampler.sample(
        workspace_id=WORKSPACE_ID, node_class="AGENT", config=CONFIG
    )
    verify = await sampler.sample(
        workspace_id=WORKSPACE_ID, node_class="VERIFIER", config=CONFIG
    )

    assert agent.false_acceptance_rate_recent == 2 / 3
    assert verify.false_acceptance_rate_recent == 0.0
    assert agent.verification_coverage_recent == 1.0
    assert verify.verification_coverage_recent == 1.0


async def test_a_verifier_that_judged_no_claims_is_recorded_as_covering_nothing(
    tmp_path: Path,
) -> None:
    """A verdict about no claims covered no claims, and the floor is where that shows up.

    Skipping such a result instead — the tempting simplification, since it has no coverage to
    average — would let one real verification stand for a window of empty verdicts and report
    full coverage for a class that is barely being checked at all.
    """

    store, sampler, _ = await setup_sampler(tmp_path)
    await record_execution(store, node_kind="AGENT", status=VerificationState.PASS, claims=1)
    for minute in (1, 2):
        await record_execution(
            store,
            node_kind="AGENT",
            status=VerificationState.PASS,
            claims=0,
            minute=minute,
        )

    sampled = await sampler.sample(
        workspace_id=WORKSPACE_ID, node_class="AGENT", config=CONFIG
    )

    assert sampled.verification_coverage_recent == 1 / 3
    allowed, tripped = exploration_allowed(
        replace(
            sampled,
            ece_recent=0.0,
            version_boundaries={"FAKE": ("2.0.0", "2.0.0")},
        )
    )
    assert allowed is False
    assert tripped == ["verification_coverage_drop"]


async def test_an_unreadable_calibration_report_is_uncalibrated_rather_than_calibrated(
    tmp_path: Path,
) -> None:
    """Three absences, one answer, and the answer is on the tripping side.

    No active version, a version whose report was never sealed, and a version whose report is
    sealed and readable are the three cases; the first two report ``UNCALIBRATED_ECE`` and the
    third reports the number the report actually carries. A tolerant default in either of the
    first two would let a router whose bounds cannot be shown to mean anything be the router an
    exploration is chosen against.
    """

    store, sampler, artifacts = await setup_sampler(tmp_path)

    nothing_active = await sampler.sample(
        workspace_id=WORKSPACE_ID, node_class="AGENT", config=CONFIG
    )
    assert nothing_active.ece_recent == UNCALIBRATED_ECE

    unsealed = await promoted_version(store, artifacts, ece=0.01, seal_report=False)
    blind = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, router_version_id=unsealed.contract_id),
    )
    assert blind.ece_recent == UNCALIBRATED_ECE

    sealed = await promoted_version(store, artifacts, ece=0.0125)
    read = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, router_version_id=sealed.contract_id),
    )
    assert read.ece_recent == 0.0125


async def test_the_validated_window_is_the_training_snapshots_boundary_and_nothing_wider(
    tmp_path: Path,
) -> None:
    """A snapshot records one version per provider, so the window is that version alone.

    Widening it to an open interval is the plausible mistake — a single string looks like a
    lower bound — and it would validate every version above the one the evidence was produced
    under, which is precisely the drift OQ-415 names. The serving version is moved by one minor
    release and has to fall outside.
    """

    store, sampler, artifacts = await setup_sampler(tmp_path)
    version = await promoted_version(
        store, artifacts, ece=0.0, boundaries={"FAKE": "2.0.0"}
    )

    matching = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, router_version_id=version.contract_id),
    )
    assert matching.version_boundaries == {"FAKE": ("2.0.0", "2.0.0")}
    assert exploration_allowed(matching)[1] == ["verification_coverage_drop"]

    drifted = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(
            CONFIG,
            router_version_id=version.contract_id,
            serving_versions={"FAKE": "2.1.0"},
        ),
    )
    assert exploration_allowed(drifted)[1] == [
        "unvalidated_version_drift",
        "verification_coverage_drop",
    ]


async def test_a_version_with_no_training_snapshot_leaves_every_component_unvalidated(
    tmp_path: Path,
) -> None:
    """No declared boundary is not a permissive boundary. It is no validation at all."""

    store, sampler, artifacts = await setup_sampler(tmp_path)
    version = await promoted_version(store, artifacts, ece=0.0, boundaries={})

    sampled = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, router_version_id=version.contract_id),
    )

    assert sampled.version_boundaries == {}
    assert "unvalidated_version_drift" in exploration_allowed(sampled)[1]


async def test_only_the_critical_cohorts_of_the_latest_report_reach_the_breaker(
    tmp_path: Path,
) -> None:
    """Three claims in one, because they are three ways for one mapping to be wrong.

    The *latest* report for the version wins, since a version can be evaluated more than once
    and the older verdict describes evidence that has since been added to. Only cohorts marked
    ``critical`` are passed, which is what keeps OQ-413's open list out of the predicate. And
    the value passed is a *lower bound* — ``baseline_value + delta_lower_bound`` — because
    §15.3's third condition compares a bound against the baseline, and a point estimate handed
    in as a bound would let a cohort that regressed inside its own interval pass.
    """

    store, sampler, artifacts = await setup_sampler(tmp_path)
    version = await promoted_version(store, artifacts, ece=0.0)
    await store.put_router_promotion_report(
        _report(
            version.contract_id,
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            cohorts=[("stale", True, 0.9, 0.0)],
        )
    )
    await store.put_router_promotion_report(
        _report(
            version.contract_id,
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
            cohorts=[("correctness", True, 0.8, -0.3), ("cosmetic", False, 0.4, -0.9)],
        )
    )

    sampled = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, router_version_id=version.contract_id),
    )

    assert sampled.cohort_lcbs == {"correctness": 0.5}
    assert sampled.cohort_baselines == {"correctness": 0.8}
    assert "critical_cohort_regression" in exploration_allowed(sampled)[1]


def _report(
    version_id: str,
    *,
    created_at: datetime,
    cohorts: list[tuple[str, bool, float, float]],
) -> RouterPromotionReport:
    """A promotion report for ``version_id`` carrying the named cohorts and their intervals."""

    return build(
        RouterPromotionReport,
        workspace_id=WORKSPACE_ID,
        project_id=None,
        created_at=created_at,
        candidate_version=version_id,
        cohort_results=[
            {
                "cohort_id": cohort_id,
                "description": f"cohort {cohort_id}",
                "sample_size": 40,
                "critical": critical,
                "comparison": {
                    "metric_id": "verified_success",
                    "baseline_value": baseline,
                    "candidate_value": baseline + lower,
                    "delta": lower,
                    "delta_lower_bound": lower,
                    "delta_upper_bound": 0.0,
                    "passed": lower >= 0.0,
                },
            }
            for cohort_id, critical, baseline, lower in cohorts
        ],
    )


async def test_a_store_that_will_not_answer_disables_exploration_on_every_axis(
    tmp_path: Path,
) -> None:
    """§15.3's sixth condition, and the five silences a bare ``audit_probe_ok`` would hide.

    A sampler that reported only ``audit_probe_ok=False`` would be claiming to have measured
    five other things it never read, and a later reader of the verdict would take those five
    silences for five clean bills of health. Every field therefore comes back on the tripping
    side, and the composite names all six — which is the assertion, because "exploration is
    off" is already true from the sixth alone and would pass a weaker check.
    """

    store, _, artifacts = await setup_sampler(tmp_path)
    await record_execution(store, node_kind="AGENT", status=VerificationState.PASS)
    refusing = RefusingStore(store, failing="list_verification_results")
    sampler = BreakerSampler(refusing, LearnedPredictorLoader(refusing, artifacts))  # type: ignore[arg-type]

    sampled = await sampler.sample(
        workspace_id=WORKSPACE_ID, node_class="AGENT", config=CONFIG
    )

    assert sampled.audit_probe_ok is False
    assert sampled.ece_recent == UNCALIBRATED_ECE
    assert sampled.false_acceptance_rate_recent == 1.0
    assert sampled.verification_coverage_recent == 0.0
    assert sampled.version_boundaries == {}
    allowed, tripped = exploration_allowed(sampled)
    assert allowed is False
    assert tripped == [
        "false_acceptance_alert",
        "calibration_exceeded",
        "unvalidated_version_drift",
        "verification_coverage_drop",
        "policy_or_audit_unavailable",
    ]
    assert "list_verification_results" in refusing.calls


async def test_the_window_holds_the_most_recent_executions_and_not_the_first(
    tmp_path: Path,
) -> None:
    """A recovered class is not held to the history it recovered from.

    ``recent_window`` is a bound on how far back the breakers look, and a window taken from the
    *front* of the list would freeze a workspace's first bad week into every later decision.
    Twelve executions are written oldest-first, the first six conflicted and the last six clean,
    and a window of six has to see only the clean ones.
    """

    store, sampler, _ = await setup_sampler(tmp_path)
    for index in range(12):
        await record_execution(
            store,
            node_kind="AGENT",
            status=VerificationState.PASS,
            conflicts=1 if index < 6 else 0,
            minute=index,
        )

    recent = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, recent_window=6),
    )
    everything = await sampler.sample(
        workspace_id=WORKSPACE_ID,
        node_class="AGENT",
        config=replace(CONFIG, recent_window=12),
    )

    assert recent.false_acceptance_rate_recent == 0.0
    assert everything.false_acceptance_rate_recent == 0.5
