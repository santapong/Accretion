"""The whole :class:`~accretion.routing.protocols.FeedbackPipeline`, as a store-only service.

M3a builds the four methods and persists what they produce; M3b hands them to the run manager.
Nothing here reaches for a runtime, a provider or a wall clock it was not given, which is what
makes the lifecycle testable end to end against a :class:`~accretion.persistence.store.MemoryStore`
before any executor calls it.

**The four methods are the node's lifecycle, in order.**
:meth:`~DefaultFeedbackPipeline.record_local` when the node's own verification finishes;
:meth:`~DefaultFeedbackPipeline.classify_failure` when it did not succeed;
:meth:`~DefaultFeedbackPipeline.recovery_decision` to decide what happens next; and
:meth:`~DefaultFeedbackPipeline.record_final` once — and only once — the *run* has been judged.
ADR-048 puts ``record_final`` last on purpose: an experience is evidence a router may learn from
only after the run has been graded, not after the node has, and a pipeline that projected an
experience at node completion would be teaching the router from work that was later thrown away.

**A local verdict and a final projection are two records in two tables, and that is the point.**
``AC4-M3-025``: ``list_verification_results`` answers "what did the verifier decide about this
node", ``list_experience_records`` answers "what may a router learn from this run", and the two
questions have different answers for the same node — a node can pass its own verification inside
a run that failed. Collapsing them into one row would make the second question unanswerable.

**Three seams the Protocol does not carry, and where each is taken from instead.**

* :class:`~accretion.feedback.recovery.RecoveryGuard` needs ``attempt``, ``attempted``,
  ``new_evidence_since`` and ``prior_success``; the Protocol's ``recovery_decision`` carries a
  failure, a budget and the candidate hashes. All four are derived from the store here rather
  than added to the frozen signature — the attempt from the node contract's own label, the
  attempted set from the failure event, the new evidence by counting experience records younger
  than the failure per configuration hash, and the prior success from
  :func:`~accretion.routing.features.summarize_evidence`.
* ``feedback.recovery.RecoveryDecision`` is not structurally
  ``routing.protocols.RecoveryDecision``: the guard reports an action *literal* and an authority
  scope, and the Protocol asks for a typed
  :class:`~accretion.contracts.routing.RecommendedAction` and a next configuration hash. Neither
  file is edited; :class:`PipelineRecoveryDecision` adapts one to the other and keeps the guard's
  own decision reachable beside it, so nothing that made the decision is discarded in translating
  it.
* Events need a run. ``AgentEvent`` requires a ``run_id`` and ``PostgresStore.append_event`` locks
  the run row, so the two §12 events this milestone owns are emitted only when the run exists,
  exactly as ``routing/train.py``'s ``_announce`` does. The durable record is the row; the event
  is a notification about it, and a synthesised run id would be a lie in the trace to make one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from accretion.contracts import (
    AcceptancePolicy,
    ErrorSummary,
    EventType,
    EvidenceClass,
    PrincipalRef,
    Provider,
    Run,
    Task,
    VerificationResult,
)
from accretion.contracts.refs import EvidenceRef, VerifierRef
from accretion.contracts.routing import (
    Criticality,
    ExecutionConfiguration,
    ExperienceOutcomes,
    ExperienceRecord,
    FailureEvent,
    IndependentVerificationResult,
    NodeContract,
    RecommendedAction,
    ResourceBudget,
    RoutingDecisionReceipt,
    VerificationSpec,
    VerificationState,
    Visibility,
)
from accretion.experience.models import ExperienceSourceKind
from accretion.feedback.attribution import AttributionInput, DependencyAttributor
from accretion.feedback.experience import (
    ExperienceMaterializer,
    ExperienceProjector,
)
from accretion.feedback.failures import FailureClassifier, FailureSignals
from accretion.feedback.recovery import (
    DEFAULT_EPSILON,
    AuthorityScope,
    RecoveryDecision,
    RecoveryGuard,
)
from accretion.feedback.verification import IndependentVerificationRecorder
from accretion.persistence.store import StateStore
from accretion.routing.features import summarize_evidence
from accretion.routing.identity import contract_signature_for, workspace_for_run
from accretion.routing.protocols import FeedbackPipeline
from accretion.runtimes.common import make_event

__all__ = [
    "ADAPTER_VERSION",
    "DEFAULT_PRIOR_SUCCESS",
    "OUTPUT_CLAIM_PREFIX",
    "DefaultFeedbackPipeline",
    "PipelineRecoveryDecision",
    "as_feedback_pipeline",
]

ADAPTER_VERSION = "feedback-pipeline-v1"
"""Stamped on every event this service emits, so a trace can say which writer produced it."""

DEFAULT_PRIOR_SUCCESS = 0.5
"""The guard's ``prior_success`` when no in-domain experience has been recorded yet.

One half and not zero. Zero would drive ``EVI_v1 = untried_fraction × prior_success`` to zero on
a cold project and stop every first recovery before it started, which is §9.8's cold-start case
turned into a refusal to act. One half is the honest prior for a question no observation has been
made about, and the Wilson bound on the untried fraction still carries all the caution.
"""

OUTPUT_CLAIM_PREFIX = "output."
"""How :class:`~accretion.routing.identity.VerificationSpecBuilder` names an output claim.

Duplicated here as a constant rather than imported from a private helper, and duplicated on
purpose with this docstring attached: ``FailureSignals.schema_findings`` counts findings against
*required output* claims only, and the only thing in the repository that can tell an output claim
from a verifier claim is the prefix that builder wrote. If the builder's naming changes, this
constant is the second place that has to change, and the test that pins the count is what says so.
"""

_PRODUCER_SESSION_FALLBACK = "\x00no-session"
"""What a node that ran without an agent session is compared against for independence.

Never equal to any real session id, because a session id cannot contain a NUL. A node with no
session and a verifier with no session must not come out as "the verifier ran in the producer's
session"; comparing two ``None`` values would do exactly that, and the fallback makes the
comparison structurally false instead of relying on the check's own null handling.
"""


@dataclass(frozen=True, slots=True)
class PipelineRecoveryDecision:
    """The guard's answer in the vocabulary ``FeedbackPipeline.recovery_decision`` promises.

    Structurally satisfies :class:`~accretion.routing.protocols.RecoveryDecision` — the two
    members that Protocol declares — while carrying the whole
    :class:`~accretion.feedback.recovery.RecoveryDecision` beside them. Nothing is thrown away in
    the translation: ``guard`` still holds the reason code, the owner, the authority scope and
    the EVI lower bound that produced the recommendation, and an operator asking *why* a retry
    stopped reads that rather than inferring it from an action code.

    ``next_configuration_hash`` is ``None`` whenever the guard did not say ``RESELECT``. That is
    §9.7's other rule made checkable: a decision that names no next configuration cannot drive a
    retry, and one that names one has already had the attempted set subtracted from its
    candidates.
    """

    action: RecommendedAction
    next_configuration_hash: str | None
    guard: RecoveryDecision

    @property
    def authority_scope(self) -> AuthorityScope:
        """Under whose authority the next step happens, straight from the guard.

        Exposed as a property rather than copied into a field, so that the scope this object
        reports and the scope the guard decided cannot drift apart.
        """

        return self.guard.authority_scope


class DefaultFeedbackPipeline:
    """The four §9.5–§9.7 methods over a store, a clock and one principal.

    ``created_by`` is the pipeline's identity and authors every record it writes on nobody's
    behalf — a verification result, a failure event. Records a principal *asked* for carry that
    principal: ``record_final`` takes one and the experience records it projects are authored by
    it, because sharing an experience is an act of permission and §10.1 wants to know whose.
    """

    def __init__(
        self,
        store: StateStore,
        experiences: ExperienceMaterializer,
        clock: Callable[[], datetime],
        created_by: PrincipalRef,
    ) -> None:
        self.store = store
        self.clock = clock
        self.created_by = created_by
        self.projector = ExperienceProjector(store, experiences, clock, created_by)
        self.attributor = DependencyAttributor(store)
        self.classifier = FailureClassifier(created_by=created_by)
        self.recorder = IndependentVerificationRecorder()
        self.guard = RecoveryGuard()

    # ------------------------------------------------------------ record_local

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
        verifier_session_ids: Mapping[str, str | None] | None = None,
        evidence: Mapping[str, EvidenceRef] | None = None,
    ) -> IndependentVerificationResult:
        """Fold this node's v0.1 verdicts into one sealed §7.9 independent result, and store it.

        ``verifier_session_ids`` and ``evidence`` are keyword arguments with defaults, which is
        what lets this satisfy the frozen Protocol signature while still being able to express
        the two things the Protocol cannot say. Omitting the first declares every verifier
        in-process — ``None`` per verifier, which
        :class:`~accretion.feedback.verification.IndependenceCheck` treats as independent because
        a verifier that never entered a session cannot have entered the producer's. Supplying it
        is how a caller reports a verifier that *did* run in a session, including the producer's,
        which is the OQ-418 violation the recorder turns into ``ERROR``.

        ``evidence`` defaults to what can be read out of the v0.1 refs themselves: the
        repository's verifiers content-address their evidence as ``<name>-sha256:<digest>``, and
        a ref in that shape resolves to a ``DIGITAL`` reference carrying that digest. A ref in
        any other shape resolves to **nothing**, and a claim it was the only support for comes
        back uncovered — INCONCLUSIVE at coverage zero. That is the mapper's documented
        fail-closed behaviour and it is the right default: inventing a content digest for
        evidence whose content this layer never saw would put a false measurement inside a
        record that exists to be audited.

        ``configuration_hash`` is not stored on the §7.9 record — it has no field for one — and
        is required by the Protocol because the caller holding a verdict must be holding the
        configuration it is about. It is carried into the event payload, where it is the only
        link between a verdict and the execution surface that produced it until ``record_final``
        writes the experience record that joins them permanently.
        """

        workspace_id = await workspace_for_run(self.store, run)
        node = await self._node_for(workspace_id, task, execution_instance_id)
        spec = await self.store.get_verification_spec(
            node.verification_spec_ref.verification_spec_id
        )
        if spec is None:
            raise KeyError(node.verification_spec_ref.verification_spec_id)
        sessions: Mapping[str, str | None] = (
            verifier_session_ids
            if verifier_session_ids is not None
            else {result.verifier_id: None for result in results}
        )
        record = self.recorder.record(
            spec=spec,
            results=results,
            execution_instance_id=execution_instance_id,
            producer_session_id=session_id or _PRODUCER_SESSION_FALLBACK,
            verifier_session_ids=sessions,
            verification_spec_hash=spec.content_hash,
            verifier=_verifier_ref(policy, results),
            workspace_id=workspace_id,
            project_id=task.envelope.project_id,
            clock=self.clock,
            created_by=self.created_by,
            evidence=evidence if evidence is not None else _resolve_evidence(results),
            producer_runtime=run.provider.value,
            prior_results=await self._prior_results(
                workspace_id, task.envelope.project_id, execution_instance_id
            ),
        )
        stored = await self.store.put_verification_result(record)
        await self._emit(
            run,
            native_type="verification.result.recorded",
            normalized_type=EventType.VERIFICATION_RESULT_RECORDED,
            payload={
                "verification_result_id": stored.contract_id,
                "execution_instance_id": execution_instance_id,
                "configuration_hash": configuration_hash,
                "status": stored.status.value,
            },
        )
        return stored

    # ------------------------------------------------------------ record_final

    async def record_final(
        self,
        *,
        run: Run,
        status: VerificationState,
        source: ExperienceSourceKind,
        principal: PrincipalRef,
        outcomes: Mapping[str, ExperienceOutcomes] | None = None,
        visibility: Visibility = Visibility.PROJECT,
    ) -> list[ExperienceRecord]:
        """One experience record per *routed* node of a finished run, attributed before written.

        Routed and not executed: the receipts are the list of nodes a router actually decided
        about, read through ``list_routing_receipts_for_run_graph``. A node that ran without a
        receipt is not evidence about a routing decision, and projecting it would put an outcome
        into the training set that no configuration choice is answerable for.

        Attribution is computed over the whole set *before* the first record is written, so the
        first generation of every row is already attributed. Projecting them unattributed and
        revising afterwards would leave an eligible, unattributed row in
        ``list_experience_records`` for every run — and §10.1's snapshot would then be reading the
        stale generation alongside the fresh one.

        ``source`` is the P7 vocabulary the projection is keyed by and is carried into the event
        rather than onto the record: :class:`~accretion.contracts.routing.ExperienceRecord`
        declares no ``source_kind`` because the P7 experience it is keyed by already has one
        (ADR-054 b), and re-declaring it here would be the duplicate registry §21 forbids.
        """

        workspace_id = await workspace_for_run(self.store, run)
        graph = await self.store.get_run_graph(run.run_id)
        if graph is None:
            return []
        receipts = await self.store.list_routing_receipts_for_run_graph(
            workspace_id=workspace_id, run_graph_id=graph.run_graph_id
        )
        nodes = {
            node.immutable_hash: node
            for node in await self.store.list_node_contracts(
                workspace_id=workspace_id, project_id=run.project_id
            )
            if node.run_graph_id == graph.run_graph_id
        }
        locals_by_instance = {
            result.execution_instance_id: result
            for result in await self.store.list_verification_results(
                workspace_id=workspace_id, project_id=run.project_id
            )
        }
        failures_by_instance = {
            event.execution_instance_id: event
            for event in await self.store.list_failure_events(
                workspace_id=workspace_id, project_id=run.project_id
            )
        }
        configurations = {
            candidate.configuration.contract_id: candidate.configuration
            for candidate in await self.store.list_configuration_candidates(
                workspace_id=workspace_id, project_id=run.project_id
            )
        }

        routed: list[tuple[RoutingDecisionReceipt, NodeContract, ExecutionConfiguration]] = []
        for receipt in receipts:
            node = nodes.get(receipt.node_contract_hash)
            if node is None or receipt.selected_configuration_id is None:
                continue
            configuration = configurations.get(receipt.selected_configuration_id)
            if configuration is None:
                continue
            routed.append((receipt, node, configuration))

        summaries = await self.attributor.compute_for_run(
            run,
            [
                AttributionInput(
                    execution_instance_id=node.execution_instance_id,
                    node_id=node.node_id,
                    node_key=node.labels.get("node_key", node.node_id),
                    attempt=_attempt_of(node),
                    local_status=(
                        locals_by_instance[node.execution_instance_id].status
                        if node.execution_instance_id in locals_by_instance
                        else VerificationState.PENDING
                    ),
                    final_status=status,
                )
                for _, node, _ in routed
            ],
        )

        measured: Mapping[str, ExperienceOutcomes] = outcomes or {}
        records: list[ExperienceRecord] = []
        for receipt, node, configuration in routed:
            local = locals_by_instance.get(node.execution_instance_id)
            failure = failures_by_instance.get(node.execution_instance_id)
            records.append(
                await self.projector.project(
                    run=run,
                    node=node,
                    receipt=receipt,
                    configuration=configuration,
                    local=local,
                    final_status=status,
                    principal=principal,
                    attribution=summaries.get(node.execution_instance_id),
                    outcomes=measured.get(node.execution_instance_id),
                    failure_type=failure.failure_type if failure is not None else None,
                    visibility=visibility,
                    permission_scope=visibility,
                )
            )
        if records:
            await self._emit(
                run,
                native_type="experience.created",
                normalized_type=EventType.EXPERIENCE_CREATED,
                payload={
                    "experience_record_ids": [record.contract_id for record in records],
                    "final_run_status": status.value,
                    "source_kind": source.value,
                    "eligible_count": sum(
                        1 for record in records if record.eligible_for_learning
                    ),
                },
            )
        return records

    # -------------------------------------------------------- classify_failure

    async def classify_failure(
        self,
        *,
        run: Run,
        execution_instance_id: str,
        error: ErrorSummary | None,
        local: IndependentVerificationResult | None,
        attempted_configuration_hashes: Sequence[str],
    ) -> FailureEvent:
        """Type one failure, assign the layer that owns it, and store the §7.11 event.

        ``error`` and ``local`` are not alternatives and neither is required: a node can fail
        with an exception and no verdict, with a verdict and no exception, or with both, and the
        rule table reads whichever signals arrived. Nothing is inferred from the message text —
        it is quoted into the rationale for a human and no rule matches on it.

        ``schema_findings`` is counted from the frozen spec rather than from the claim results
        alone, because a :class:`~accretion.contracts.routing.ClaimResult` carries no criticality
        and the taxonomy's rule is about *required* output claims. With no node contract or no
        spec reachable the count is zero, which types the failure by its other signals instead of
        by a guess at this one.
        """

        workspace_id = await workspace_for_run(self.store, run)
        node = await self._node_by_instance(workspace_id, run, execution_instance_id)
        spec: VerificationSpec | None = None
        if node is not None:
            spec = await self.store.get_verification_spec(
                node.verification_spec_ref.verification_spec_id
            )
        signals = FailureSignals(
            error_code=error.code if error is not None else None,
            error_message=error.message if error is not None else "",
            local_status=local.status if local is not None else None,
            conflict_count=len(local.conflict_refs) if local is not None else 0,
            schema_findings=_schema_findings(spec, local),
            attempted_configuration_hashes=tuple(attempted_configuration_hashes),
        )
        event = self.classifier.classify(
            signals=signals,
            execution_instance_id=execution_instance_id,
            workspace_id=workspace_id,
            project_id=run.project_id,
            clock=self.clock,
        )
        return await self.store.put_failure_event(event)

    # ------------------------------------------------------- recovery_decision

    async def recovery_decision(
        self,
        *,
        failure: FailureEvent,
        budget: ResourceBudget,
        candidate_hashes: Sequence[str],
        epsilon: float = DEFAULT_EPSILON,
    ) -> PipelineRecoveryDecision:
        """Ask the §9.7 guard, with the four inputs it needs and the Protocol does not carry.

        ``attempt`` comes from the node contract's own ``attempt`` label and falls back to one:
        a failure whose node contract cannot be found is a first attempt as far as anything
        knows, and guessing higher would spend the budget on a node that has not used it.

        ``new_evidence_since`` counts experience records for a configuration hash that were
        sealed *after* the failure was. That is §9.7's escape hatch made concrete — a failed
        configuration may be tried again when something has been learned since it failed — and
        counting records older than the failure would let the evidence that was already in front
        of the router when it failed count as new.

        ``prior_success`` is the verified success rate over in-domain experience for this node's
        signature, or :data:`DEFAULT_PRIOR_SUCCESS` when there is none.
        """

        node = await self._node_by_instance(
            failure.workspace_id, None, failure.execution_instance_id
        )
        attempt = _attempt_of(node) if node is not None else 1
        records = await self.store.list_experience_records(
            workspace_id=failure.workspace_id, project_id=failure.project_id
        )
        new_evidence_since = _evidence_since(records, failure.created_at)
        prior_success = DEFAULT_PRIOR_SUCCESS
        if node is not None:
            summary = summarize_evidence(
                records,
                signature=contract_signature_for(node),
                configuration_hash=candidate_hashes[0] if candidate_hashes else "",
                as_of=self.clock(),
            )
            if summary.verified_success_rate is not None:
                prior_success = summary.verified_success_rate
        decision = self.guard.decide(
            failure=failure,
            budget=budget,
            attempt=attempt,
            candidate_hashes=candidate_hashes,
            attempted=list(failure.attempted_configuration_hashes),
            new_evidence_since=new_evidence_since,
            prior_success=prior_success,
            epsilon=epsilon,
        )
        next_hash: str | None = None
        if decision.action == "RESELECT":
            eligible = self.guard.eligible_candidates(
                candidate_hashes=candidate_hashes,
                attempted=list(failure.attempted_configuration_hashes),
                new_evidence_since=new_evidence_since,
            )
            next_hash = eligible[0] if eligible else None
        return PipelineRecoveryDecision(
            action=RecommendedAction(
                action_code=decision.authority_scope,
                owner=decision.owner,
                rationale=(
                    f"§9.7 guard {decision.reason_code}: {decision.action} under "
                    f"{decision.authority_scope} authority, owned by {decision.owner.value}"
                ),
                retry_allowed=decision.action == "RESELECT",
            ),
            next_configuration_hash=next_hash,
            guard=decision,
        )

    # ---------------------------------------------------------------- internals

    async def _node_for(
        self, workspace_id: str, task: Task, execution_instance_id: str
    ) -> NodeContract:
        """The node contract this execution instance belongs to, or ``KeyError``.

        Raising rather than returning ``None``: ``record_local`` cannot record independence
        against a spec it cannot find, and a verdict recorded against a guessed spec would claim
        coverage of claims nobody froze.
        """

        for node in await self.store.list_node_contracts(
            workspace_id=workspace_id, project_id=task.envelope.project_id
        ):
            if node.execution_instance_id == execution_instance_id:
                return node
        raise KeyError(execution_instance_id)

    async def _node_by_instance(
        self, workspace_id: str, run: Run | None, execution_instance_id: str
    ) -> NodeContract | None:
        """The same lookup where absence is an answer rather than an error.

        ``classify_failure`` and ``recovery_decision`` both have to work on a node whose contract
        is unreachable — a pruned graph, a failure raised before the freeze completed — and both
        degrade to a weaker but honest answer rather than refusing to classify a real failure.
        """

        for node in await self.store.list_node_contracts(
            workspace_id=workspace_id,
            project_id=run.project_id if run is not None else None,
        ):
            if node.execution_instance_id == execution_instance_id:
                return node
        return None

    async def _prior_results(
        self, workspace_id: str, project_id: str, execution_instance_id: str
    ) -> list[IndependentVerificationResult]:
        """Every verdict already recorded about this execution instance, oldest first.

        Handed to the recorder so that a second verifier disagreeing with a first produces a
        ``conflict_ref`` rather than a silent overwrite of the verdict: §7.9's conflict detection
        is between *records*, and a recorder that only ever saw one could never find one.
        """

        return [
            result
            for result in await self.store.list_verification_results(
                workspace_id=workspace_id, project_id=project_id
            )
            if result.execution_instance_id == execution_instance_id
        ]

    async def _emit(
        self,
        run: Run,
        *,
        native_type: str,
        normalized_type: EventType,
        payload: dict[str, object],
    ) -> None:
        """Emit a §12 event when there is a run to emit it against, and otherwise not at all.

        ``routing/train.py``'s ``_announce`` precedent, for its reason: the event store is
        run-scoped end to end and ``PostgresStore.append_event`` locks the run row, so an event
        for a run that is not in the store cannot be written and must not be faked. The row this
        method was called about is already durable by the time it runs.
        """

        stored_run = await self.store.get_run(run.run_id)
        if stored_run is None:
            return
        await self.store.append_event(
            make_event(
                run_id=run.run_id,
                session_id=stored_run.session_id or "ses_pending",
                provider=Provider.DETERMINISTIC,
                native_type=native_type,
                normalized_type=normalized_type,
                payload=dict(payload),
                adapter_version=ADAPTER_VERSION,
            )
        )


def as_feedback_pipeline(pipeline: DefaultFeedbackPipeline) -> FeedbackPipeline:
    """The Protocol conformance, asserted where a type checker will read it.

    ``FeedbackPipeline`` is a structural Protocol, so nothing at runtime ever checks that this
    class satisfies it, and nothing would notice a drifted argument name until M3b tried to wire
    the two together. This function is the check: ``mypy src`` fails here the moment a keyword is
    renamed, a return type moves or a method is dropped, and it costs one identity function.

    It also pins the two extensions that are deliberately compatible rather than accidental —
    ``record_local``'s ``verifier_session_ids`` and ``evidence``, and ``record_final``'s
    ``outcomes`` and ``visibility`` — because a keyword-only parameter *with a default* keeps a
    method a subtype of the Protocol's, and one without a default would not.
    """

    return pipeline


def _attempt_of(node: NodeContract) -> int:
    """The attempt number the freezer stamped onto the node contract, defaulting to one."""

    try:
        value = int(node.labels.get("attempt", "1"))
    except ValueError:
        return 1
    return value if value >= 1 else 1


def _schema_findings(
    spec: VerificationSpec | None, local: IndependentVerificationResult | None
) -> int:
    """Failed **required output** claims, which is what the taxonomy counts (``FailureSignals``).

    A finding against an optional claim is a quality signal and not a structural failure, and a
    finding against a *verifier* claim is what ``local_status`` already says. Only the
    intersection — required, output-shaped, failed — is a schema finding, which is why this needs
    the spec: criticality lives there and nowhere else.
    """

    if spec is None or local is None:
        return 0
    required_outputs = {
        claim.claim_id
        for claim in spec.claims
        if claim.criticality is Criticality.REQUIRED
        and claim.claim_id.startswith(OUTPUT_CLAIM_PREFIX)
    }
    return sum(
        1
        for result in local.claim_results
        if result.claim_id in required_outputs
        and result.status is VerificationState.FAIL
    )


def _evidence_since(
    records: Sequence[ExperienceRecord], moment: datetime
) -> dict[str, int]:
    """How many experience records for each configuration hash are younger than ``moment``.

    Strictly younger. A record sealed in the same instant as the failure was not learned *since*
    it, and ``>=`` would let the failing attempt's own experience record count as the new
    evidence that justifies re-trying the configuration it failed on — a loop that funds itself.
    """

    counts: dict[str, int] = {}
    for record in records:
        if record.created_at > moment:
            counts[record.configuration_hash] = (
                counts.get(record.configuration_hash, 0) + 1
            )
    return counts


def _verifier_ref(
    policy: AcceptancePolicy, results: Sequence[VerificationResult]
) -> VerifierRef:
    """One reference standing for the verifier set that produced ``results``.

    §7.9 records *the* verifier implementation, and a node graded by three deterministic checks
    has three. Rather than pick one — which would attribute the whole verdict to whichever
    verifier sorted first — the reference names the acceptance policy as the contract and digests
    the sorted ``(verifier_id, verifier_version)`` pairs as the implementation. That digest is
    exactly the registry §4 requirement the field exists for: it moves when the code that made
    the claim changes, and it does not move when anything else does.
    """

    pairs = sorted({(result.verifier_id, result.verifier_version) for result in results})
    digest = hashlib.sha256(
        "\x1f".join(f"{verifier}@{version}" for verifier, version in pairs).encode("utf-8")
    ).hexdigest()
    return VerifierRef(
        verifier_contract_id=policy.policy_id, implementation_digest=digest
    )


def _resolve_evidence(
    results: Sequence[VerificationResult],
) -> dict[str, EvidenceRef]:
    """v0.1 evidence ids resolved into sealed §4 references, where the id carries its own digest.

    The repository's verifiers content-address their evidence as ``<name>-sha256:<hex>`` — the
    git-diff and command verifiers both do — and a ref in that shape is already a complete
    reference: the digest is in it, and every v0.1 verifier is a deterministic check, which is
    what makes the class ``DIGITAL`` rather than a guess (``verification._deterministic_refs``
    states the same thing for the same reason).

    A ref in any other shape resolves to nothing at all. That is not a gap to be filled with
    ``sha256(evidence_id)``: the content digest is a claim about the *evidence*, and hashing its
    name would produce a valid-looking digest of the wrong thing, which no later audit could tell
    from a real one. An unresolved ref leaves its claim uncovered, which is the fail-closed
    behaviour :class:`~accretion.feedback.verification.ClaimCoverageMapper` documents.
    """

    resolved: dict[str, EvidenceRef] = {}
    for result in results:
        for evidence_id in result.evidence_refs:
            _, separator, digest = evidence_id.rpartition("sha256:")
            if not separator or len(digest) != 64:
                continue
            if any(character not in "0123456789abcdef" for character in digest):
                continue
            resolved[evidence_id] = EvidenceRef(
                evidence_id=evidence_id,
                evidence_class=EvidenceClass.DIGITAL,
                content_digest=digest,
            )
    return resolved
