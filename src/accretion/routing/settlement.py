"""Check exploration accounting after a node; durable outcomes own settlement.

The hook can log an unavailable account without failing a completed node.
Admission always reconstructs the account again, so hook delivery, retries and
process lifetime cannot release or erase observed spend.
"""

from __future__ import annotations

import logging

from accretion.contracts import Run, RunNode, WorkspaceLease
from accretion.contracts.routing import (
    DecisionType,
    ExecutionConfiguration,
    IndependentVerificationResult,
    RoutingDecisionReceipt,
)
from accretion.persistence.store import StateStore
from accretion.routing.bandit import NODE_CLASS_LABEL, LedgerRegistry
from accretion.routing.ledger import normalised_cost
from accretion.routing.protocols import FrozenNode

_LOGGER = logging.getLogger(__name__)


class ExplorationSettlement:
    """Eagerly validate durable accounting; never issue an in-process-only credit."""

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
        """Validate the account if its durable outcome has arrived; never fail the node."""

        del run, node, configuration, outcome, lease
        try:
            await self._settle(frozen=frozen, receipt=receipt)
        except Exception:
            # A node that ran and was verified must not be failed by its own bookkeeping.
            _LOGGER.exception("exploration settlement failed for receipt %s", receipt.contract_id)

    async def _settle(self, *, frozen: FrozenNode, receipt: RoutingDecisionReceipt) -> None:
        if receipt.decision_type is not DecisionType.EXPLORE:
            return
        # No volatile credit is issued here. The next admission independently
        # rebuilds from the same durable receipt/node/experience chain, even if
        # this hook never runs or the process crashes immediately afterwards.
        node_class = receipt.labels.get(NODE_CLASS_LABEL)
        if node_class:
            await self.ledgers.ledger(workspace_id=receipt.workspace_id, node_class=node_class)


__all__ = ["ExplorationSettlement", "normalised_cost"]
