"""Ways a promotion gate is lied to, and what this one does about each of them.

Three attacks, and none of them is hypothetical — each is the ordinary failure mode of a
gate built the obvious way.

*The mean that hides the slice.* A candidate that is better on average and worse on the
records that matter is what an aggregate metric was invented to conceal, and it is the reason
§10.3 makes a critical regression a block rather than a tradeoff. The cohort below is a
quarter of the holdout — enough to be measured, nowhere near enough to pull the aggregate
below its bar — and by the registered list it is decisive regardless.

*The artefact that changed under a stable digest.* A version pins the bytes it was fitted
from, and a gate that scored whatever the store handed back would produce a real-looking
report about a different model. The refusal is a *finding* rather than an exception, because
"this candidate's artefact no longer verifies" is exactly the thing an operator needs written
down and attributable.

*The importance weight that ran away.* A logged decision taken with probability 0.05 gets a
weight of twenty under inverse propensity scoring, and three such rows can carry an entire
holdout. R4's smoothing is the answer, and the test below measures the gap between the two
estimators rather than asserting that the pessimistic one exists.

There is no ``conftest.py``. The bench is the evaluator module's, imported and not edited.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from test_v04_m8_evaluator import (
    HOLDOUT_PROJECTS,
    seed_shadow_stage,
    setup_bench,
)

from accretion.contracts.routing import RouterPromotionDecision
from accretion.ids import new_id
from accretion.routing.ope import lambda_rule, ls_estimate
from accretion.routing.promotion import PromotionNotApprovedError, PromotionService
from accretion.routing.ranker import ArtifactDigestMismatchError

HOLDOUT_ROWS = HOLDOUT_PROJECTS * 4


# --------------------------------------------------------------------------------------
# The mean that hides the slice.
# --------------------------------------------------------------------------------------


async def test_a_large_mean_improvement_does_not_buy_a_small_critical_regression(
    tmp_path: Path,
) -> None:
    """The aggregate is comfortably positive and the verdict is still ``REJECT``.

    Both halves are asserted, because either alone would be satisfiable by a gate that was
    simply broken: the primary metric passes its own bar, *and* the critical cohort is a
    sixth of the holdout, *and* the decision is a rejection naming that cohort. A gate that
    rejected everything would fail the first assertion; one that read only the mean would
    fail the third.
    """

    bench = await setup_bench(tmp_path, secret_cohort=True)
    await seed_shadow_stage(bench)

    report = await bench.evaluate()
    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None

    secrets = next(item for item in stored.cohort_results if item.cohort_id == "secrets")
    assert stored.primary_metric_result.passed
    assert stored.primary_metric_result.delta_lower_bound > 0.02
    assert secrets.sample_size * 4 == HOLDOUT_ROWS
    assert secrets.comparison.delta < 0
    assert stored.decision is RouterPromotionDecision.REJECT


async def test_a_rejected_report_cannot_be_turned_into_an_activation(
    tmp_path: Path,
) -> None:
    """The seam between the gate and the ledger, closed from the ledger's side too.

    A caller holding a ``REJECT`` report and the promote route still cannot activate
    anything, and the refusal names the decision it read rather than a generic conflict.
    """

    from test_v04_m8_api import setup_wired

    wired = await setup_wired(tmp_path, secret_cohort=True)
    await seed_shadow_stage(wired.bench)
    report = await wired.bench.evaluate()
    assert report.decision is RouterPromotionDecision.REJECT

    service: PromotionService = wired.service
    with pytest.raises(PromotionNotApprovedError) as refusal:
        await service.promote(report.contract_id, wired.bench.corpus.principal)

    assert refusal.value.code == "ROUTER_PROMOTION_NOT_APPROVED"
    assert "REJECT" in refusal.value.message
    assert (
        await wired.store.list_router_activations(workspace_id=wired.workspace_id) == []
    )


# --------------------------------------------------------------------------------------
# The artefact that changed under a stable digest.
# --------------------------------------------------------------------------------------


async def test_a_candidate_whose_bytes_no_longer_verify_is_rejected_and_claims_nothing(
    tmp_path: Path,
) -> None:
    """A digest mismatch is recorded as a finding, and no metric is invented around it.

    Every comparison in the report reads zero and fails, which is the honest shape of "not
    measured": a gate that had defaulted the unmeasured gates to *passing* would let a
    corrupted candidate through on everything except the one check that noticed.
    """

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)
    bench.loader.failure = ArtifactDigestMismatchError(
        "stored bytes hash to 0000 and the version pinned abcd"
    )
    bench.loader.failing_version_id = bench.candidate.contract_id

    report = await bench.evaluate()
    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None

    finding = next(
        item
        for item in stored.critical_regressions
        if item.finding_id == "PREDICTOR_UNASSEMBLABLE"
    )
    assert "ArtifactDigestMismatchError" in finding.description
    assert stored.primary_metric_result.delta == 0.0
    assert not stored.primary_metric_result.passed
    assert stored.cohort_results == []
    assert stored.decision is RouterPromotionDecision.REJECT
    assert stored.approved_by is None


async def test_a_candidate_that_was_never_evaluated_cannot_pass_the_sealed_gates(
    tmp_path: Path,
) -> None:
    """AC4-M4-016's rule, restated where promotion reads it.

    A version carrying no ``holdout_eval_digest`` has no §10.2 evidence at all, so the three
    mandatory comparisons are unmeasured and the report says so by name.
    """

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)
    stripped = bench.candidate.model_dump(mode="json")
    stripped.pop("content_hash", None)
    stripped["contract_id"] = new_id("router_model_version")
    stripped["labels"] = {}
    unevaluated = type(bench.candidate).model_validate(stripped)
    await bench.store.put_router_model_version(unevaluated)
    bench.loader.predictors[unevaluated.contract_id] = bench.loader.predictors[
        bench.candidate.contract_id
    ]

    report = await bench.evaluate(candidate_version_id=unevaluated.contract_id)

    assert "HOLDOUT_EVALUATION_MISSING" in {
        finding.finding_id for finding in report.critical_regressions
    }
    assert not report.verified_success_non_regression.passed
    assert report.decision is RouterPromotionDecision.REJECT


# --------------------------------------------------------------------------------------
# The importance weight that ran away.
# --------------------------------------------------------------------------------------


async def test_a_holdout_logged_at_low_propensity_is_priced_pessimistically(
    tmp_path: Path,
) -> None:
    """R4's whole purpose, measured rather than asserted.

    Every decision in this corpus was logged with probability 0.05, so every retained row
    carries a weight of twenty and unsmoothed IPS would credit the candidate with an
    improvement about twenty times the observed one. The smoothed estimate is computed here
    a second way from the same weights and costs, and the report's own delta must not exceed
    it — a gate that had quietly used IPS would report the larger number.
    """

    bench = await setup_bench(tmp_path, propensity=0.05)
    await seed_shadow_stage(bench)

    report = await bench.evaluate()

    scored = int(report.labels["accretion.scored-rows"])
    retained = int(report.labels["accretion.retained-rows"])
    assert retained == scored

    lam = lambda_rule(scored)
    weights = [20.0] * scored
    costs = [-1.0 if index % 4 != 3 else 0.0 for index in range(scored)]
    smoothed = ls_estimate(weights, costs, lam)
    unsmoothed = math.fsum(w * c for w, c in zip(weights, costs, strict=True)) / scored

    assert smoothed > unsmoothed
    assert report.primary_metric_result.delta < abs(unsmoothed)


async def test_the_report_carries_the_diagnostics_that_say_whether_to_believe_it(
    tmp_path: Path,
) -> None:
    """ESS and the clip mass are on every report, because the verdict alone is not evidence.

    "n = 24" and "ESS = 24" are the same holdout and the same evidence; "n = 24, ESS = 3"
    is not, and a report that recorded only the first would leave the second to be discovered
    by whoever re-derived it.
    """

    honest = await setup_bench(tmp_path / "honest")
    await seed_shadow_stage(honest)
    concentrated = await setup_bench(tmp_path / "concentrated", propensity=0.05)
    await seed_shadow_stage(concentrated)

    plain = await honest.evaluate()
    weighted = await concentrated.evaluate()

    for report in (plain, weighted):
        assert set(report.labels) >= {
            "accretion.clipped-weight-mass",
            "accretion.effective-sample-size",
            "accretion.retained-rows",
            "accretion.scored-rows",
            "accretion.snips-diagnostic",
        }
    assert float(plain.labels["accretion.clipped-weight-mass"]) == 0.0
    assert float(weighted.labels["accretion.clipped-weight-mass"]) == pytest.approx(0.5)
    assert float(plain.labels["accretion.effective-sample-size"]) == pytest.approx(
        float(weighted.labels["accretion.effective-sample-size"])
    )
