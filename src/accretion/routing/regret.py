"""Constrained regret, computed from receipts, candidates and outcomes and nothing else.

The v0.1 ACR-ARCH benchmark already established the arithmetic: score every executed
configuration on one task, take the best, subtract what the selector actually got, and never
let the difference go below zero. This module keeps that shape and changes the two things
v0.4 needs changed.

**The best is taken over a registered subset, not over everything.** Protocol §8.3's oracle
ranges over configurations that were executed under matched conditions. A candidate that was
never admissible for the node is not part of the bound, so ``oracle_subset`` is an argument
and not an assumption.

**An invalid selection is priced, and also counted.** A policy that selects a configuration
the node was never eligible for has not merely done badly; it has done something a guarded
router must never do. Pricing it at the registered ``invalid_action_penalty`` puts it in the
utility column where the comparison lives, and incrementing
:attr:`SafetyCounters.invalid_selections` puts it in the safety column where a gate can see
it. Doing only the first would let a method buy safety violations with utility, which is the
failure mode the protocol's separate safety criteria exist to prevent, and doing only the
second would leave the utility table quietly flattering the method that cheated.

**Only three inputs, on purpose.** :func:`regret_from_receipts` takes stored receipts, stored
candidates and observed outcomes. It holds no reference to the runner that produced them, no
store handle and no cache, so the number it returns is a function of what was *persisted* —
which is the only version of the number an auditor can check. A benchmark whose regret came
partly from a live object and partly from a row would be reproducible only on the machine
that ran it.

**On the weight vector.** Utility is ``quality - w_cost·cost - w_latency·latency``: quality is
the numeraire and the other two are priced against it. ``UtilityWeights.quality`` is
therefore *not* applied as a fourth multiplier — see the note on :func:`utility`, which is
the one place in this module where a reader is owed an explanation rather than a rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from accretion.contracts.routing import (
    ConfigurationCandidate,
    RoutingDecisionReceipt,
    UtilityWeights,
)

ORACLE_SUBSET_LABEL = "router_benchmark_oracle_subset"
"""The candidate label that says "this configuration is in protocol §8.3's registered subset".

The subset has to be recoverable from what was *persisted*, or the audit path would depend
on a corpus file the store never saw. A header label is where a stored candidate carries a
benchmark's registration: it is inside the content hash, so it cannot be added after the
fact, and it needs no schema change to a sealed contract. A task whose candidates carry no
such label falls back to its whole admissible set, which is the right reading of a corpus
that registered everything it ran."""

ORACLE_SUBSET_REGISTERED = "REGISTERED"
"""The only value :data:`ORACLE_SUBSET_LABEL` is read for. Anything else means "not in it"."""

_PLACES = 9
"""Decimal places every reported quantity is rounded to.

Not cosmetic. The runner and the receipt path perform the same additions in the same order,
so they agree bit for bit, and rounding here keeps a stored report from carrying eighteen
digits of float noise that a reviewer would have to squint past."""


def outcome_key(task_id: str, configuration_id: str) -> str:
    """The key one observed outcome is stored under: a task and a configuration.

    JSON has no tuple keys and the receipt path has no tuples either — a receipt names its
    ``routing_request_id`` and its ``selected_configuration_id`` — so the pair is encoded
    once, here, and both the corpus loader and :func:`regret_from_receipts` use it. Two call
    sites building the key by hand is how the two paths come to disagree about a cell.

    The task id's length is written into the key before the id itself. A plain separator is
    ambiguous the moment an id can contain it — ``("a::b", "c")`` and ``("a", "b::c")`` would
    name one cell — and while today's ids do not contain one, a keying scheme that is correct
    only because of a convention held somewhere else is a keying scheme that silently merges
    two outcomes on the day the convention changes.
    """

    return f"{len(task_id)}:{task_id}::{configuration_id}"


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one configuration did on one task, as the replay trace recorded it.

    ``cost`` and ``latency`` are the normalised, budget-relative quantities utility is
    computed from; ``latency_ms`` is the wall-clock number they were normalised from and is
    carried so a report can quote a duration rather than a ratio. ``verified``,
    ``false_accept`` and ``invalid`` are outside utility entirely: they feed the gates and
    the safety counters, which are reported separately.
    """

    quality: float
    cost: float
    latency: float
    latency_ms: int
    verified: bool
    false_accept: bool
    invalid: bool


@dataclass(frozen=True, slots=True)
class SafetyCounters:
    """The safety column: events, not rates, and never folded into utility.

    ``deferred_to_human`` counts receipts that selected nothing. Those rows carry no regret —
    there is no selection to regret — but dropping them silently would let a method improve
    its mean by abstaining, so the abstention is counted where a gate can find it.
    """

    invalid_selections: int = 0
    unverified_selections: int = 0
    false_acceptances: int = 0
    deferred_to_human: int = 0

    def plus(
        self,
        *,
        invalid: bool = False,
        unverified: bool = False,
        false_accept: bool = False,
        deferred: bool = False,
    ) -> SafetyCounters:
        """This counter with one row's events added. Frozen, so it returns a new value."""

        return SafetyCounters(
            invalid_selections=self.invalid_selections + int(invalid),
            unverified_selections=self.unverified_selections + int(unverified),
            false_acceptances=self.false_acceptances + int(false_accept),
            deferred_to_human=self.deferred_to_human + int(deferred),
        )


@dataclass(frozen=True, slots=True)
class RegretRow:
    """One task's regret, with the raw quantities the scalar was built from.

    The raw ``quality``, ``cost`` and ``latency`` travel beside ``selected_utility`` because
    a utility is a weighted opinion and the three numbers under it are facts: a reader who
    disagrees with the objective's weights can re-derive the comparison, and a reader who
    finds the number surprising can see which term produced it.

    There is deliberately no ``verified`` or ``false_accept`` field here. Those belong to the
    gates, and a regret row that carried them would be one refactor away from a utility that
    quietly included them.
    """

    task_id: str
    project_id: str
    selected_candidate_id: str
    selected_utility: float
    oracle_candidate_id: str
    oracle_utility: float
    regret: float
    invalid: bool
    quality: float
    cost: float
    latency: float


@dataclass(frozen=True, slots=True)
class RegretReport:
    """Every row, the summaries a report quotes, and the safety column beside them."""

    rows: tuple[RegretRow, ...]
    total_regret: float
    mean_regret: float
    regret_by_project: Mapping[str, float]
    safety: SafetyCounters

    def pairs_by_project(self) -> dict[str, list[float]]:
        """Per-project regret values, in row order: the clustered bootstrap's input shape."""

        grouped: dict[str, list[float]] = {}
        for row in self.rows:
            grouped.setdefault(row.project_id, []).append(row.regret)
        return grouped


def utility(outcome: Outcome, weights: UtilityWeights) -> float:
    """``quality - w_cost·cost - w_latency·latency`` — the protocol's per-task utility.

    Quality is the numeraire. ``weights.cost`` and ``weights.latency`` say how much quality
    a unit of each is worth to this objective, which is what makes the three commensurable
    at all; ``weights.quality`` is *not* applied as a fourth multiplier, because scaling the
    numeraire would rescale every utility, every regret and every reported gap by the same
    factor while changing none of the orderings — a difference that shows up only when two
    projects with different quality weights are compared, and then shows up as an artefact.

    ``UtilityWeights`` still carries ``quality`` and this function still takes the whole
    vector, so an objective that wants the three weights normalised to sum to one can be
    written and read without loss. That reading is left to the ranker (registry §9.1 stage
    10), which is where normalisation belongs.
    """

    return round(
        outcome.quality - weights.cost * outcome.cost - weights.latency * outcome.latency,
        _PLACES,
    )


def constrained_regret(
    task_outcomes: Mapping[str, Outcome],
    selected_id: str,
    oracle_subset: Sequence[str],
    weights: UtilityWeights,
    invalid_penalty: float,
    *,
    task_id: str,
    project_id: str,
) -> RegretRow:
    """One task's regret against the best configuration in the registered oracle subset.

    ``task_outcomes`` maps a configuration id to what it did on this task, and covers only
    configurations that were admissible for it. A ``selected_id`` outside that mapping is by
    construction an **invalid selection**: the policy chose something the node was never
    eligible for. It scores ``-invalid_penalty`` rather than being dropped, and the row says
    so in :attr:`RegretRow.invalid` so the caller can count the safety event as well as pay
    the price. An outcome that was executed but flagged ``invalid`` by the trace is treated
    identically; a policy should not be better off for having had its invalid choice run.

    Regret is clamped at zero, following the v0.1 benchmark: a selected configuration cannot
    beat a maximum taken over a set that contains it, so a negative value would mean the
    oracle subset and the outcomes disagree about what was executed, and reporting a negative
    regret would hide that disagreement inside a mean.
    """

    if invalid_penalty < 0:
        raise ValueError(f"the invalid-action penalty is non-negative; got {invalid_penalty}")
    ranked = sorted(
        candidate_id for candidate_id in oracle_subset if candidate_id in task_outcomes
    )
    if not ranked:
        raise ValueError(
            f"task {task_id!r} has no observed outcome for any configuration in its oracle "
            "subset, so there is nothing to regret against"
        )
    oracle_id = max(ranked, key=lambda candidate_id: utility(task_outcomes[candidate_id], weights))
    oracle_utility = utility(task_outcomes[oracle_id], weights)

    observed = task_outcomes.get(selected_id)
    invalid = observed is None or observed.invalid
    if observed is None:
        selected_utility = round(-invalid_penalty, _PLACES)
        quality, cost, latency = 0.0, 0.0, 0.0
    elif observed.invalid:
        selected_utility = round(-invalid_penalty, _PLACES)
        quality, cost, latency = observed.quality, observed.cost, observed.latency
    else:
        selected_utility = utility(observed, weights)
        quality, cost, latency = observed.quality, observed.cost, observed.latency

    return RegretRow(
        task_id=task_id,
        project_id=project_id,
        selected_candidate_id=selected_id,
        selected_utility=selected_utility,
        oracle_candidate_id=oracle_id,
        oracle_utility=oracle_utility,
        regret=round(max(0.0, oracle_utility - selected_utility), _PLACES),
        invalid=invalid,
        quality=quality,
        cost=cost,
        latency=latency,
    )


@dataclass(frozen=True, slots=True)
class TaskSelection:
    """One task, its admissible set, its observed outcomes and what a policy selected.

    The single input shape both regret paths reduce to. The benchmark runner builds these
    from corpus rows and :func:`regret_from_receipts` builds them from stored receipts and
    candidates; from here on the arithmetic is one function, so the two paths cannot drift
    into computing slightly different numbers and each blaming the other.

    ``selected_id`` is ``None`` for a decision that selected nothing. ``task_outcomes``
    covers the admissible configurations only, which is what makes an out-of-set
    ``selected_id`` mean "invalid" without a second flag saying so.

    ``eligible_ids`` and ``oracle_subset_ids`` are two different questions and are kept
    apart. The first is admissibility — what the node was allowed to run, and therefore what
    an invalid selection is measured against. The second is protocol §8.3's registered subset
    — what was executed under matched conditions, and therefore what the post-hoc bound may
    be taken over. A configuration can be perfectly admissible and still be outside the
    oracle's reach; collapsing the two would either let an unmatched run define the bound or
    charge a penalty for choosing something the node genuinely allowed.
    """

    task_id: str
    project_id: str
    selected_id: str | None
    eligible_ids: tuple[str, ...]
    oracle_subset_ids: tuple[str, ...]
    task_outcomes: Mapping[str, Outcome]
    selected_outcome: Outcome | None


def regret_over_selections(
    selections: Sequence[TaskSelection],
    weights: UtilityWeights,
    invalid_penalty: float,
) -> RegretReport:
    """The report for a sequence of :class:`TaskSelection` rows: rows, totals, safety.

    Every safety event is decided here and nowhere else. An invalid selection is counted
    from the row the arithmetic produced rather than from the caller's opinion of it, so a
    caller cannot pay the penalty without also raising the counter, or raise the counter
    without paying.
    """

    rows: list[RegretRow] = []
    safety = SafetyCounters()
    for selection in selections:
        if selection.selected_id is None:
            safety = safety.plus(deferred=True)
            continue
        if not selection.eligible_ids:
            raise ValueError(
                f"task {selection.task_id!r} has no eligible candidate on record; regret "
                "cannot be computed against an unknown admissible set"
            )
        row = constrained_regret(
            selection.task_outcomes,
            selection.selected_id,
            selection.oracle_subset_ids or selection.eligible_ids,
            weights,
            invalid_penalty,
            task_id=selection.task_id,
            project_id=selection.project_id,
        )
        rows.append(row)
        observed = selection.selected_outcome
        safety = safety.plus(
            invalid=row.invalid,
            unverified=observed is None or not observed.verified,
            false_accept=observed is not None and observed.false_accept,
        )
    return summarise(rows, safety)


def summarise(rows: Sequence[RegretRow], safety: SafetyCounters) -> RegretReport:
    """Roll per-task rows up into the report, in a deterministic row order.

    Rows are sorted by project and then task so that two computations of the same benchmark
    produce not merely equal numbers but an equal *object*, which is what lets the cold-store
    test assert equality on the whole report rather than field by field.
    """

    ordered = tuple(sorted(rows, key=lambda row: (row.project_id, row.task_id)))
    total = round(sum(row.regret for row in ordered), _PLACES)
    by_project: dict[str, float] = {}
    for row in ordered:
        running = by_project.get(row.project_id, 0.0) + row.regret
        by_project[row.project_id] = round(running, _PLACES)
    return RegretReport(
        rows=ordered,
        total_regret=total,
        mean_regret=round(total / len(ordered), _PLACES) if ordered else 0.0,
        regret_by_project=MappingProxyType(dict(sorted(by_project.items()))),
        safety=safety,
    )


def regret_from_receipts(
    *,
    receipts: Sequence[RoutingDecisionReceipt],
    candidates: Sequence[ConfigurationCandidate],
    outcomes: Mapping[str, Outcome],
    weights: UtilityWeights,
    invalid_penalty: float,
) -> RegretReport:
    """Recompute the whole regret report from stored records alone.

    The three inputs are exactly the three things a store holds: the receipts that say what
    was chosen, the candidates that say what was admissible, and the observed outcomes keyed
    by :func:`outcome_key`. Nothing else is consulted — not the policy that produced the
    decision, not the runner that ran it, not a clock. That is what makes this function the
    audit path: a reviewer can populate an empty store from a pull request's fixtures, call
    it, and get the number the report claimed, or find out that they cannot.

    Candidates are grouped by ``routing_request_id`` and the admissible set for a task is the
    ``hard_eligible`` ones. A receipt naming a configuration outside that set is an invalid
    selection and is priced and counted as one. A receipt that selected nothing —
    ``HUMAN_REVIEW_REQUIRED``, the only decision type permitted to — contributes no regret
    row and increments :attr:`SafetyCounters.deferred_to_human`.
    """

    admissible: dict[str, dict[str, ConfigurationCandidate]] = {}
    registered: dict[str, set[str]] = {}
    for candidate in candidates:
        if not candidate.hard_eligible:
            continue
        configuration_id = candidate.configuration.contract_id
        admissible.setdefault(candidate.routing_request_id, {})[configuration_id] = candidate
        if candidate.labels.get(ORACLE_SUBSET_LABEL) == ORACLE_SUBSET_REGISTERED:
            registered.setdefault(candidate.routing_request_id, set()).add(configuration_id)

    selections: list[TaskSelection] = []
    for receipt in sorted(receipts, key=lambda item: item.routing_request_id):
        task_id = receipt.routing_request_id
        selected_id = receipt.selected_configuration_id
        eligible_ids = tuple(sorted(admissible.get(task_id, {})))
        subset_ids = tuple(sorted(registered.get(task_id, set())))
        if selected_id is not None and not eligible_ids:
            raise ValueError(
                f"receipt {receipt.contract_id!r} names routing request {task_id!r}, for "
                "which no eligible candidate was stored; regret cannot be computed against "
                "an unknown admissible set"
            )
        selections.append(
            TaskSelection(
                task_id=task_id,
                project_id=receipt.project_id or "",
                selected_id=selected_id,
                eligible_ids=eligible_ids,
                oracle_subset_ids=subset_ids,
                task_outcomes={
                    candidate_id: outcomes[outcome_key(task_id, candidate_id)]
                    for candidate_id in eligible_ids
                    if outcome_key(task_id, candidate_id) in outcomes
                },
                selected_outcome=(
                    None
                    if selected_id is None
                    else outcomes.get(outcome_key(task_id, selected_id))
                ),
            )
        )
    return regret_over_selections(selections, weights, invalid_penalty)
