"""The collaborators SDD §9.4's routing stages are assembled from, and their defaults.

M2 shipped the §9.1 pipeline with two seam comments where §9.4's later stages belong:
``# stage-9-gate / stage-11-behavior`` above the deterministic selector, and
``# post-route-shadow / active-version`` after the receipt commit. A comment is not a
seam — it is a place where the next milestone edits the same 160-line method — so this
module replaces both with named, typed extension points that
:class:`~accretion.routing.service.DefaultNodeRoutingService` is constructed from.

**Why five protocols and not one strategy object.** The five stages are owned by four
different milestones. M5 owns scoring (:class:`CandidateScorer`), M6 owns shadow recording
(:class:`PostRouteHook`) and the post-node experience projection
(:class:`PostNodeHook`), M7 owns behaviour (:class:`BehaviorPolicy`) and M8 owns which
model version is live (:class:`ActiveVersionResolver`). One object with five methods would
mean four milestones editing one class; five protocols mean each lane adds a module and one
constructor argument. Evidence retrieval (:class:`EvidenceRetriever`) is separate again
because §15.1 makes "evidence retrieval unavailable" a *distinct* degradation from "router
model unavailable", and a retriever folded into the scorer could not be failed on its own.

**Why every default is inert rather than absent.** ``None`` for a collaborator would put an
``if`` in the routing method for every stage, and the branch not taken is the branch that is
never tested. :class:`NoEvidence`, :class:`StatusActiveVersionResolver` with no active
version, and :class:`DeterministicBehavior` together reproduce M2's behaviour exactly, so
the deterministic path is the *same* code path as the learned one with the learned parts
answering honestly that they have nothing to add.

**Why hooks may not fail a route.** :meth:`PostRouteHook.after_receipt` runs after the
receipt has been committed. By then the decision is a durable, replayable fact and the
caller has already been promised it; a shadow recorder that raised would turn a successful
routing decision into an error the caller would retry, and the retry would return the very
receipt the first call had already produced. Exceptions from a post-route hook are therefore
logged and swallowed, which is stated here because it is a property callers depend on rather
than an implementation detail of the service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from accretion.contracts import PrincipalRef, Run, RunNode, WorkspaceLease
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ContractSignature,
    ExecutionConfiguration,
    ExperienceRecord,
    IndependentVerificationResult,
    NodeContract,
    ObjectiveContract,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RoutingContext,
    RoutingDecisionReceipt,
)
from accretion.persistence.store import StateStore
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.protocols import FrozenNode
from accretion.routing.selector import DETERMINISTIC_PROPENSITY, SelectionResult
from accretion.routing.snapshot import RoutingSnapshot

DEGRADED_LABEL = "degraded"
"""The one label key §15.1's availability ladder is reported under.

One key and not four booleans, because §15.1's entries are alternatives with different
consequences and a receipt that carried two of them at once would not say which one
decided the outcome. :func:`worst_degradation` picks the entry that did.
"""

WORKSPACE_MODEL_UNAVAILABLE = "WORKSPACE_MODEL_UNAVAILABLE"
"""§15.1: no loadable workspace prior, so the audited deterministic baseline decided."""

EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
"""§15.1: retrieval failed, so no history was used. History was not invented instead."""

VOCABULARY_MISMATCH = "VOCABULARY_MISMATCH"
"""§7.12: the prior was fitted under another token table, so its columns mean nothing here."""

ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
"""§15.1: no project adapter, so the workspace prior decided with reduced confidence."""

DEGRADED_PRECEDENCE: tuple[str, ...] = (
    WORKSPACE_MODEL_UNAVAILABLE,
    VOCABULARY_MISMATCH,
    EVIDENCE_UNAVAILABLE,
    ADAPTER_UNAVAILABLE,
)
"""Most consequential first. The order is §15.1's, read as "how much was not consulted".

A missing workspace model means *nothing learned ran*; a mismatched vocabulary means the
model that ran could not be trusted to mean what its columns say, which is the same outcome
reached for a different reason; a failed retrieval means the model ran on no history; a
missing adapter means it ran on history but without the project's own correction. Reporting
the least consequential of several would understate the decision.
"""


def worst_degradation(*reasons: str | None) -> str | None:
    """The most consequential §15.1 entry among ``reasons``, or ``None`` if there is none.

    Raises ``ValueError`` for a reason that is not in :data:`DEGRADED_PRECEDENCE`: an
    unknown ladder entry is a typo, and silently ranking it last would make a receipt claim
    a milder degradation than the one that happened.
    """

    ranked = [reason for reason in reasons if reason is not None]
    for reason in ranked:
        if reason not in DEGRADED_PRECEDENCE:
            raise ValueError(
                f"{reason!r} is not one of §15.1's availability ladder entries "
                f"{list(DEGRADED_PRECEDENCE)}"
            )
    if not ranked:
        return None
    return min(ranked, key=DEGRADED_PRECEDENCE.index)


def node_signature(node: NodeContract, *, objective_digest: str) -> ContractSignature:
    """SDD §7.10's retrieval key, derived from the frozen node contract.

    ``objective_digest`` is a parameter rather than read from
    ``node.objective_contract_ref``, which is nullable: a node contract may exist before its
    objective revision is pinned, and a signature is only meaningful once it is. The caller
    holds the non-nullable copy — the routing service holds
    :attr:`~accretion.routing.protocols.FrozenNode.objective_ref` and the scorer holds the
    objective contract itself — so the value arrives already proven present rather than
    defaulted to something that would silently make two different objectives one subject.

    The other digests are read off the contract rather than recomputed: the verification spec
    hash is the one its reference carries, and it is the value an experience record projected
    from a run of this node will carry too. Only ``capability_digest`` has to be computed,
    because a node's required capability *set* is a list of references and not a digest
    anywhere else.

    That computation is over ``(capability_id, capability_version, required_scope)`` sorted,
    and not over the list as written: two nodes that require the same capabilities at the
    same scopes are the same retrieval subject, and a digest that followed the order the
    planner happened to emit would make one node's evidence invisible to the other.
    ``version_range`` is deliberately excluded — it is the *acceptable* range, while the
    reference is what was actually required, and two nodes whose ranges differ but whose
    resolved requirements are equal exchange evidence under §7.10.
    """

    return ContractSignature(
        node_kind=node.node_kind,
        objective_digest=objective_digest,
        capability_digest=content_hash(
            sorted(
                [
                    requirement.capability.capability_id,
                    requirement.capability.capability_version,
                    requirement.required_scope,
                ]
                for requirement in node.required_capabilities
            ),
            exclude=(),
        ),
        verification_spec_hash=node.verification_spec_ref.content_hash,
        risk_class=node.allowed_risk_class,
    )


@dataclass(frozen=True, slots=True)
class ActiveVersions:
    """Which router and adapter a decision is being made *by*, as ids and as labels.

    Four fields and not two, because a receipt needs both halves and they are not the same
    thing. ``router_version_id`` is a stored :class:`~accretion.contracts.routing.
    RouterModelVersion` id or ``None``; ``router_label`` is what goes into the receipt's
    ``workspace_router_version`` and into
    :func:`~accretion.routing.identity.routing_request_id`, and it is never ``None`` —
    a workspace with no learned model is still routed by something, and that something is
    the audited deterministic router, named.
    """

    router_version_id: str | None
    adapter_version_id: str | None
    router_label: str
    adapter_label: str | None


class ActiveVersionResolver(Protocol):
    """SDD §8.3: which workspace router and project adapter this decision is pinned to.

    A protocol rather than a store query, because §10.3 makes the answer a *promotion*
    decision (M8) rather than a property of the rows: a version can be ACTIVE and still be
    held back by a circuit breaker, and the service must not have to know that.
    """

    async def resolve(self, *, workspace_id: str, project_id: str) -> ActiveVersions:
        """The versions in force for one workspace and project, at this instant."""
        ...


class EvidenceRetriever(Protocol):
    """SDD §9.4's third stage: the verified experience this node may learn from.

    ``signature`` and not the node contract, because §7.10 makes the contract signature the
    retrieval key: two nodes exchange evidence exactly when their signatures are equal, and
    a retriever handed the whole contract could quietly widen that rule. ``principal`` is
    required because §10.1's permission proof is part of what makes a record eligible, and a
    retriever that could not see who is asking could not honour it.
    """

    async def retrieve(
        self,
        *,
        workspace_id: str,
        project_id: str | None,
        signature: ContractSignature,
        principal: PrincipalRef,
        as_of: datetime,
    ) -> Sequence[ExperienceRecord]:
        """Every record this node may treat as evidence, in a deterministic order."""
        ...


@dataclass(frozen=True, slots=True)
class ScoredSlate:
    """The candidates after §9.3's outcome estimation, and what it took to produce them.

    ``evidence_ids`` is what the receipt's ``experience_refs`` becomes, so a decision can
    always name the records that moved it — and an empty tuple is the honest answer for a
    cold start rather than an absence the reader has to interpret. ``labels`` carries the
    §15.1 degradation ladder the scorer walked down; the service merges them into the
    receipt's labels rather than inventing its own vocabulary for the same facts.
    """

    candidates: tuple[ConfigurationCandidate, ...]
    calibration_version: str
    evidence_ids: tuple[str, ...]
    labels: Mapping[str, str]


class CandidateScorer(Protocol):
    """SDD §9.3: attach predicted outcomes, uncertainty and a lower bound to each candidate.

    ``evidence_by_hash`` is keyed by ``configuration_hash`` and not by candidate id, because
    §9.2 canonicalises behaviourally equivalent candidates by configuration signature: two
    candidates with one signature must be scored against one body of evidence, and a
    per-candidate mapping would let them disagree.
    """

    async def score(
        self,
        *,
        context: RoutingContext,
        candidates: Sequence[ConfigurationCandidate],
        node: NodeContract,
        objective: ObjectiveContract,
        versions: ActiveVersions,
        evidence_by_hash: Mapping[str, Sequence[ExperienceRecord]],
    ) -> ScoredSlate:
        """Re-score ``candidates`` in place of the cold-start prior, or degrade to it."""
        ...


@dataclass(frozen=True, slots=True)
class BehaviorDecision:
    """SDD §9.1 stage 11: what was selected, with what propensity, and why.

    ``propensity`` is separate from the selection because §10.2's off-policy evaluation
    needs the probability the *behaviour policy* assigned to the action it took, and a
    deterministic policy assigning 1.0 is a real measurement rather than a placeholder — an
    estimator that read a missing propensity as one would be right by accident here and
    wrong the moment exploration is switched on.
    """

    selection: SelectionResult
    propensity: float
    labels: Mapping[str, str]


class BehaviorPolicy(Protocol):
    """SDD §9.5's guarded exploration seam, as it will be reached from M7.

    ``baseline`` is passed alongside the slate because §9.5 defines the safe action set
    relative to a deterministic choice that already passed the lower-confidence gate: a
    policy that explores must be able to say what it explored *instead of*, and re-deriving
    that inside every policy would put the floor gate in two places.
    """

    async def select(
        self,
        *,
        context: RoutingContext,
        slate: ScoredSlate,
        baseline: SelectionResult,
        node: NodeContract,
        objective: ObjectiveContract,
        snapshot: RoutingSnapshot,
    ) -> BehaviorDecision:
        """Choose the action actually taken, within the gates ``baseline`` already applied."""
        ...


class PostRouteHook(Protocol):
    """Runs after the receipt commit. Exceptions are logged and never propagate.

    The receipt is already durable when this is called, which is what makes M6's shadow
    recording safe to attach here: a shadow decision is a record *about* a decision that was
    made, so it cannot be allowed to unmake it.
    """

    async def after_receipt(
        self,
        *,
        run: Run,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        context: RoutingContext,
        receipt: RoutingDecisionReceipt,
        slate: ScoredSlate,
    ) -> None:
        """Observe one committed routing decision. Must not raise; must not mutate it."""
        ...


class PostNodeHook(Protocol):
    """Invoked by the run manager once a routed node has finished executing (M6.2 wires it).

    Separate from :class:`PostRouteHook` because the two fire at different times and see
    different things: a post-route hook sees a decision and no outcome, a post-node hook sees
    the outcome and the configuration that produced it. ADR-048 makes the second the only
    point at which an experience may be projected.
    """

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
        """Observe one completed routed node execution. Must not raise."""
        ...


class NoEvidence:
    """The retriever a workspace has before M6 attaches a real one: nothing, honestly.

    §15.1 forbids fabricating history when retrieval is unavailable, and "no retriever
    configured" is the strongest form of unavailable. Returning an empty sequence rather
    than raising keeps the cold-start path a *normal* path: a fresh project genuinely has no
    evidence, and that is not an error anywhere in §9.4.
    """

    async def retrieve(
        self,
        *,
        workspace_id: str,
        project_id: str | None,
        signature: ContractSignature,
        principal: PrincipalRef,
        as_of: datetime,
    ) -> Sequence[ExperienceRecord]:
        return ()


class StatusActiveVersionResolver:
    """The ACTIVE row per scope, or the audited deterministic router named explicitly.

    "Latest" is ``(created_at, contract_id)`` and not store order, for the reason every
    other list in this package gives: the two store backends must agree, and a resolver that
    took the first row a backend happened to return would pin one receipt to one model on
    Postgres and to another in memory.

    With no ACTIVE row the labels are ``("deterministic-router/1", None)`` — the same string
    :data:`~accretion.routing.catalog.WORKSPACE_ROUTER_VERSION` M2 wrote into every receipt.
    That is what makes this resolver's introduction a no-op for an unpromoted workspace:
    the request id derives from the labels, so a changed label would change every
    ``routing_request_id`` and orphan every stored receipt.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    async def resolve(self, *, workspace_id: str, project_id: str) -> ActiveVersions:
        workspace_versions = await self.store.list_router_model_versions(
            workspace_id=workspace_id
        )
        project_versions = await self.store.list_router_model_versions(
            workspace_id=workspace_id, project_id=project_id
        )
        router = self._latest_active(workspace_versions, RouterScope.TEAM_WORKSPACE)
        adapter = self._latest_active(project_versions, RouterScope.PROJECT_ADAPTER)
        return ActiveVersions(
            router_version_id=router,
            adapter_version_id=adapter,
            router_label=router if router is not None else WORKSPACE_ROUTER_VERSION,
            adapter_label=adapter,
        )

    @staticmethod
    def _latest_active(
        versions: Sequence[RouterModelVersion], scope: RouterScope
    ) -> str | None:
        active = [
            version
            for version in versions
            if version.scope is scope and version.status is RouterStatus.ACTIVE
        ]
        if not active:
            return None
        return max(active, key=lambda item: (item.created_at, item.contract_id)).contract_id


class DeterministicBehavior:
    """Take the deterministic selection, with the propensity that statement deserves: 1.0.

    Not a no-op object: it returns the *measured* propensity of a policy that had no choice,
    which is what §10.2's estimators need to treat M2's history and M7's exploration as one
    logged policy rather than as two incomparable regimes.
    """

    async def select(
        self,
        *,
        context: RoutingContext,
        slate: ScoredSlate,
        baseline: SelectionResult,
        node: NodeContract,
        objective: ObjectiveContract,
        snapshot: RoutingSnapshot,
    ) -> BehaviorDecision:
        return BehaviorDecision(
            selection=baseline, propensity=DETERMINISTIC_PROPENSITY, labels={}
        )


__all__ = [
    "ADAPTER_UNAVAILABLE",
    "DEGRADED_LABEL",
    "DEGRADED_PRECEDENCE",
    "EVIDENCE_UNAVAILABLE",
    "VOCABULARY_MISMATCH",
    "WORKSPACE_MODEL_UNAVAILABLE",
    "ActiveVersionResolver",
    "ActiveVersions",
    "BehaviorDecision",
    "BehaviorPolicy",
    "CandidateScorer",
    "DeterministicBehavior",
    "EvidenceRetriever",
    "NoEvidence",
    "PostNodeHook",
    "PostRouteHook",
    "ScoredSlate",
    "StatusActiveVersionResolver",
    "node_signature",
    "worst_degradation",
]
