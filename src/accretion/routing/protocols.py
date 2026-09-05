"""The two seams every later v0.4 lane programs against, frozen in M1 (SDD §8, §9).

M2 builds the router. M3 builds the feedback pipeline. Both have to be reachable from
``RunManager``, and both are built after M1 — which means that without this module each lane
would invent its own calling convention, `RunManager` would grow two more shapes of
attribute, and the integration PR would be a rewrite of whichever lane guessed differently.
So the seams are frozen here, before either implementation exists, and the plan records this
file's sha256 so that a later PR changing it has to say so out loud.

**Why protocols and not base classes.** ``NodeRoutingService`` will be implemented by a class
that owns a store, a snapshot builder, a candidate builder and a ranker; ``FeedbackPipeline``
by one that owns the P7 experience service. Neither should inherit from anything declared
here, because a base class is an import edge and an import edge from M1 into M2's internals
is the coupling AC4-M1-005 spends its whole budget removing. A ``Protocol`` gives
``RunManager`` a checked type with no edge at all, which is the same argument
``CapabilityNodeInvoker`` and ``SearchNodeExecutor`` already make in
``services/run_manager.py``.

**Why the freeze is a hash and not a promise.** Every method below is named in a later
milestone's plan. A signature that drifted between M1 and M2 would not fail here — nothing in
M1 calls these — it would fail in the integration PR, as a type error in a file nobody
touched. Recording the digest in ``docs/releases/v0.4/m1-plan.md`` makes the drift visible in
the diff that causes it.

``RoutingSnapshot`` is re-exported from :mod:`accretion.routing.snapshot` rather than
redefined: the snapshot a router is handed and the snapshot a compatibility rule reads are
the same object, and two names for it would eventually become two types.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from accretion.contracts import (
    AcceptancePolicy,
    ErrorSummary,
    PrincipalRef,
    Run,
    RunNode,
    Task,
    VerificationResult,
    WorkflowNodeSpec,
    WorkflowTemplate,
)
from accretion.contracts.routing import (
    ExecutionConfiguration,
    ExperienceRecord,
    FailureEvent,
    IndependentVerificationResult,
    NodeContract,
    ObjectiveContractRef,
    RecommendedAction,
    ResourceBudget,
    RoutingDecisionReceipt,
    VerificationSpec,
    VerificationState,
)
from accretion.experience.models import ExperienceSourceKind
from accretion.routing.snapshot import RoutingSnapshot


class RoutingMode(StrEnum):
    """SDD §11.1. How much of the learned router a workspace has turned on.

    Three values and not a pair of booleans, because the three states are mutually exclusive
    and a boolean pair has a fourth combination that means nothing. §11.1 also makes the
    progression one-directional in practice — a workspace earns ``AUTO`` by passing shadow
    evaluation in ``SHADOW`` — and a mode that could be spelled two ways would make the gate
    that checks it ambiguous.
    """

    AUTO = "AUTO"
    """The learned router selects, within the gates. The end state, and never the default."""

    SHADOW = "SHADOW"
    """The learned router selects and the decision is recorded; the baseline is executed.

    The only mode in which a router's selections can be measured against outcomes it did not
    cause, which is what M6's shadow evaluation needs and why the mode exists at all.
    """

    BASELINE_ONLY = "BASELINE_ONLY"
    """The deterministic selector decides and nothing learned is consulted.

    The default for a workspace that has never been evaluated, and the state a promotion
    rollback returns one to.
    """


@dataclass(frozen=True, slots=True)
class FrozenNode:
    """One graph-node execution, frozen into contracts, before any configuration exists.

    ADR-041 makes one graph-node execution instance one routable action and ADR-044 freezes
    verification semantics *before* routing. This dataclass is the result of doing both: the
    node's requirements, what must be verified about it, the objective revision that
    authorised it, and the identity the whole attempt is filed under.

    Frozen and slotted for the reason :class:`RoutingSnapshot` is: a router that mutated its
    frozen node would produce a receipt pinning a ``node_contract_hash`` that no longer
    describes anything, and the failure would surface as an unreproducible decision rather
    than as an error.

    ``execution_instance_id`` is carried beside the contract that already holds it because
    the feedback pipeline is handed this identity without the contract — a failure event and
    an experience record are both keyed by it — and re-deriving it from
    ``node_contract.execution_instance_id`` in three places is three chances to disagree.
    """

    node_contract: NodeContract
    verification_spec: VerificationSpec
    objective_ref: ObjectiveContractRef
    execution_instance_id: str


class RecoveryDecision(Protocol):
    """§9.7's answer to "what happens after this failure", as a structural placeholder.

    **This is a placeholder and is expected to be replaced.** M3 owns
    ``accretion.feedback.recovery``, which does not exist on develop; the real
    ``RecoveryDecision`` will be a contract or a frozen dataclass there. Declaring it as a
    ``Protocol`` here rather than as a concrete class is what lets that happen without
    editing this file: M3's class satisfies this structurally by having the two members, so
    the frozen signature of :meth:`FeedbackPipeline.recovery_decision` survives, and no
    import edge from M1 into M3 is created in the meantime.

    Two members, because §9.7 makes exactly two decisions. ``action`` is the typed
    recommendation — who owns the failure now and whether a retry is allowed at all — and is
    the existing :class:`~accretion.contracts.routing.RecommendedAction` rather than a new
    shape, so a recovery decision and the failure event it answers speak one vocabulary.
    ``next_configuration_hash`` is §9.7's other rule made checkable: equivalent failed
    configurations must not repeat without new evidence, and two configurations are
    equivalent exactly when their ``configuration_hash`` signatures are equal, so the next
    attempt names the signature it will use or ``None`` when there is no next attempt.
    """

    @property
    def action(self) -> RecommendedAction: ...

    @property
    def next_configuration_hash(self) -> str | None: ...


class NodeRoutingService(Protocol):
    """The router as ``RunManager`` sees it: freeze, snapshot, route, replay, amend.

    Seven methods, and the split between them is the §8 lifecycle rather than a convenience.
    :meth:`freeze` and :meth:`snapshot` produce the two *immutable inputs* — what the node
    needs, and what the world was — and neither may read the other, because a snapshot that
    depended on a node contract could not be shared between the nodes of one graph.
    :meth:`route` consumes both and produces a receipt. Everything after it operates on a
    persisted receipt and never on the inputs again, which is what makes §8.2's guarantee —
    repeated requests with identical immutable inputs return the same receipt — a property of
    the data rather than of the caller's discipline.
    """

    async def claim_dispatch(
        self, *, receipt: RoutingDecisionReceipt, run: Run
    ) -> ExecutionConfiguration:
        """Atomically claim an unamended persisted decision before any side effect."""
        ...

    async def latest_receipt(
        self, *, frozen: FrozenNode, run: Run
    ) -> RoutingDecisionReceipt | None:
        """Restore the latest decision for a frozen execution, including amendments."""
        ...

    async def freeze(
        self,
        *,
        run: Run,
        task: Task,
        node: RunNode,
        spec: WorkflowNodeSpec,
        template: WorkflowTemplate,
        policy: AcceptancePolicy,
        graph_revision: int,
        attempt: int,
    ) -> FrozenNode:
        """Freeze one node execution into a node contract and a verification spec.

        ``attempt`` is a parameter and not derived from the node's state because a retry is a
        *different* routable action under ADR-041: it gets its own execution instance id, its
        own receipt and its own experience record, and a router that reused the first
        attempt's identity would attribute the retry's outcome to the configuration that
        failed. ``graph_revision`` is passed for the same reason a
        :class:`~accretion.contracts.routing.NodeContract` carries the
        ``(run_graph_id, graph_revision)`` pair: a node re-planned into a new revision is not
        the node that was frozen before.
        """
        ...

    async def snapshot(
        self, *, workspace_id: str, project_id: str | None, task: Task
    ) -> RoutingSnapshot:
        """Observe the registry, the runtimes, the connections and the policy, once."""
        ...

    async def route(
        self,
        *,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        mode: RoutingMode,
        run: Run,
    ) -> RoutingDecisionReceipt:
        """Select a complete configuration and persist the receipt that explains it.

        ``mode`` is per call rather than per service because §11.1 scopes it to a workspace
        and a project adapter, both of which vary between the runs one service instance
        handles. A service that captured its mode at construction would route a
        ``BASELINE_ONLY`` workspace under whatever mode the process started with.
        """
        ...

    async def replay(self, routing_request_id: str) -> RoutingDecisionReceipt | None:
        """The receipt already recorded for ``routing_request_id``, or ``None``.

        ``None`` and not an exception: §8.2 makes a repeated request return the same receipt,
        so a caller asks this *before* routing, and "no receipt yet" is the ordinary answer on
        the first pass rather than an error.
        """
        ...

    async def configuration_for(
        self, receipt: RoutingDecisionReceipt
    ) -> ExecutionConfiguration:
        """The configuration ``receipt`` selected, dereferenced from the store.

        A receipt carries ``selected_configuration_id`` and ``selected_configuration_hash``
        rather than the tuple itself, so dispatch has to dereference; doing it here means the
        hash is checked in one place. Raises when the receipt selected nothing — a caller
        that reached dispatch with a ``decision_type`` that chose no configuration has a bug
        upstream, and returning ``None`` would let it dispatch nothing quietly.
        """
        ...

    async def override(
        self,
        *,
        receipt_id: str,
        candidate_id: str,
        reason_code: str,
        reason: str,
        expected_receipt_version: int,
        principal: PrincipalRef,
    ) -> RoutingDecisionReceipt:
        """Replace a selection with an operator's, as a new receipt.

        ``expected_receipt_version`` makes the write a compare-and-set: two operators
        overriding one decision must not silently produce a last-writer-wins outcome on a
        record that is meant to be an audit trail. ``principal`` is required and has no
        default, because an override with no author is an override no one can be asked about.
        """
        ...

    async def cancel(
        self, *, receipt_id: str, principal: PrincipalRef
    ) -> RoutingDecisionReceipt:
        """Withdraw a routing decision that has not been dispatched, as a new receipt.

        A cancellation is a decision and is recorded as one, not as a deletion: §7.8 makes
        receipts immutable, and a routing history with a hole in it cannot be replayed.
        """
        ...


class FeedbackPipeline(Protocol):
    """What happens to a routing decision after the node ran (SDD §9.5-§9.7, §10.1).

    Four methods on one seam rather than four seams, because all four read the same
    execution instance and write records keyed by it, and splitting them would let a caller
    record an experience for a run whose failure was never classified.

    The order is the lifecycle: :meth:`record_local` when the node's own verification
    finishes, :meth:`classify_failure` when it did not succeed, :meth:`recovery_decision` to
    decide what happens next, and :meth:`record_final` once — and only once — the run reaches
    a terminal state. ``record_final`` is last for a reason ADR-048 makes explicit: an
    experience is only evidence a router may learn from after the *run* has been judged, not
    after the node has.
    """

    async def record_local(
        self,
        *,
        run: Run,
        task: Task,
        execution_instance_id: str,
        session_id: str | None,
        results: Sequence[VerificationResult],
        policy: AcceptancePolicy,
        configuration_hash: str,
    ) -> IndependentVerificationResult:
        """Fold the node's verifier results into one §7.9 independent verification result.

        ``session_id`` is carried because OQ-418 makes a *separate context* mandatory for
        independent verification: the record has to be able to show which session produced
        the work and which did not, and a pipeline that could not see the session could not
        record the independence it claims. ``None`` is the honest value for a node that ran
        without one.
        """
        ...

    async def record_final(
        self,
        *,
        run: Run,
        status: VerificationState,
        source: ExperienceSourceKind,
        principal: PrincipalRef,
    ) -> list[ExperienceRecord]:
        """Project one experience record per routed node of a finished run.

        A list because a run has many nodes and §9.6's attribution divides one outcome
        between them; returning a single record would force a caller to loop and would make
        the attribution somebody else's job.

        ``status`` is a :class:`~accretion.contracts.routing.VerificationState` and not a
        ``RunState``: §7.10's ``final_run_status`` grades whether the run's *claims were
        verified*, which is not the same question as whether the process terminated. A run
        can succeed mechanically and fail verification, and the record has to say which.
        ``source`` is the P7 vocabulary the projection is keyed by (ADR-054 b), unchanged.
        """
        ...

    async def classify_failure(
        self,
        *,
        run: Run,
        execution_instance_id: str,
        error: ErrorSummary | None,
        local: IndependentVerificationResult | None,
        attempted_configuration_hashes: Sequence[str],
    ) -> FailureEvent:
        """Type one failure and assign the layer that owns recovering from it (§7.11).

        Both ``error`` and ``local`` are nullable and they are not alternatives: a node can
        fail with an exception and no verification, with a verification failure and no
        exception, or with both. A classifier handed only one of them would have to guess at
        the other, and §9.7 routes recovery by the classification.

        ``attempted_configuration_hashes`` accumulates across attempts, which is what makes
        §9.7's "equivalent failed configurations must not repeat" enforceable rather than
        aspirational.
        """
        ...

    async def recovery_decision(
        self,
        *,
        failure: FailureEvent,
        budget: ResourceBudget,
        candidate_hashes: Sequence[str],
    ) -> RecoveryDecision:
        """Decide what happens after ``failure``, within ``budget``.

        ``candidate_hashes`` are the configuration signatures still available. The decision
        may pick none of them: registry §5.4 makes safety, authority and unknown failures
        stop automatic recovery outright, and a pipeline that always produced a next
        configuration would drive a retry past the stop that exists to prevent one.
        """
        ...


__all__ = [
    "FeedbackPipeline",
    "FrozenNode",
    "NodeRoutingService",
    "RecoveryDecision",
    "RoutingMode",
    "RoutingSnapshot",
]
