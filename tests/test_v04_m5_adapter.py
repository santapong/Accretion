"""The project adapter: zero influence at the start, regularised, blind, and reproducible.

Five claims are under test, and each one is a way the adapter could be dangerous if it were
false.

**It is exactly the identity for a new project.** Not nearly. ``influence(0, k)`` is asserted
against ``0.0`` with ``==`` and ``apply`` at ``n_project = 0`` is asserted to return the
prior's logit unchanged bit for bit, using an artifact whose residual is large enough that
any leakage would be obvious. Float equality is deliberate in exactly those two places: an
approximate identity would mean a brand-new project already routes differently from its
workspace, which is the failure OQ-406 names.

**The trust schedule is the artifact's, and it is ``n / (n + k)``.** ``k`` is pinned by
literal weights rather than by the module constant, and two artifacts differing only in
``k`` are shown to correct the same logit at the same count by different amounts. Otherwise
serving code could hand a two-run project the authority of a two-hundred-run one without
changing any artifact — an unversioned change of exactly the kind ADR-049 makes promotion
exist to prevent.

**The residual is genuinely regularised.** Fitting the same miscalibrated evidence under a
ladder of growing ``l2`` must move the residual monotonically toward zero. The same test
first checks that the smallest ``l2`` *recovers* the miscalibration it was given, because a
shrinkage test that shrank noise would pass while proving nothing.

**It cannot see anything but a logit and a count.** SDD §6.5 is enforced by the signature
and checked by reading it. The parameter names are pinned to an exact set, so a context,
policy or risk handle added to either entry point turns the test red at the moment it is
added rather than at the review that might have caught it.

**A seed reproduces a fit exactly.** Same evidence and same seed give bit-identical
coefficients and therefore an identical artifact digest; a different seed gives a different
last bit while landing on the same optimum, which is what proves the seed is threaded
through the fit rather than merely recorded on the artifact. The digest is then swept field
by field — a constant, or a hash of the coefficients alone, would satisfy the equality
above while making two adapters fitted against different priors indistinguishable where
``RouterModelVersion.calibration_artifact_digest`` is compared.

**A calibrated prior is left alone.** Labels drawn from the prior's own probabilities
produce a near-zero residual, and the same tolerance applied to miscalibrated evidence
fails by a wide margin — so the threshold discriminates instead of merely being loose.

Everything here is offline, seeded and clock-free; no store is needed because the adapter
touches none.
"""

from __future__ import annotations

import inspect
import math
import random

import pytest
from pydantic import ValidationError

from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import FEATURE_SCHEMA_VERSION
from accretion.routing.adapter import (
    COEFFICIENT_BOUND,
    DEFAULT_INFLUENCE_HALF_LIFE,
    AdapterArtifact,
    ProjectAdapter,
    artifact_digest,
    influence,
)

PRIOR_VERSION_ID = "rmv_0000000000000000000001"

FORBIDDEN_IN_A_SIGNATURE = (
    "context",
    "policy",
    "risk",
    "capability",
    "verification",
    "verifier",
    "snapshot",
    "store",
    "session",
    "workspace",
    "candidate",
    "objective",
)
"""Words SDD §6.5 keeps out of the adapter. Substring-matched against names and annotations."""


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def sample_outcomes(
    *,
    seed: int,
    count: int,
    true_bias: float,
    true_slope: float,
) -> tuple[list[float], list[int]]:
    """Prior logits and the outcomes a prior mis-stated by ``true_bias``/``true_slope`` produces.

    The true success probability of observation ``i`` is
    ``sigmoid(logit + true_bias + true_slope · logit)``, so ``(0.0, 0.0)`` is a perfectly
    calibrated prior and anything else is a prior the adapter has a real residual to find.
    Both the logits and the coin flips come from one ``random.Random(seed)``, so a call is
    reproducible and no test reads a clock.
    """

    rng = random.Random(seed)
    prior_logits = [rng.uniform(-3.0, 3.0) for _ in range(count)]
    labels = [
        1 if rng.random() < _sigmoid(logit + true_bias + true_slope * logit) else 0
        for logit in prior_logits
    ]
    return prior_logits, labels


def test_influence_is_zero_at_n_zero_and_monotone_in_n() -> None:
    half_lives = [0.5, 1.0, DEFAULT_INFLUENCE_HALF_LIFE, 1_000.0]

    for k in half_lives:
        # Exact, not approximate: a new project routes on the workspace prior alone.
        assert influence(0, k) == 0.0
        weights = [influence(n, k) for n in range(0, 200)]
        assert all(
            later > earlier for earlier, later in zip(weights[:-1], weights[1:], strict=True)
        )
        assert weights[-1] < 1.0

    # Two different k values must give two different weights, and the numbers are pinned as
    # literals: an influence that ignored k, or a k read from somewhere other than its
    # argument, would still satisfy "zero at zero and monotone in n".
    assert influence(20, 20.0) == pytest.approx(0.5)
    assert influence(20, 5.0) == pytest.approx(0.8)
    assert influence(10, 5.0) > influence(10, 50.0)
    assert influence(1_000_000, 20.0) < 1.0

    opinionated = AdapterArtifact(
        bias=4.0,
        slope=-1.5,
        l2=1.0,
        n_fit=250,
        k=DEFAULT_INFLUENCE_HALF_LIFE,
        seed=1,
        prior_version_id=PRIOR_VERSION_ID,
    )
    for prior_logit in (-2.75, -0.5, 0.0, 0.5, 3.25):
        # A loud residual still has literally no effect before the first outcome resolves.
        assert ProjectAdapter.apply(opinionated, prior_logit, 0) == prior_logit

    # influence(20, 20.0) == 0.5, so this is the brief's formula written out at a logit that
    # is neither 0 nor 1: the slope term must be multiplied by the logit, not merely added
    # to the bias (2.5 here; a bias-plus-slope degenerate form would give 3.25).
    assert ProjectAdapter.apply(opinionated, 2.0, 20) == pytest.approx(
        2.0 + 0.5 * (4.0 + -1.5 * 2.0)
    )

    adapted = [ProjectAdapter.apply(opinionated, 2.0, n) for n in range(0, 60)]
    assert all(
        abs(later - 2.0) > abs(earlier - 2.0)
        for earlier, later in zip(adapted[:-1], adapted[1:], strict=True)
    )

    # The schedule that governs a decision is the promoted artifact's own k, not a default
    # the serving code happens to hold. Two artifacts identical but for k must therefore
    # correct the same logit at the same count by visibly different amounts.
    impatient = opinionated.model_copy(update={"k": 5.0})
    patient = opinionated.model_copy(update={"k": 500.0})
    impatient_adapted = ProjectAdapter.apply(impatient, 2.0, 10)
    patient_adapted = ProjectAdapter.apply(patient, 2.0, 10)
    assert abs(impatient_adapted - 2.0) > abs(patient_adapted - 2.0)
    assert impatient_adapted != patient_adapted

    with pytest.raises(ValueError, match="non-negative count"):
        influence(-1, 20.0)
    with pytest.raises(ValueError, match="positive, finite half-trust count"):
        influence(10, 0.0)


def test_fit_is_deterministic_for_a_seed() -> None:
    prior_logits, labels = sample_outcomes(seed=11, count=1_200, true_bias=0.8, true_slope=0.5)

    first = ProjectAdapter.fit(
        prior_logits, labels, l2=1.0, seed=3, prior_version_id=PRIOR_VERSION_ID
    )
    second = ProjectAdapter.fit(
        prior_logits, labels, l2=1.0, seed=3, prior_version_id=PRIOR_VERSION_ID
    )

    # Bit-for-bit, because the artifact digest is what two machines compare.
    assert first.bias == second.bias
    assert first.slope == second.slope
    assert artifact_digest(first) == artifact_digest(second)

    # Equality alone is a free pass — a constant would satisfy it. Every field of the
    # artifact must move the digest, so an adapter fitted against a different prior, under a
    # different penalty, seed, feature vocabulary or trust schedule is a different artifact
    # at promotion rather than a silent substitution (SDD §6.5).
    perturbations: dict[str, object] = {
        "bias": first.bias + 0.5,
        "slope": first.slope + 0.5,
        "l2": 2.0,
        "n_fit": first.n_fit + 1,
        "k": 5.0,
        "seed": 4,
        "prior_version_id": "rmv_0000000000000000000002",
        "feature_schema_version": "9.9.9",
        "schema_version": "1.0",
    }
    # A field added later cannot escape the sweep by being forgotten here.
    assert set(perturbations) == set(AdapterArtifact.model_fields)
    for field, value in sorted(perturbations.items()):
        if field == "schema_version":
            # Literal["1.0"] admits no second value to perturb, so prove membership in the
            # hashed payload directly.
            assert b'"schema_version"' in canonical_json(first)
            continue
        perturbed = first.model_copy(update={field: value})
        assert artifact_digest(perturbed) != artifact_digest(first), (
            f"{field} is not part of the artifact digest"
        )

    assert first.n_fit == len(prior_logits)
    # Pinned as a literal, not against the constant it came from: the brief fixes the
    # default half-trust count at twenty, and a comparison to the constant would agree with
    # any value the constant were quietly changed to.
    assert first.k == 20.0
    assert DEFAULT_INFLUENCE_HALF_LIFE == 20.0
    assert first.seed == 3
    assert first.prior_version_id == PRIOR_VERSION_ID
    assert first.feature_schema_version == FEATURE_SCHEMA_VERSION

    others = [
        ProjectAdapter.fit(
            prior_logits, labels, l2=1.0, seed=seed, prior_version_id=PRIOR_VERSION_ID
        )
        for seed in range(0, 12)
        if seed != 3
    ]
    # The seed is a real input — some other starting point lands on a different last bit —
    # and the objective is strictly convex, so every seed finds the same optimum anyway.
    assert any(other.bias != first.bias or other.slope != first.slope for other in others)
    assert all(
        abs(other.bias - first.bias) < 1e-9 and abs(other.slope - first.slope) < 1e-9
        for other in others
    )

    empty = ProjectAdapter.fit([], [], l2=1.0, seed=3, prior_version_id=PRIOR_VERSION_ID)
    assert (empty.bias, empty.slope, empty.n_fit) == (0.0, 0.0, 0)
    assert ProjectAdapter.apply(empty, 1.75, 10_000) == 1.75


def test_l2_pulls_the_residual_toward_zero_as_it_grows() -> None:
    prior_logits, labels = sample_outcomes(seed=11, count=2_000, true_bias=0.8, true_slope=0.5)
    ladder = [0.01, 1.0, 10.0, 100.0, 1_000.0, 10_000.0, 1_000_000.0]

    fitted = [
        ProjectAdapter.fit(prior_logits, labels, l2=l2, seed=3, prior_version_id=PRIOR_VERSION_ID)
        for l2 in ladder
    ]

    # The barely-penalised fit recovers the miscalibration it was shown, so what the rest of
    # the ladder shrinks is signal rather than noise.
    assert fitted[0].bias == pytest.approx(0.8, abs=0.15)
    assert fitted[0].slope == pytest.approx(0.5, abs=0.15)

    norms = [math.hypot(artifact.bias, artifact.slope) for artifact in fitted]
    assert all(later < earlier for earlier, later in zip(norms[:-1], norms[1:], strict=True))
    assert norms[-1] < 1e-3

    with pytest.raises(ValueError, match="l2 must be positive and finite"):
        ProjectAdapter.fit(prior_logits, labels, l2=0.0, seed=3, prior_version_id=PRIOR_VERSION_ID)
    # Every other input guard is a raise branch of its own; each is pinned here so that
    # neutering one turns this test red instead of letting bad evidence into the gradient.
    with pytest.raises(ValueError, match="must belong to exactly one prior logit"):
        ProjectAdapter.fit([0.1, 0.2], [1], l2=1.0, seed=3, prior_version_id=PRIOR_VERSION_ID)
    with pytest.raises(ValueError):
        ProjectAdapter.fit([math.inf], [1], l2=1.0, seed=3, prior_version_id=PRIOR_VERSION_ID)
    with pytest.raises(ValueError, match="does not grade in between"):
        ProjectAdapter.fit([0.1], [2], l2=1.0, seed=3, prior_version_id=PRIOR_VERSION_ID)
    with pytest.raises(ValueError, match="seed must be non-negative"):
        ProjectAdapter.fit([0.1], [1], l2=1.0, seed=-1, prior_version_id=PRIOR_VERSION_ID)
    with pytest.raises(ValueError, match="positive, finite half-trust count"):
        ProjectAdapter.fit([0.1], [1], l2=1.0, seed=3, k=0.0, prior_version_id=PRIOR_VERSION_ID)
    with pytest.raises(ValueError):
        ProjectAdapter.apply(fitted[0], math.nan, 10)


def test_the_adapter_cannot_see_anything_but_a_logit_and_a_count() -> None:
    apply_signature = inspect.signature(ProjectAdapter.apply)
    assert list(apply_signature.parameters) == ["artifact", "prior_logit", "n_project"]
    assert apply_signature.parameters["prior_logit"].annotation == "float"
    assert apply_signature.parameters["n_project"].annotation == "int"
    assert apply_signature.parameters["artifact"].annotation == "AdapterArtifact"
    assert apply_signature.return_annotation == "float"

    fit_signature = inspect.signature(ProjectAdapter.fit)
    assert list(fit_signature.parameters) == [
        "prior_logits",
        "labels",
        "l2",
        "seed",
        "k",
        "prior_version_id",
    ]
    assert {name: parameter.annotation for name, parameter in fit_signature.parameters.items()} == {
        "prior_logits": "list[float]",
        "labels": "list[int]",
        "l2": "float",
        "seed": "int",
        "k": "float",
        "prior_version_id": "str",
    }

    influence_signature = inspect.signature(influence)
    assert list(influence_signature.parameters) == ["n", "k"]

    for signature in (apply_signature, fit_signature, influence_signature):
        for name, parameter in signature.parameters.items():
            spelling = f"{name} {parameter.annotation}".lower()
            for forbidden in FORBIDDEN_IN_A_SIGNATURE:
                assert forbidden not in spelling, f"{name} exposes {forbidden!r} to the adapter"

    # The artifact is the adapter's whole world, and it is nine scalars — no nested object
    # could carry policy, capabilities or verification rules in through the back door.
    assert set(AdapterArtifact.model_fields) == {
        "schema_version",
        "bias",
        "slope",
        "l2",
        "n_fit",
        "k",
        "seed",
        "prior_version_id",
        "feature_schema_version",
    }

    with pytest.raises(ValidationError):
        AdapterArtifact(
            bias=0.0,
            slope=0.0,
            l2=1.0,
            n_fit=0,
            k=20.0,
            seed=1,
            prior_version_id=PRIOR_VERSION_ID,
            risk_class="LOW",  # type: ignore[call-arg]
        )

    # An adapter fitted on zero outcomes must carry a zero residual (OQ-406); the validator's
    # condition is an `or`, so both operands are pinned.
    with pytest.raises(ValidationError, match="zero outcomes must carry a zero residual"):
        AdapterArtifact(
            bias=0.3, slope=0.0, l2=1.0, n_fit=0, k=20.0, seed=1, prior_version_id=PRIOR_VERSION_ID
        )
    with pytest.raises(ValidationError, match="zero outcomes must carry a zero residual"):
        AdapterArtifact(
            bias=0.0, slope=0.3, l2=1.0, n_fit=0, k=20.0, seed=1, prior_version_id=PRIOR_VERSION_ID
        )

    # A runaway or non-finite fit fails at construction instead of being persisted and
    # applied to every decision in the project.
    for runaway in (COEFFICIENT_BOUND * 2, -COEFFICIENT_BOUND * 2, math.inf, -math.inf, math.nan):
        with pytest.raises(ValidationError):
            AdapterArtifact(
                bias=runaway,
                slope=0.0,
                l2=1.0,
                n_fit=5,
                k=20.0,
                seed=1,
                prior_version_id=PRIOR_VERSION_ID,
            )
        with pytest.raises(ValidationError):
            AdapterArtifact(
                bias=0.0,
                slope=runaway,
                l2=1.0,
                n_fit=5,
                k=20.0,
                seed=1,
                prior_version_id=PRIOR_VERSION_ID,
            )
    assert COEFFICIENT_BOUND == 1_000.0


def test_a_well_calibrated_prior_gets_a_near_zero_residual() -> None:
    calibrated_logits, calibrated_labels = sample_outcomes(
        seed=20260905, count=4_000, true_bias=0.0, true_slope=0.0
    )
    calibrated = ProjectAdapter.fit(
        calibrated_logits, calibrated_labels, l2=1.0, seed=7, prior_version_id=PRIOR_VERSION_ID
    )

    assert abs(calibrated.bias) < 0.15
    assert abs(calibrated.slope) < 0.15

    # Even at heavy influence the correction to a calibrated prior stays small.
    for prior_logit in (-3.0, -1.0, 0.0, 1.0, 3.0):
        adapted = ProjectAdapter.apply(calibrated, prior_logit, 2_000)
        # Small, but not nothing: the adapter did run and did move the logit.
        assert 0.0 < abs(adapted - prior_logit) < 0.25

    miscalibrated_logits, miscalibrated_labels = sample_outcomes(
        seed=20260905, count=4_000, true_bias=0.8, true_slope=0.5
    )
    miscalibrated = ProjectAdapter.fit(
        miscalibrated_logits,
        miscalibrated_labels,
        l2=1.0,
        seed=7,
        prior_version_id=PRIOR_VERSION_ID,
    )
    # The same tolerance rejects a prior that is actually wrong, so it is a test and not a
    # formality: a fit that always returned zero would pass the assertions above and fail here.
    assert abs(miscalibrated.bias) > 0.15
    assert abs(miscalibrated.slope) > 0.15
