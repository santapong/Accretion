"""Closing the exploration cost ledger once a routed node's outcome is known (SDD §9.5).

:class:`~accretion.routing.ledger.CostLedger` charges an exploration at its *upper* confidence
bound the moment it is taken, because a cost that has been incurred and not yet measured is
not a free cost — treating it as free until the measurement lands is precisely how a budget
gets spent twice. That pessimism has a price: until somebody replaces the bound with the
observation, a workspace that explored cheaply keeps paying for the possibility that it did
not. This module is the somebody.

**Why a post-node hook and not a post-route one.** A post-route hook sees a decision and no
outcome; only the post-node hook sees what the configuration actually cost. ADR-048 makes the
node's completion the one point at which an experience may be projected, and the projected
:class:`~accretion.contracts.routing.ExperienceRecord` is where the observed cost lives — so
this hook fires exactly where the number it needs first exists.

**Why it settles into a shared registry rather than writing a row.** ADR4-M7-003: the ledger
has no table. Settling therefore updates the live
:class:`~accretion.routing.bandit.LedgerRegistry` the bandit reads, and a restart forgets it
and re-charges every past exploration at its upper bound. That is the conservative direction —
a fresh process holds a *tighter* budget than the measured one, never a looser — and it is
what makes "no new table" a safe simplification rather than a lost guarantee.

**Why nothing here may raise.** :class:`~accretion.routing.stages.PostNodeHook` runs after a
node has executed and after its receipt is durable. A settlement that raised would turn a
completed node into a failed one over a bookkeeping entry whose worst-case absence is that the
budget stays where it already was.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from accretion.contracts import Run, RunNode, WorkspaceLease
from accretion.contracts.routing import (
    DecisionType,
    ExecutionConfiguration,
    ExperienceRecord,
    IndependentVerificationResult,
    NodeContract,
    RoutingDecisionReceipt,
)
from accretion.persistence.store import StateStore
from accretion.routing.bandit import NODE_CLASS_LABEL, LedgerRegistry
from accretion.routing.protocols import FrozenNode

_LOGGER = logging.getLogger(__name__)


def normalised_cost(cost: Decimal, *, node: NodeContract) -> float:
    """One node's observed cost as the ledger's unit: a fraction of its own cap, in ``[0, 1]``.

    :mod:`accretion.routing.ledger` states the unit outright — a cost is a fraction of the
    node's ``resource_cap``, not a currency, not a token count and not a duration — because
    the ledger compares costs from different providers in the same breath. The normalisation
    therefore belongs to whoever holds the node contract, which is here.

    A node whose cap is zero has no denominator. Any positive spend against a zero cap is the
    whole of a budget that did not exist, so it normalises to 1.0 and a zero spend to 0.0;
    charging such an exploration nothing would let a class of nodes explore for free forever.
    """

    cap = node.resource_cap.maximum_cost
    if cap <= 0:
        return 0.0 if cost <= 0 else 1.0
    return min(1.0, max(0.0, float(cost / cap)))


class ExplorationSettlement:
    """Replace an exploration's charged upper bound with what the node actually cost.

    ``ledgers`` is the *same* :class:`~accretion.routing.bandit.LedgerRegistry` instance the
    :class:`~accretion.routing.bandit.GuardedBandit` reads, and passing it in rather than
    building one here is the whole point: two registries over one store would each rebuild the
    same ledger from the same receipts and only one of them would ever see a settlement, so
    the budget the bandit checked would be the one nobody had measured.
    """

    def __init__(self, store: StateStore, ledgers: LedgerRegistry) -> None:
        self.store = store
        self.ledgers = ledgers

    async def after_node(
        self,
        *,
        run: Run,
        node: RunNode,
        frozen: FrozenNode,
        receipt: RoutingDecisionReceipt,
        configuration: ExecutionConfiguration,
        outcome: IndependentVerificationResult | None,
        lease: WorkspaceLease | None,
    ) -> None:
        """Settle this node's exploration if it was one and if its cost has been measured.

        Four ways this is a no-op, and each of them is a normal state rather than an error:
        the decision was not an exploration; the receipt names no ledger key, so it was
        written before this milestone; no experience record has been projected for the
        execution yet, because ADR-048 defers that to the run's own verdict; or the
        exploration has already been settled, which happens whenever a node's completion is
        observed twice.
        """

        del run, node, configuration, outcome, lease
        try:
            await self._settle(frozen=frozen, receipt=receipt)
        except Exception:
            # A node that ran and was verified must not be failed by its own bookkeeping.
            _LOGGER.exception(
                "exploration settlement failed for receipt %s", receipt.contract_id
            )

    async def _settle(
        self, *, frozen: FrozenNode, receipt: RoutingDecisionReceipt
    ) -> None:
        if receipt.decision_type is not DecisionType.EXPLORE:
            return
        node_class = receipt.labels.get(NODE_CLASS_LABEL)
        if not node_class:
            return
        record = await self._record_for(
            workspace_id=receipt.workspace_id,
            execution_instance_id=frozen.execution_instance_id,
        )
        if record is None:
            return
        ledger = await self.ledgers.ledger(
            workspace_id=receipt.workspace_id, node_class=node_class
        )
        observed = normalised_cost(record.outcomes.cost, node=frozen.node_contract)
        try:
            ledger.settle(receipt.contract_id, observed)
        except (KeyError, ValueError):
            # KeyError: the receipt is not in this ledger, which means its labels named
            # another node class and settling it here would charge the wrong budget.
            # ValueError: it is already settled, and the ledger refuses a second measurement
            # of one exploration on purpose — a ledger that could be talked down after the
            # fact is not a ledger.
            _LOGGER.debug(
                "exploration %s was not settled at %s", receipt.contract_id, observed
            )

    async def _record_for(
        self, *, workspace_id: str, execution_instance_id: str
    ) -> ExperienceRecord | None:
        """The latest experience record projected for one node execution, or ``None``.

        The *latest* by ``(created_at, contract_id)`` because an execution's record is
        revisable (v0.4's experience revisions), and the cost a settlement should use is the
        one the current revision states rather than the first that was written.
        """

        records = [
            record
            for record in await self.store.list_experience_records(workspace_id=workspace_id)
            if record.source_node_execution_id == execution_instance_id
        ]
        if not records:
            return None
        return max(records, key=lambda item: (item.created_at, item.contract_id))


__all__ = ["ExplorationSettlement", "normalised_cost"]
