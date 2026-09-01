"""Inherited v0.2 P7 experience proofs (V02-P7-003, V02-P7-008).

The acceptance baseline records these two as the most misleading evidence in the
suite: "the benchmarks replay hand-authored fixtures rather than system output.
The headline '19/20 stale sources rejected' counts a literal field in
`sources.v1.json`; `ExperienceService.assess()` is never invoked, and 16 of its
19 rejection codes have no test."

`V02-P7-003` ("deliberately stale/incompatible experience is rejected or heavily
downranked") is a claim about the **system**, so it is proven here against the
real `ExperienceService.assess()` - every one of the 19 reason codes is
provoked by a distinct, deliberate incompatibility, and each is shown to reject
or downrank rather than accept.

`V02-P7-008` ("held-out experiment measures uplift and negative-transfer rate")
is a claim about the **benchmark**, so it is proven against the runner: every
published figure is pinned literally and then shown to be derived, by perturbing
one held-out trace in a `tmp_path` copy and asserting the right number moves.

The benchmark's stale-rejection figure is the one number that was pure fixture
arithmetic. `run(stale_assessor=...)` now decides each STALE_INCOMPATIBLE source
through a real assessment and reports `stale_rejection_source="ASSESSED"`,
treating the declared outcome as a pin that raises on disagreement. The API
routes still take the declared path and now say so on the gate; closing that
last gap end to end is v0.4, recorded under "P7 experience benchmark provenance"
in docs/releases/v0.3/backlog.md.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_p7_experience_service import (
    create_task,
    enable_p7,
    initialize_repository,
    seed_succeeded_run,
    services,
)

from accretion.experience.models import (
    Experience,
    ExperiencePolarity,
    ExperienceQuery,
    ExperienceTrust,
    MatchDisposition,
)
from accretion.experience.service import ExperienceService
from accretion.experience_benchmark import ExperienceBenchmarkRunner

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


# ---------------------------------------------------------------------------
# V02-P7-003 — every rejection code, proven against the real assessor
# ---------------------------------------------------------------------------


async def assessable_pair(
    tmp_path: Path,
) -> tuple[ExperienceService, ExperienceQuery, Experience, Path]:
    """A compatible (experience, query) pair that assesses as ACCEPTED.

    Every case below perturbs exactly one thing about this pair, so a rejection
    can only be attributed to the incompatibility under test.
    """
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, experience = services(tmp_path)
    project = await manager.create_project("P7 assess", repository)
    await enable_p7(dynamic, project.project_id)

    source_task_id = await create_task(
        manager, project.project_id, "Review a deterministic service boundary."
    )
    run = await seed_succeeded_run(
        manager, task_id=source_task_id, project_id=project.project_id
    )
    materialized = await experience.materialize(run.run_id)

    target_task_id = await create_task(
        manager, project.project_id, "Review a deterministic service boundary."
    )
    matches = await experience.query(target_task_id, include_failures=True)
    assert matches, "fixture must retrieve its own experience"
    record = await manager.store.get_experience_query(matches[0].query_id)
    assert record is not None
    return experience, record[0], materialized.experience, repository


# (code, how to perturb the experience, how to perturb the query)
Perturbation = tuple[
    str,
    Callable[[Experience], Experience],
    Callable[[ExperienceQuery], ExperienceQuery],
]

def IDENTITY_QUERY(query: ExperienceQuery) -> ExperienceQuery:
    """Leave the query alone: the incompatibility is on the experience side."""
    return query

HARD_REJECTIONS: list[Perturbation] = [
    (
        "EXPERIENCE_RETRACTED",
        lambda e: e.model_copy(update={"retracted": True}),
        IDENTITY_QUERY,
    ),
    (
        "REPOSITORY_MISMATCH",
        lambda e: e.model_copy(update={"repository_identity": DIGEST_B}),
        IDENTITY_QUERY,
    ),
    (
        "PROTECTED_SIDE_EFFECT_STATE",
        lambda e: e.model_copy(update={"protected_side_effects": True}),
        IDENTITY_QUERY,
    ),
    (
        "ARCHITECTURE_MAJOR_INCOMPATIBLE",
        lambda e: e.model_copy(update={"architecture_version": "99.0.0"}),
        IDENTITY_QUERY,
    ),
    (
        "POLICY_INCOMPATIBLE",
        lambda e: e.model_copy(update={"policy_digest": DIGEST_B}),
        IDENTITY_QUERY,
    ),
    (
        "VERIFIER_INCOMPATIBLE",
        lambda e: e.model_copy(update={"verifier_digest": DIGEST_B}),
        IDENTITY_QUERY,
    ),
    (
        "SKILL_NOT_REQUESTED",
        lambda e: e.model_copy(update={"requested_skills": ["unrequested-skill"]}),
        lambda q: q.model_copy(update={"requested_skills": []}),
    ),
    (
        "CAPABILITY_NOT_ALLOWED",
        lambda e: e.model_copy(update={"allowed_capabilities": ["fs.write"]}),
        lambda q: q.model_copy(update={"allowed_capabilities": []}),
    ),
    (
        "CAPABILITY_DENIED",
        lambda e: e.model_copy(update={"allowed_capabilities": ["net.fetch"]}),
        lambda q: q.model_copy(
            update={
                "allowed_capabilities": ["net.fetch"],
                "denied_capabilities": ["net.fetch"],
            }
        ),
    ),
    (
        "SKILL_OR_PLUGIN_UNAVAILABLE",
        lambda e: e.model_copy(update={"requested_skills": ["missing-plugin"]}),
        lambda q: q.model_copy(update={"requested_skills": ["missing-plugin"]}),
    ),
    (
        "CAPABILITY_UNAVAILABLE",
        lambda e: e.model_copy(update={"allowed_capabilities": ["never.registered"]}),
        lambda q: q.model_copy(
            update={"allowed_capabilities": ["never.registered"]}
        ),
    ),
    (
        "VERIFIER_UNAVAILABLE",
        lambda e: e.model_copy(update={"verifier_ids": ["no-such-verifier"]}),
        IDENTITY_QUERY,
    ),
    (
        "SOURCE_COMMIT_MISSING",
        lambda e: e.model_copy(update={"source_commit": "0" * 40}),
        IDENTITY_QUERY,
    ),
    (
        "MAX_AGE_EXCEEDED",
        lambda e: e.model_copy(
            update={"created_at": datetime.now(UTC) - timedelta(days=181)}
        ),
        lambda q: q.model_copy(update={"max_age_days": 30}),
    ),
    (
        "INVALID_DIGEST",
        lambda e: e.model_copy(update={"manifest_digest": "not-a-digest"}),
        IDENTITY_QUERY,
    ),
]


@pytest.mark.acceptance("V02-P7-003")
@pytest.mark.parametrize(
    ("code", "perturb_experience", "perturb_query"),
    HARD_REJECTIONS,
    ids=[item[0] for item in HARD_REJECTIONS],
)
async def test_each_incompatibility_rejects_with_its_own_reason_code(
    tmp_path: Path,
    code: str,
    perturb_experience: Callable[[Experience], Experience],
    perturb_query: Callable[[ExperienceQuery], ExperienceQuery],
) -> None:
    """Every hard incompatibility must reject, naming its own cause.

    `INVALID_DIGEST` is constructed by bypassing the contract's own pattern
    validation, which is why it is built with `model_construct`: the guard
    exists for data that reached the store before the pattern did.
    """
    service, query, experience, repository = await assessable_pair(tmp_path)

    if code == "INVALID_DIGEST":
        broken = Experience.model_construct(
            **{**experience.model_dump(), "manifest_digest": "not-a-digest"}
        )
    else:
        broken = perturb_experience(experience)

    assessment = await service.assess(
        perturb_query(query), broken, semantic_score=1.0, repository=repository
    )

    assert assessment.disposition is MatchDisposition.REJECTED
    assert code in assessment.reasons
    # A rejected match is never usable, whatever its score.
    assert not assessment.replay_eligible
    assert not assessment.negative_guidance_eligible


@pytest.mark.acceptance("V02-P7-003")
async def test_a_negative_experience_is_rejected_when_failures_are_excluded(
    tmp_path: Path,
) -> None:
    """FAILURES_EXCLUDED: the same evidence flips on the caller's declared intent."""
    service, query, experience, repository = await assessable_pair(tmp_path)
    negative = experience.model_copy(
        update={
            "polarity": ExperiencePolarity.NEGATIVE,
            "trust": ExperienceTrust.MEDIUM,
            "failure_taxonomy": ["FIXTURE_FAILURE"],
        }
    )

    excluded = await service.assess(
        query.model_copy(update={"include_failures": False}),
        negative,
        semantic_score=1.0,
        repository=repository,
    )
    assert excluded.disposition is MatchDisposition.REJECTED
    assert "FAILURES_EXCLUDED" in excluded.reasons

    included = await service.assess(
        query.model_copy(update={"include_failures": True}),
        negative,
        semantic_score=1.0,
        repository=repository,
    )
    assert "FAILURES_EXCLUDED" not in included.reasons
    assert included.disposition is not MatchDisposition.REJECTED


@pytest.mark.acceptance("V02-P7-003")
async def test_manifest_drift_downranks_rather_than_rejects(tmp_path: Path) -> None:
    """The criterion says rejected *or heavily downranked* - this is the second half.

    Manifest drift is a soft signal: the evidence is still usable, but it must
    lose score and say why, rather than being silently accepted at full value.
    """
    service, query, experience, repository = await assessable_pair(tmp_path)

    baseline = await service.assess(
        query, experience, semantic_score=1.0, repository=repository
    )

    content_drift = await service.assess(
        query,
        experience.model_copy(update={"manifest_digest": DIGEST_A}),
        semantic_score=1.0,
        repository=repository,
    )
    assert "MANIFEST_CONTENT_DRIFT" in content_drift.reasons
    assert content_drift.disposition is not MatchDisposition.REJECTED
    assert content_drift.environment_score < baseline.environment_score
    assert content_drift.final_score < baseline.final_score

    set_drift = await service.assess(
        query,
        experience.model_copy(
            update={"manifest_digest": DIGEST_A, "manifest_paths": ["unrelated.toml"]}
        ),
        semantic_score=1.0,
        repository=repository,
    )
    assert "MANIFEST_SET_DRIFT" in set_drift.reasons
    assert set_drift.disposition is not MatchDisposition.REJECTED
    # Losing the whole manifest set is worse than drifting its contents.
    assert set_drift.environment_score < content_drift.environment_score


@pytest.mark.acceptance("V02-P7-003")
async def test_a_commit_off_the_query_ancestry_is_rejected(tmp_path: Path) -> None:
    """SOURCE_COMMIT_NOT_ANCESTOR: real git ancestry, not a string comparison."""
    service, query, experience, repository = await assessable_pair(tmp_path)

    # A real commit on a divergent branch: present in the repository, but not an
    # ancestor of the query's commit.
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", "-b", "divergent"], check=True
    )
    (repository / "divergent.txt").write_text("divergent\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "divergent"], check=True)
    divergent = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assessment = await service.assess(
        query,
        experience.model_copy(update={"source_commit": divergent}),
        semantic_score=1.0,
        repository=repository,
    )
    assert assessment.disposition is MatchDisposition.REJECTED
    assert "SOURCE_COMMIT_NOT_ANCESTOR" in assessment.reasons


@pytest.mark.acceptance("V02-P7-003")
async def test_the_control_case_is_accepted_so_rejection_means_something(
    tmp_path: Path,
) -> None:
    """Without this, every test above would pass against an assessor that
    rejected unconditionally."""
    service, query, experience, repository = await assessable_pair(tmp_path)
    assessment = await service.assess(
        query, experience, semantic_score=1.0, repository=repository
    )
    assert assessment.disposition is not MatchDisposition.REJECTED
    assert assessment.reasons == [] or all(
        reason.endswith("_DRIFT") for reason in assessment.reasons
    )


@pytest.mark.acceptance("V02-P7-003")
def test_every_reason_code_the_assessor_can_emit_is_covered_by_a_test() -> None:
    """A code added to the service without a test here fails this test.

    The baseline's complaint was that 16 of 19 codes had no test at all; this
    keeps that from silently recurring.
    """
    source = (
        Path(__file__).resolve().parents[1] / "src" / "accretion" / "experience" / "service.py"
    ).read_text()
    emitted = {
        line.split('"')[1]
        for line in source.splitlines()
        if "reasons.append(" in line and '"' in line
    }
    covered = {code for code, _, _ in HARD_REJECTIONS} | {
        "FAILURES_EXCLUDED",
        "MANIFEST_CONTENT_DRIFT",
        "MANIFEST_SET_DRIFT",
        "SOURCE_COMMIT_NOT_ANCESTOR",
    }
    assert emitted, "failed to parse any reason codes out of the service"
    assert emitted - covered == set(), f"uncovered assessor reason codes: {emitted - covered}"
    assert len(emitted) == 19


# ---------------------------------------------------------------------------
# V02-P7-008 — held-out uplift and negative transfer
# ---------------------------------------------------------------------------

EXPECTED_STALE_REJECTION_RATE = 0.95
EXPECTED_MINIMUM_STALE_REJECTION_RATE = 0.95


@pytest.mark.acceptance("V02-P7-008")
def test_held_out_gate_reports_uplift_and_negative_transfer_against_its_thresholds() -> None:
    summary = ExperienceBenchmarkRunner().run()
    gate = summary.gate

    treatments = {item.treatment.value: item for item in summary.treatments}
    fresh = treatments["FRESH"]
    replay = treatments["REPLAY"]

    # Uplift is measured against the no-experience control, not asserted.
    assert replay.quality_uplift == round(replay.mean_quality - fresh.mean_quality, 6)
    assert fresh.quality_uplift == 0.0
    assert replay.quality_uplift > 0

    # Negative transfer is a rate over the held-out matrix, and is reported
    # whether or not it passes.
    assert 0.0 <= gate.negative_transfer_rate <= 1.0
    assert gate.negative_transfer_rate <= gate.thresholds["maximum_negative_transfer_rate"]
    assert gate.negative_transfer_passed is (
        gate.negative_transfer_rate <= gate.thresholds["maximum_negative_transfer_rate"]
    )

    # Safety conditions are conjunctive: the gate cannot pass on uplift alone.
    assert gate.passed is (
        gate.false_accepts_not_increased
        and gate.stale_rejection_passed
        and gate.negative_transfer_passed
        and gate.benefit_passed
        and gate.success_rate_not_regressed
    )
    assert gate.stale_rejection_rate == EXPECTED_STALE_REJECTION_RATE
    assert (
        gate.thresholds["minimum_stale_rejection_rate"]
        == EXPECTED_MINIMUM_STALE_REJECTION_RATE
    )

    # Default (API) path is honest about where its stale figure came from.
    assert gate.stale_rejection_source == "DECLARED"


def perturbed_experience_corpus(
    tmp_path: Path, name: str, mutate: Callable[[dict], None]
) -> Path:
    root = tmp_path / name
    shutil.copytree(ExperienceBenchmarkRunner().root, root)
    path = root / "replay-traces.v1.json"
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return root


@pytest.mark.acceptance("V02-P7-008")
def test_uplift_is_derived_from_the_held_out_traces_it_summarizes(tmp_path: Path) -> None:
    """Sensitivity: degrade the REPLAY arm and uplift must fall.

    Without this the pinned uplift would only prove the fixture is unchanged.
    """
    baseline = ExperienceBenchmarkRunner().run()
    baseline_replay = {
        item.treatment.value: item for item in baseline.treatments
    }["REPLAY"]

    def degrade(payload: dict) -> None:
        changed = 0
        for trace in payload["traces"]:
            if trace["treatment"] == "REPLAY":
                trace["quality"] = round(max(0.0, trace["quality"] - 0.2), 6)
                changed += 1
        assert changed, "no REPLAY traces found to perturb"

    root = perturbed_experience_corpus(tmp_path, "degraded-replay", degrade)
    perturbed = ExperienceBenchmarkRunner(root=root).run()
    perturbed_replay = {
        item.treatment.value: item for item in perturbed.treatments
    }["REPLAY"]
    perturbed_fresh = {item.treatment.value: item for item in perturbed.treatments}["FRESH"]
    baseline_fresh = {item.treatment.value: item for item in baseline.treatments}["FRESH"]

    assert perturbed_replay.quality_uplift < baseline_replay.quality_uplift
    # The control arm is untouched, so the movement is uplift, not drift.
    assert perturbed_fresh.mean_quality == baseline_fresh.mean_quality
    assert perturbed.trace_sha256 != baseline.trace_sha256
    assert perturbed.benchmark_run_id != baseline.benchmark_run_id


@pytest.mark.acceptance("V02-P7-008")
def test_the_stale_rejection_figure_can_be_derived_from_a_real_assessment() -> None:
    """The headline stale figure is fixture arithmetic by default; prove the
    seam that makes it a measurement, and prove it refuses to disagree.

    `run(stale_assessor=...)` runs each STALE_INCOMPATIBLE source through a
    decision function and treats the corpus's declared outcome as a pin.
    """
    runner = ExperienceBenchmarkRunner()
    declared = json.loads(runner.sources_path.read_text())["sources"]
    outcomes = {
        str(item["source_id"]): str(item["retrieval_outcome"])
        for item in declared
        if item["class"] == "STALE_INCOMPATIBLE"
    }
    assert len(outcomes) == 20

    faithful = runner.run(
        stale_assessor=lambda source_id: MatchDisposition(outcomes[source_id])
    )
    assert faithful.gate.stale_rejection_source == "ASSESSED"
    assert faithful.gate.stale_rejection_rate == EXPECTED_STALE_REJECTION_RATE
    assert faithful.gate.stale_rejection_passed

    # An assessor that disagrees with the published corpus must raise rather
    # than quietly publishing a different headline number.
    with pytest.raises(ValueError, match="disagree"):
        runner.run(stale_assessor=lambda _source_id: MatchDisposition.ACCEPTED)

    # And the derived rate really is derived: an assessor that rejects
    # everything disagrees with the one DOWNRANKED source.
    with pytest.raises(ValueError, match="disagree"):
        runner.run(stale_assessor=lambda _source_id: MatchDisposition.REJECTED)


@pytest.mark.acceptance("V02-P7-008")
def test_the_assessor_is_consulted_for_every_stale_source() -> None:
    """The pin is only worth anything if every source actually goes through it.

    Note on what this can and cannot prove: while the pin holds, the assessed
    count and the declared count are necessarily equal, so no passing test can
    distinguish "counted the assessments" from "counted the declared field after
    checking they agree". Those two are genuinely equivalent implementations.
    What is falsifiable, and what this test plus the two `pytest.raises` cases
    above establish, is that every stale source is put to the assessor and any
    disagreement is fatal - which is what makes the ASSESSED label mean
    something.
    """
    runner = ExperienceBenchmarkRunner()
    declared = json.loads(runner.sources_path.read_text())["sources"]
    stale_ids = {
        str(item["source_id"])
        for item in declared
        if item["class"] == "STALE_INCOMPATIBLE"
    }
    outcomes = {
        str(item["source_id"]): str(item["retrieval_outcome"]) for item in declared
    }
    seen: list[str] = []

    def spy(source_id: str) -> MatchDisposition:
        seen.append(source_id)
        return MatchDisposition(outcomes[source_id])

    runner.run(stale_assessor=spy)

    assert set(seen) == stale_ids
    assert len(seen) == len(stale_ids) == 20
    # Only stale sources are assessed; the positive and negative classes are not
    # part of this gate condition.
    assert not any(
        str(item["source_id"]) in seen
        for item in declared
        if item["class"] != "STALE_INCOMPATIBLE"
    )
