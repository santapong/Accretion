"""SDD §9.7's recovery guard: who may act next, and whether anything may act at all.

A classified failure (:mod:`accretion.feedback.failures`) says which layer owns the problem.
This module answers the two questions that follow — *what should happen next* and *is
another automatic attempt still justified* — and it is deliberately the only place either is
answered, because both are authority decisions:

* **Authority is a fixed literal per owner, not a judgement.** ``AUTHORITY_SCOPE_BY_OWNER``
  is total over :class:`~accretion.contracts.routing.FailureOwner`, so a failure the router
  owns yields ``ROUTER_RESELECT`` and can never yield ``PLANNER_REPLAN`` or ``HUMAN``. A
  guard that computed the scope from the action would let a stopped recovery quietly widen
  into human authority, and "the recovery path granted itself a scope it was never given"
  is the failure mode this whole module exists to make unrepresentable.
* **The one escalation is explicit and has a rule.** Two or more *distinct* configurations
  failing on one node is evidence about the node, not about the configurations: the router
  has now demonstrated that its own search space does not contain an answer. That re-types
  the failure to ``STRUCTURAL`` and hands it to the planner — the only path from router
  authority to planner authority, taken on a stated condition and recorded in
  ``reason_code`` as ``CONFIGURATION_SPACE_EXHAUSTED`` rather than inferred later.
* **Two independent stops, and each is sufficient.** The hard cap
  (``attempt >= budget.maximum_attempts``) is arithmetic on the objective contract's own
  budget. The EVI gate is statistical: ``EVI_v1 = (untried eligible / total) × prior_success``
  measures what another attempt could plausibly be worth, and recovery continues only while a
  *lower confidence bound* on it exceeds ``epsilon``. The bound is a Wilson lower bound on
  the untried fraction and not the point estimate, which matters exactly when it is
  expensive to be wrong: one untried candidate out of twenty is a 0.05 point estimate — over
  a 0.02 threshold — and a 0.009 lower bound, under it. Point-estimate optimism on a small
  remaining candidate set is precisely how a loop keeps paying for attempts that will not pay
  back.
* **A failed configuration does not come back without new evidence.** ``eligible_candidates``
  removes every hash already attempted — the ones the caller reports *and* the ones the
  failure event itself carries — unless ``new_evidence_since`` records evidence arriving for
  that hash since it was tried. This is §9.7's last rule, and it is a public method so that
  a caller (and a test) can see the surviving set rather than infer it from an action.

Pure and synchronous: no clock, no store, no I/O. The run manager wires it in M3b.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from accretion.contracts.routing import FailureEvent, FailureOwner, ResourceBudget

RecoveryAction = Literal["RESELECT", "REPLAN", "RESOLVE_EVIDENCE", "ESCALATE", "STOP"]
"""What the guard says should happen next. ``STOP`` ends automatic recovery."""

AuthorityScope = Literal["ROUTER_RESELECT", "PLANNER_REPLAN", "EVIDENCE_RESOLUTION", "HUMAN"]
"""Under whose authority that step happens (SDD §9.7)."""

AUTHORITY_SCOPE_BY_OWNER: dict[FailureOwner, AuthorityScope] = {
    FailureOwner.CONFIGURATION: "ROUTER_RESELECT",
    # The capability manager rebinds within the routing surface: a different tool binding is
    # a different configuration, chosen by the same authority under the same policy.
    FailureOwner.CAPABILITY: "ROUTER_RESELECT",
    FailureOwner.ENVIRONMENT: "ROUTER_RESELECT",
    FailureOwner.STRUCTURAL: "PLANNER_REPLAN",
    FailureOwner.VERIFICATION: "EVIDENCE_RESOLUTION",
    # A budget is raised by whoever set it, which is never the router.
    FailureOwner.RESOURCE: "HUMAN",
    FailureOwner.SAFETY: "HUMAN",
    FailureOwner.AUTHORITY: "HUMAN",
    FailureOwner.UNKNOWN: "HUMAN",
}
"""Total over :class:`FailureOwner`; the scope is a property of the owner and of nothing else."""

DEFAULT_EPSILON = 0.02
"""SDD §9.7's ``LCB[EVI] > ε`` threshold: below this an attempt is not worth its cost."""

WILSON_Z = 1.96
"""Two-sided 95% normal quantile, so the bound is a 97.5% one-sided lower confidence limit."""

_MINIMUM_DISTINCT_CONFIGURATION_FAILURES = 2
"""Distinct failed configurations on one node before the failure re-types to structural."""


def wilson_lower_bound(successes: int, total: int, *, z: float = WILSON_Z) -> float:
    """A Wilson score lower confidence bound on ``successes / total``.

    Wilson rather than the normal approximation because the interesting inputs are exactly
    the ones the normal interval handles worst — a handful of candidates, a proportion near
    0 or 1 — and a bound that collapses to zero width at ``successes == total`` would make
    "one candidate left, all untried" look like certainty.

    Returns 0.0 for ``total == 0``: no observation supports no confidence.
    """

    if successes < 0 or total < 0 or successes > total:
        raise ValueError(
            f"wilson_lower_bound needs 0 <= successes <= total; got {successes}/{total}"
        )
    if total == 0:
        return 0.0
    proportion = successes / total
    denominator = 1.0 + (z * z) / total
    centre = (proportion + (z * z) / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1.0 - proportion) / total + (z * z) / (4 * total * total))
        / denominator
    )
    return max(0.0, min(1.0, centre - margin))


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """The guard's answer: one action, one owner, one authority scope, one reason.

    ``evi_lcb`` is ``None`` when the EVI gate never ran — a cap stop, an escalation and a
    replan are all decided without it — and a float whenever it did, including when it is
    the thing that stopped recovery. A stored ``None`` and a stored ``0.0`` therefore say
    different things, which is the point of the optional.
    """

    action: RecoveryAction
    owner: FailureOwner
    authority_scope: AuthorityScope
    reason_code: str
    evi_lcb: float | None


@dataclass(frozen=True, slots=True)
class RecoveryGuard:
    """Decides whether automatic recovery continues, and under whose authority (SDD §9.7).

    ``z`` is injectable so an operator profile can demand a stricter bound; it is not a knob
    the guard adjusts for itself, because a guard that could widen its own confidence bound
    could authorise its own next attempt.
    """

    z: float = WILSON_Z

    def eligible_candidates(
        self,
        *,
        candidate_hashes: Sequence[str],
        attempted: Sequence[str],
        new_evidence_since: Mapping[str, int],
    ) -> tuple[str, ...]:
        """The candidates §9.7 still permits, in the order the caller ranked them.

        Input order is preserved rather than sorted: the sequence arrives ranked by utility
        and re-sorting it would silently replace the router's preference with an alphabetical
        one. Duplicates are collapsed, keeping the first occurrence, so a repeated hash cannot
        inflate the EVI denominator or the eligible count.
        """

        blocked = set(attempted)
        seen: set[str] = set()
        eligible: list[str] = []
        for digest in candidate_hashes:
            if digest in seen:
                continue
            seen.add(digest)
            if digest in blocked and new_evidence_since.get(digest, 0) <= 0:
                continue
            eligible.append(digest)
        return tuple(eligible)

    def decide(
        self,
        *,
        failure: FailureEvent,
        budget: ResourceBudget,
        attempt: int,
        candidate_hashes: Sequence[str],
        attempted: Sequence[str],
        new_evidence_since: Mapping[str, int],
        prior_success: float,
        epsilon: float = DEFAULT_EPSILON,
    ) -> RecoveryDecision:
        """Decide the next recovery step for ``failure``.

        ``attempt`` is the number of the attempt that has just failed, counting from one, so
        ``attempt >= budget.maximum_attempts`` means the contract's last permitted attempt is
        the one that failed. The comparison is ``>=`` and not ``>``: with ``>`` a budget of
        three attempts would buy four.
        """

        if attempt < 1:
            raise ValueError(f"attempt counts from one; got {attempt}")
        if not 0.0 <= prior_success <= 1.0:
            raise ValueError(f"prior_success is a probability; got {prior_success}")
        if epsilon < 0.0:
            raise ValueError(f"epsilon cannot be negative; got {epsilon}")

        owner = failure.assigned_owner
        distinct_attempted = set(attempted) | set(failure.attempted_configuration_hashes)
        retyped = (
            owner is FailureOwner.CONFIGURATION
            and len(distinct_attempted) >= _MINIMUM_DISTINCT_CONFIGURATION_FAILURES
        )
        if retyped:
            owner = FailureOwner.STRUCTURAL
        scope = AUTHORITY_SCOPE_BY_OWNER[owner]

        # The cap is absolute and is checked after the re-typing so that a stopped decision
        # still records the owner the failure actually has — an operator reading it needs to
        # know who would have acted, not who acted first.
        if attempt >= budget.maximum_attempts:
            return RecoveryDecision(
                action="STOP",
                owner=owner,
                authority_scope=scope,
                reason_code="ATTEMPT_CAP_REACHED",
                evi_lcb=None,
            )

        if retyped:
            return RecoveryDecision(
                action="REPLAN",
                owner=owner,
                authority_scope=scope,
                reason_code="CONFIGURATION_SPACE_EXHAUSTED",
                evi_lcb=None,
            )

        if owner is FailureOwner.STRUCTURAL:
            return RecoveryDecision(
                action="REPLAN",
                owner=owner,
                authority_scope=scope,
                reason_code="STRUCTURAL_FAILURE_OWNED_BY_PLANNER",
                evi_lcb=None,
            )

        if owner is FailureOwner.VERIFICATION:
            return RecoveryDecision(
                action="RESOLVE_EVIDENCE",
                owner=owner,
                authority_scope=scope,
                reason_code="VERIFICATION_CONFLICT_UNRESOLVED",
                evi_lcb=None,
            )

        if owner in {FailureOwner.SAFETY, FailureOwner.AUTHORITY, FailureOwner.RESOURCE}:
            return RecoveryDecision(
                action="ESCALATE",
                owner=owner,
                authority_scope=scope,
                reason_code="HUMAN_AUTHORITY_REQUIRED",
                evi_lcb=None,
            )

        if owner is FailureOwner.UNKNOWN:
            # Registry §5.4 stops automatic recovery on an unknown owner, and there is
            # nothing to escalate *to* either: an unclassified failure names no layer, so a
            # hand-off would be a hand-off to nobody.
            return RecoveryDecision(
                action="STOP",
                owner=owner,
                authority_scope=scope,
                reason_code="UNCLASSIFIED_FAILURE",
                evi_lcb=None,
            )

        # Everything that reaches here is reselectable in principle: CONFIGURATION,
        # CAPABILITY and ENVIRONMENT all mean "a different execution configuration might
        # work". Whether one may actually be tried is the retryable flag and then the EVI.
        if not failure.retryable:
            return RecoveryDecision(
                action="STOP",
                owner=owner,
                authority_scope=scope,
                reason_code="FAILURE_NOT_RETRYABLE",
                evi_lcb=None,
            )

        eligible = self.eligible_candidates(
            candidate_hashes=candidate_hashes,
            attempted=sorted(distinct_attempted),
            new_evidence_since=new_evidence_since,
        )
        total = len(set(candidate_hashes))
        if total == 0:
            return RecoveryDecision(
                action="STOP",
                owner=owner,
                authority_scope=scope,
                reason_code="NO_CANDIDATE_CONFIGURATIONS",
                evi_lcb=0.0,
            )

        evi_lcb = wilson_lower_bound(len(eligible), total, z=self.z) * prior_success
        if evi_lcb <= epsilon:
            return RecoveryDecision(
                action="STOP",
                owner=owner,
                authority_scope=scope,
                reason_code="EVI_BELOW_THRESHOLD",
                evi_lcb=evi_lcb,
            )
        return RecoveryDecision(
            action="RESELECT",
            owner=owner,
            authority_scope=scope,
            reason_code="EVI_ABOVE_THRESHOLD",
            evi_lcb=evi_lcb,
        )
