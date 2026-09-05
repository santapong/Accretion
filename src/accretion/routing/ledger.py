"""The conservative exploration cost ledger: SDD §9.5's budget as one inequality.

§9.5 bounds exploration by ``exploration_budget_remaining``, and the program plan's R5
(https://www.alphaxiv.org/abs/2412.06165, conservative contextual bandits) says what that
budget has to mean if it is to be a safety guarantee rather than a quota: **cumulative
explored cost must stay within a factor of ``(1 + α)`` of what the deterministic baseline
would have cost on the same rounds, at every round**. Not on average, not at the end of the
day — at every round, checked before the round is taken.

**One ledger, one key.** A ledger instance is the whole state for one
``(workspace_id, node_class)`` pair. Exploration budgets do not pool across node classes:
spending a graph's tolerance for wrong answers on cheap formatting nodes and then having none
left for the expensive planning node would be exactly the wrong trade, and a single global
counter cannot express the difference.

**Pessimism is the point, and it has three parts.** The candidate is charged at its *upper*
confidence bound and the baseline is credited at its *lower* bound, so the inequality is
evaluated against the worst arithmetic consistent with the estimates. Only explored rounds
are recorded, so the ledger never banks slack from rounds the baseline played — the paper
allows that slack, and declining it is strictly safer and much easier to audit. And an
exploration whose real cost is not known yet keeps its upper bound: an unsettled exploration
is a cost that has been incurred and not yet measured, and treating it as free until the
measurement lands is precisely how a budget gets spent twice. :meth:`CostLedger.settle`
replaces the bound with the observation, which can only ever release budget it should not
have been holding.

**Two absolute caps sit outside the inequality.** ``(1 + α)`` is a *relative* bound, and a
relative bound on an expensive baseline is a large absolute number.
:class:`ExplorationCaps` therefore bounds the count of explorations and their total cost
outright, and those bind whatever ``α`` says.

**Costs are normalised floats in ``[0, 1]``.** One exploration's cost is a fraction of the
node's ``resource_cap``, not a currency, not a token count and not a duration — the ledger
compares costs from different providers in the same breath and can only do that if the
caller has already normalised them. ``ExplorationCaps.max_cost`` is a *cumulative* budget in
that same unit and is therefore free to exceed 1.

Nothing here touches a store, a clock or a network. :meth:`CostLedger.snapshot` hands a
plain, sorted, JSON-serialisable dict to whoever wants to persist it; the persistence itself
belongs to a later PR.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

_COST_UNIT: Final = "a normalised cost must be a float in [0, 1]"


def _check_cost(name: str, value: float) -> float:
    """Refuse a cost outside ``[0, 1]``. A denormalised cost makes the inequality a lie."""
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} is {value}; {_COST_UNIT}")
    return float(value)


@dataclass(frozen=True, slots=True)
class ExplorationCaps:
    """The absolute bounds that hold no matter how generous ``α`` is.

    ``max_explore_count`` bounds how many explorations may be recorded under one
    ``(workspace_id, node_class)`` key; ``max_cost`` bounds their cumulative charged cost.
    Both are inclusive: a cap of two permits a second exploration and refuses a third.
    """

    max_explore_count: int
    max_cost: float

    def __post_init__(self) -> None:
        if self.max_explore_count < 0:
            raise ValueError(
                f"max_explore_count is {self.max_explore_count}; a cap on a count cannot be "
                "negative, and zero already means 'no exploration'"
            )
        if self.max_cost < 0:
            raise ValueError(
                f"max_cost is {self.max_cost}; a cumulative cost cap cannot be negative"
            )


@dataclass(frozen=True, slots=True)
class LedgerDecision:
    """Whether this exploration may be taken, and the binding constraint if it may not.

    ``reason`` is filled on both outcomes. The allowed case names the headroom that let it
    through, because "exploration was permitted" is a claim about an inequality and a receipt
    that recorded the permission without the inequality could not be replayed against it.
    """

    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One recorded exploration. ``observed_cost`` is ``None`` until it settles.

    Frozen on purpose: settling produces a *replacement* entry rather than mutating one, so
    there is no code path anywhere that edits a cost in place.
    """

    receipt_id: str
    cost_ucb: float
    baseline_cost_lcb: float
    observed_cost: float | None

    @property
    def charged_cost(self) -> float:
        """The upper bound while unsettled; the observation once it has landed."""
        return self.cost_ucb if self.observed_cost is None else self.observed_cost


class CostLedger:
    """The conservative-bandit inequality for one ``(workspace_id, node_class)`` pair.

    Deterministic by construction: every sum is taken over entries in sorted ``receipt_id``
    order, so two ledgers that recorded the same explorations in different orders hold the
    same floats and produce byte-identical snapshots. Nothing is iterated in insertion order.
    """

    def __init__(self, *, workspace_id: str, node_class: str) -> None:
        if not workspace_id:
            raise ValueError("workspace_id is empty; a ledger with no key cannot be scoped")
        if not node_class:
            raise ValueError("node_class is empty; a ledger with no key cannot be scoped")
        self._workspace_id = workspace_id
        self._node_class = node_class
        self._entries: dict[str, LedgerEntry] = {}

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def node_class(self) -> str:
        return self._node_class

    @property
    def explore_count(self) -> int:
        """How many explorations have been recorded, settled or not."""
        return len(self._entries)

    def _sorted_entries(self) -> list[LedgerEntry]:
        return [self._entries[receipt_id] for receipt_id in sorted(self._entries)]

    @property
    def explored_cost_sum(self) -> float:
        """Cumulative charged cost: each entry at its observation, or at its UCB if unsettled."""
        return sum(entry.charged_cost for entry in self._sorted_entries())

    @property
    def baseline_cost_lcb_sum(self) -> float:
        """Cumulative lower-bounded cost the deterministic baseline would have incurred."""
        return sum(entry.baseline_cost_lcb for entry in self._sorted_entries())

    def can_explore(
        self,
        *,
        candidate_cost_ucb: float,
        baseline_cost_lcb: float,
        alpha: float,
        caps: ExplorationCaps,
    ) -> LedgerDecision:
        """Decide whether one more exploration keeps every bound. Reads state, never writes.

        The conservative inequality is evaluated *as if the round had been taken*: the
        candidate's own baseline lower bound joins the right-hand side, because the round
        being weighed is a round the baseline would also have played. Writing it any other
        way would make the first exploration of a fresh ledger impossible, since a
        right-hand side of zero refuses every non-zero cost forever.

        ``<=`` and not ``<``: a candidate that lands exactly on the bound is inside the
        budget the objective declared, and refusing it would silently make every declared α
        slightly smaller than the number a human approved.

        Caps are checked before the inequality so that the reason names the constraint an
        operator can actually change.
        """
        candidate = _check_cost("candidate_cost_ucb", candidate_cost_ucb)
        baseline = _check_cost("baseline_cost_lcb", baseline_cost_lcb)
        if alpha < 0:
            raise ValueError(
                f"alpha is {alpha}; a negative exploration budget would demand explorations "
                "cheaper than the baseline and is not a budget"
            )

        projected_count = self.explore_count + 1
        if projected_count > caps.max_explore_count:
            return LedgerDecision(
                allowed=False,
                reason=(
                    f"max_explore_count {caps.max_explore_count} would be exceeded: this "
                    f"would be exploration {projected_count} for node class "
                    f"{self._node_class!r}"
                ),
            )

        explored = self.explored_cost_sum
        projected_cost = explored + candidate
        if projected_cost > caps.max_cost:
            return LedgerDecision(
                allowed=False,
                reason=(
                    f"max_cost {caps.max_cost} would be exceeded: charged {explored} plus "
                    f"candidate {candidate} is {projected_cost}"
                ),
            )

        budget = (1.0 + alpha) * (self.baseline_cost_lcb_sum + baseline)
        if projected_cost > budget:
            return LedgerDecision(
                allowed=False,
                reason=(
                    f"the conservative inequality would break: explored {explored} plus "
                    f"candidate {candidate} is {projected_cost}, above (1 + {alpha}) x "
                    f"baseline {self.baseline_cost_lcb_sum + baseline} = {budget}"
                ),
            )
        return LedgerDecision(
            allowed=True,
            reason=(
                f"within budget: {projected_cost} of {budget} conservative, "
                f"{projected_count} of {caps.max_explore_count} explorations, "
                f"{projected_cost} of {caps.max_cost} cost"
            ),
        )

    def record(self, receipt_id: str, cost_ucb: float, baseline_cost_lcb: float) -> LedgerEntry:
        """Book an exploration at its upper bound, against the baseline's lower bound.

        Append-only: a receipt id already in the ledger is refused rather than overwritten,
        because the same receipt recorded twice is either a double charge or a rewritten
        history and neither should be decided here.
        """
        if not receipt_id:
            raise ValueError("receipt_id is empty; an exploration must be attributable")
        if receipt_id in self._entries:
            raise ValueError(
                f"receipt {receipt_id!r} is already in this ledger; the ledger is append-only "
                "and a repeated receipt would charge the same exploration twice"
            )
        entry = LedgerEntry(
            receipt_id=receipt_id,
            cost_ucb=_check_cost("cost_ucb", cost_ucb),
            baseline_cost_lcb=_check_cost("baseline_cost_lcb", baseline_cost_lcb),
            observed_cost=None,
        )
        self._entries[receipt_id] = entry
        return entry

    def settle(self, receipt_id: str, observed_cost: float) -> LedgerEntry:
        """Replace an exploration's upper bound with what it actually cost.

        Settling can only move a charge; it never removes the entry, so the exploration keeps
        counting against ``max_explore_count`` forever. An unknown receipt raises
        :class:`KeyError` and an already-settled one raises :class:`ValueError`: a second
        observation for the same exploration is a measurement bug, and quietly taking the
        newer number would let a ledger be talked down after the fact.
        """
        entry = self._entries.get(receipt_id)
        if entry is None:
            raise KeyError(
                f"receipt {receipt_id!r} was never recorded in the ledger for "
                f"{self._workspace_id!r}/{self._node_class!r}"
            )
        if entry.observed_cost is not None:
            raise ValueError(
                f"receipt {receipt_id!r} already settled at {entry.observed_cost}; an "
                "exploration is measured once"
            )
        settled = replace(entry, observed_cost=_check_cost("observed_cost", observed_cost))
        self._entries[receipt_id] = settled
        return settled

    def snapshot(self) -> dict[str, object]:
        """A plain, sorted, JSON-serialisable picture for a later PR to persist.

        Entries come out sorted by ``receipt_id`` and the returned structure is freshly built
        on every call, so a caller may keep it, mutate it or hash it without reaching back
        into the ledger.
        """
        return {
            "workspace_id": self._workspace_id,
            "node_class": self._node_class,
            "explore_count": self.explore_count,
            "explored_cost_sum": self.explored_cost_sum,
            "baseline_cost_lcb_sum": self.baseline_cost_lcb_sum,
            "entries": [
                {
                    "receipt_id": entry.receipt_id,
                    "cost_ucb": entry.cost_ucb,
                    "baseline_cost_lcb": entry.baseline_cost_lcb,
                    "observed_cost": entry.observed_cost,
                    "settled": entry.observed_cost is not None,
                }
                for entry in self._sorted_entries()
            ],
        }
