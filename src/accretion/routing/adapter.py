"""The project adapter: a regularised residual on the workspace prior's logit (OQ-406).

ADR-047 splits the learned router in two — a workspace prior fitted over everything the
workspace has resolved, and a per-project adapter fitted over the one project's own
outcomes. This module is the second half, and the whole of the design is contained in one
sentence: **the adapter never predicts, it only corrects.** It takes the number the prior
already produced and returns that number plus a small, shrunk, regularised correction.

**Why a residual and not a model.** A project-scoped model fitted on a project's first
handful of runs is a model fitted on noise, and it would be at its most confident exactly
when it knows least. Parameterising the adapter as a two-number affine residual on the
prior's *logit* makes the failure mode benign instead: with the residual at zero the
adapter is the identity function, so the worst a badly-fitted adapter can do is nudge a
decision the prior had already made. The prior remains the thing that decides; the adapter
is a calibration layer over it, which is why SDD §9.3 and OQ-405 keep the calibration
digest separate from the model digest in :class:`~accretion.contracts.routing.
RouterModelVersion`.

**Why influence grows with n and is exactly zero at the start.** :func:`influence` is
``n / (n + k)``, the standard shrinkage weight, and at ``n = 0`` it is exactly ``0.0`` —
not approximately, not a small epsilon. A brand-new project therefore routes on the
workspace prior *alone*, bit for bit, and no configuration, flag or rollout decision is
needed to arrange that. ``k`` is the count at which the adapter is trusted half way, so it
is the one knob that says how fast a project is allowed to disagree with its workspace.
The shrinkage is applied at serving time rather than baked into the coefficients so that
the same artifact keeps being correct as the project accumulates evidence: only ``n``
moves, and the fitted residual is not refitted just because a run finished.

**What the adapter is allowed to see.** A float logit and an integer count. Not the policy,
not the risk class, not the capability set, not the verification rules (SDD §6.5). That
restriction is enforced by the signature and tested by inspecting it, because it is the
property that makes the adapter safe to promote on evidence alone: a component that cannot
read policy cannot quietly relitigate a policy decision, and one that cannot read the risk
class cannot learn to route around a risk gate. Everything the adapter knows about the
world arrived through the prior's logit, and the prior is versioned — which is why
``prior_version_id`` is part of the artifact and part of its digest. An adapter fitted
against one prior is meaningless applied to another, and the digest is what makes that
mismatch detectable rather than silent.

**Determinism.** The fit is a damped Newton descent on a strictly convex objective (the
logistic negative log-likelihood plus an L2 penalty toward the zero residual), so it has
exactly one optimum and no random restart could find a different one. The ``seed`` is
still real and still recorded: it draws the starting point, so a re-fit reproduces the
same iterates and therefore the same floating-point bits, which is what an artifact digest
compared across two machines actually requires. Nothing here reads a clock, a store or the
environment.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import FEATURE_SCHEMA_VERSION

DEFAULT_INFLUENCE_HALF_LIFE = 20.0
"""The default ``k``: the resolved-outcome count at which the adapter is half trusted.

Twenty is not a tuned value and is not claimed to be one. It is the count at which a
project's own evidence stops being anecdote, chosen so that the first handful of runs move
a decision by a few percent of the residual rather than by all of it. It is a field of the
artifact rather than a module constant used at serving time precisely so that changing this
default cannot retroactively change what an already-fitted adapter does.
"""

COEFFICIENT_BOUND = 1_000.0
"""The largest residual coefficient an artifact may carry, in logits.

A logit residual of a thousand is not a correction, it is a runaway fit — the sign of an
L2 term set too small against separable data. The bound exists so that such an artifact
fails validation at construction, where the fit that produced it is still on the stack,
rather than being persisted and later applied to every decision in the project. It also
excludes the non-finite floats, which no comparison against a bound can accept.
"""

_MAX_NEWTON_STEPS = 100
"""Iteration cap. Newton on this objective converges in well under ten from any start."""

_MAX_BACKTRACKS = 40
"""Step halvings before a Newton direction is abandoned; ``2**-40`` is already nothing."""

_CONVERGENCE_TOLERANCE = 1e-12
"""Stop once neither coefficient moved by more than this, measured in logits."""

_INITIAL_SPREAD = 1e-3
"""Half-width of the seeded interval the starting residual is drawn from.

Small enough that the first Newton step is taken essentially from the zero residual — the
point the L2 penalty pulls toward — and non-zero so that ``seed`` is a real input whose
value is recoverable from the artifact and whose effect on the final bits is reproducible.
"""

_EXP_LIMIT = 700.0
"""``math.exp`` overflows a shade above 709; clamping here keeps the sigmoid total."""


def influence(n: int, k: float) -> float:
    """The weight the adapter's residual carries after ``n`` compatible resolved outcomes.

    ``n / (n + k)``: exactly ``0.0`` at ``n = 0``, strictly increasing in ``n``, and
    approaching but never reaching ``1.0``. The adapter can therefore never fully replace
    the prior no matter how much evidence a project accumulates, which is the difference
    between a calibration layer and a second, unreviewed router.

    Raises ``ValueError`` for a negative count or a non-positive ``k``: a negative count is
    not a number of outcomes, and ``k <= 0`` would give the very first outcome full
    authority — the exact failure this function exists to prevent.
    """

    if n < 0:
        raise ValueError(f"n must be a non-negative count of resolved outcomes, got {n}")
    if not math.isfinite(k) or k <= 0.0:
        raise ValueError(
            f"k must be a positive, finite half-trust count, got {k}; a non-positive k "
            "would give a project's first outcome the same weight as its thousandth"
        )
    return n / (n + k)


def _sigmoid(z: float) -> float:
    """The logistic function, evaluated on whichever side of zero does not overflow."""

    clamped = max(-_EXP_LIMIT, min(_EXP_LIMIT, z))
    if clamped >= 0.0:
        return 1.0 / (1.0 + math.exp(-clamped))
    shifted = math.exp(clamped)
    return shifted / (1.0 + shifted)


def _log1p_exp(z: float) -> float:
    """``log(1 + exp(z))`` without overflowing for large ``z`` or losing it for small."""

    if z > _EXP_LIMIT:
        return z
    if z > 0.0:
        return z + math.log1p(math.exp(-z))
    return math.log1p(math.exp(max(-_EXP_LIMIT, z)))


def _penalised_loss(
    prior_logits: list[float],
    labels: list[int],
    bias: float,
    slope: float,
    l2: float,
) -> float:
    """The objective the fit minimises: logistic NLL plus ``l2/2`` times the squared residual.

    The prior's logit enters twice and differently: once as a fixed offset, which is what
    makes this a residual rather than a model, and once as the single feature the slope
    multiplies, which is what lets the residual say "the prior is overconfident at the
    extremes" rather than only "the prior is uniformly too high".
    """

    total = 0.5 * l2 * (bias * bias + slope * slope)
    for prior_logit, label in zip(prior_logits, labels, strict=True):
        z = prior_logit + bias + slope * prior_logit
        total += _log1p_exp(z) - label * z
    return total


def _fit_residual(
    prior_logits: list[float],
    labels: list[int],
    l2: float,
    rng: random.Random,
) -> tuple[float, float]:
    """Damped Newton on :func:`_penalised_loss`, returning ``(bias, slope)``.

    The Hessian is ``XᵀWX + l2·I`` with ``W`` the logistic variances, so it is positive
    definite with smallest eigenvalue at least ``l2`` and its 2x2 determinant is at least
    ``l2**2``: the solve below cannot divide by zero as long as ``l2`` is positive, which
    the caller has already required. Backtracking makes each accepted step a descent step,
    so the sequence of losses is monotone and the loop terminates on movement rather than
    on a residual that a full Newton step might have overshot.
    """

    bias = rng.uniform(-_INITIAL_SPREAD, _INITIAL_SPREAD)
    slope = rng.uniform(-_INITIAL_SPREAD, _INITIAL_SPREAD)
    loss = _penalised_loss(prior_logits, labels, bias, slope, l2)

    for _ in range(_MAX_NEWTON_STEPS):
        gradient_bias = l2 * bias
        gradient_slope = l2 * slope
        hessian_bb = l2
        hessian_bs = 0.0
        hessian_ss = l2
        for prior_logit, label in zip(prior_logits, labels, strict=True):
            z = prior_logit + bias + slope * prior_logit
            probability = _sigmoid(z)
            residual = probability - label
            gradient_bias += residual
            gradient_slope += residual * prior_logit
            variance = probability * (1.0 - probability)
            hessian_bb += variance
            hessian_bs += variance * prior_logit
            hessian_ss += variance * prior_logit * prior_logit

        determinant = hessian_bb * hessian_ss - hessian_bs * hessian_bs
        step_bias = (hessian_ss * gradient_bias - hessian_bs * gradient_slope) / determinant
        step_slope = (hessian_bb * gradient_slope - hessian_bs * gradient_bias) / determinant

        scale = 1.0
        accepted = False
        candidate_bias = bias
        candidate_slope = slope
        candidate_loss = loss
        for _attempt in range(_MAX_BACKTRACKS):
            candidate_bias = bias - scale * step_bias
            candidate_slope = slope - scale * step_slope
            candidate_loss = _penalised_loss(
                prior_logits, labels, candidate_bias, candidate_slope, l2
            )
            if candidate_loss <= loss:
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            # Every halving increased the loss: the current point is the optimum to the
            # precision floats can express, and moving would be a lie about convergence.
            break

        moved = max(abs(candidate_bias - bias), abs(candidate_slope - slope))
        bias, slope, loss = candidate_bias, candidate_slope, candidate_loss
        if moved <= _CONVERGENCE_TOLERANCE:
            break

    return bias, slope


class AdapterArtifact(StrictModel):
    """A fitted project adapter: two coefficients, the conditions of the fit, and its lineage.

    Everything needed to reproduce the fit and to refuse to misapply it is here.
    ``l2``, ``seed`` and ``n_fit`` reproduce it; ``prior_version_id`` and
    ``feature_schema_version`` refuse it — an adapter is a correction to *one* prior under
    *one* feature vocabulary, and applying it to another prior would be applying a
    correction computed for numbers that no longer mean the same thing.

    ``k`` lives on the artifact rather than being supplied at serving time so that the
    influence schedule is part of what was reviewed and promoted. A caller that could pass
    its own ``k`` to :meth:`ProjectAdapter.apply` could give a two-run project the
    authority of a two-hundred-run one without changing any artifact, which is precisely
    the sort of unversioned change ADR-049 makes promotion exist to prevent.
    """

    schema_version: Literal["1.0"] = "1.0"
    bias: float = Field(ge=-COEFFICIENT_BOUND, le=COEFFICIENT_BOUND)
    slope: float = Field(ge=-COEFFICIENT_BOUND, le=COEFFICIENT_BOUND)
    l2: float = Field(gt=0.0, le=1e12)
    n_fit: int = Field(ge=0)
    k: float = Field(gt=0.0, le=1e12)
    seed: int = Field(ge=0)
    prior_version_id: str = Field(min_length=1, max_length=64)
    feature_schema_version: str = Field(
        default=FEATURE_SCHEMA_VERSION, pattern=r"^\d+\.\d+\.\d+$"
    )

    @model_validator(mode="after")
    def _an_adapter_fitted_on_nothing_is_the_identity(self) -> Self:
        if self.n_fit == 0 and (self.bias != 0.0 or self.slope != 0.0):
            raise ValueError(
                "an adapter fitted on zero outcomes must carry a zero residual; a non-zero "
                f"residual (bias={self.bias}, slope={self.slope}) at n_fit=0 is a correction "
                "derived from no evidence, which OQ-406 requires to have no influence"
            )
        return self


class ProjectAdapter:
    """Fit and apply the residual. Two static methods, no state, no dependencies.

    There is no instance to construct because there is nothing for an instance to hold: the
    artifact carries every fitted number and the caller carries the count. Keeping it that
    way is what makes the signature check in the tests meaningful — a hidden ``self`` could
    smuggle in a policy handle that ``inspect.signature`` would never show.
    """

    @staticmethod
    def fit(
        prior_logits: list[float],
        labels: list[int],
        *,
        l2: float,
        seed: int,
        k: float = DEFAULT_INFLUENCE_HALF_LIFE,
        prior_version_id: str,
    ) -> AdapterArtifact:
        """Fit the two-parameter logistic residual on the prior's logits.

        ``prior_logits[i]`` is what the workspace prior said about observation ``i`` and
        ``labels[i]`` is whether that observation resolved successfully — one and zero, and
        nothing else, because a partially-successful outcome is a judgement this layer is
        not entitled to make.

        With no observations the penalised optimum is exactly the zero residual, and that
        is returned directly rather than iterated toward: the identity adapter is a value
        the fit should produce exactly, not to within a rounding error.

        Raises ``ValueError`` on mismatched lengths, a non-finite logit, a label outside
        ``{0, 1}``, or a non-positive ``l2`` — an unregularised fit on separable data has
        no finite optimum, so "no regularisation" is not a mode this function offers.
        """

        if len(prior_logits) != len(labels):
            raise ValueError(
                f"prior_logits has {len(prior_logits)} entries and labels has "
                f"{len(labels)}; each label must belong to exactly one prior logit"
            )
        if not math.isfinite(l2) or l2 <= 0.0:
            raise ValueError(
                f"l2 must be positive and finite, got {l2}; the residual is regularised "
                "toward zero by construction and an unpenalised fit on separable outcomes "
                "diverges instead of converging"
            )
        if not math.isfinite(k) or k <= 0.0:
            raise ValueError(f"k must be a positive, finite half-trust count, got {k}")
        if seed < 0:
            raise ValueError(f"seed must be non-negative, got {seed}")
        for index, prior_logit in enumerate(prior_logits):
            if not math.isfinite(prior_logit):
                raise ValueError(
                    f"prior_logits[{index}] is {prior_logit}; a non-finite logit is not a "
                    "prediction the prior could have made"
                )
        for index, label in enumerate(labels):
            if label != 0 and label != 1:
                raise ValueError(
                    f"labels[{index}] is {label}; an outcome resolved successfully (1) or "
                    "it did not (0), and this layer does not grade in between"
                )

        if prior_logits:
            bias, slope = _fit_residual(prior_logits, labels, l2, random.Random(seed))
        else:
            bias, slope = 0.0, 0.0

        return AdapterArtifact(
            bias=bias,
            slope=slope,
            l2=l2,
            n_fit=len(prior_logits),
            k=k,
            seed=seed,
            prior_version_id=prior_version_id,
        )

    @staticmethod
    def apply(artifact: AdapterArtifact, prior_logit: float, n_project: int) -> float:
        """The adapted logit: ``prior_logit + influence(n_project, k) · (bias + slope · logit)``.

        At ``n_project == 0`` the influence is exactly ``0.0`` and the return value is the
        prior's logit unchanged — the identity, not an approximation of it.

        ``n_project`` is the project's count of compatible resolved outcomes at serving
        time, which is deliberately not ``artifact.n_fit``: an adapter fitted last week on
        forty outcomes should not claim the authority of the ninety the project has now,
        nor keep the authority of forty if the eligible evidence has since shrunk.
        """

        if not math.isfinite(prior_logit):
            raise ValueError(
                f"prior_logit is {prior_logit}; the adapter corrects a prediction and a "
                "non-finite logit is not one"
            )
        weight = influence(n_project, artifact.k)
        return prior_logit + weight * (artifact.bias + artifact.slope * prior_logit)


def artifact_digest(artifact: AdapterArtifact) -> str:
    """The SHA-256 of the artifact's canonical JSON (ADR-056).

    This is the value that goes into ``RouterModelVersion.calibration_artifact_digest``, so
    it must move when *anything* about the fit moves — not only the coefficients but the
    ``l2`` that shaped them, the ``seed`` that reproduces them, the ``k`` that scales them
    and the prior they correct. Hashing the whole model rather than a chosen subset is how
    that stays true when a field is added.
    """

    return hashlib.sha256(canonical_json(artifact)).hexdigest()
