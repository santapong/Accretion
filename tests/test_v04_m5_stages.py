"""The four routing-stage collaborators, and the three properties later lanes rely on.

M5's job in this file is not to prove that the stages *do* anything — M6, M7 and M8 supply
the implementations that will. It is to prove the three things those milestones will build
on top of and cannot check for themselves:

1. **A post-route hook cannot unmake a decision.** The receipt is committed before any hook
   runs, and a hook that raises is logged and swallowed. M6's shadow recorder is the first
   real hook; if it could fail a route, a shadow evaluation would be able to take down the
   very production path it exists to observe without touching.
2. **The deterministic behaviour policy reports a propensity of 1.0.** That number is a
   measurement — the probability the acting policy assigned to the action it took — and
   M7's off-policy estimators divide by it. A default that produced ``None`` or ``0.0``
   would make every pre-exploration decision either uncountable or infinitely weighted.
3. **An excluded configuration is not a candidate.** §9.7 forbids repeating an equivalent
   failed configuration without new evidence, and the enforcement point matters: excluded at
   construction the configuration cannot be ranked, cannot be the fallback and cannot be
   reached by an operator override, which is what "must not repeat" has to mean.

There is no ``conftest.py``. The real freeze/snapshot/catalog stack is imported from
``tests/test_v04_m2_service.py`` and not edited, and every service below is rebuilt from that
fixture's own store, snapshot builder, catalog factory and runtimes, so a stage under test is
attached to the same production objects M2 ships.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_v04_m2_service import _routable_execution

from accretion.contracts import PrincipalRef, PrincipalStatus, Project
from accretion.contracts.routing import (
    ConstructionStage,
    DecisionType,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RoutingDecisionReceipt,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.candidates import CandidateBuilder, CandidateBuildResult
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.compatibility import CompatibilityEngine
from accretion.routing.gates import PolicyGate
from accretion.routing.protocols import RoutingMode
from accretion.routing.selector import DeterministicSelector
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.snapshot import RoutingSnapshot
from accretion.routing.stages import (
    ADAPTER_UNAVAILABLE,
    EVIDENCE_UNAVAILABLE,
    VOCABULARY_MISMATCH,
    WORKSPACE_MODEL_UNAVAILABLE,
    DeterministicBehavior,
    ScoredSlate,
    StatusActiveVersionResolver,
    node_signature,
    worst_degradation,
)

EXCLUSION_REASON = "ATTEMPTED_WITHOUT_NEW_EVIDENCE"


class RecordingPostRouteHook:
    """A hook that reads the store back and can be told to fail afterwards.

    Not a mock: it takes the same arguments the protocol declares and does the one thing a
    real hook does — look at the committed decision. ``seen_in_store`` is what makes the
    ordering claim checkable, because it is read from the service's own store at the moment
    the hook ran rather than asserted about afterwards.
    """

    def __init__(self, store: MemoryStore, *, raises: Exception | None = None) -> None:
        self.store = store
        self.raises = raises
        self.calls = 0
        self.seen_in_store: list[str | None] = []
        self.slates: list[ScoredSlate] = []

    async def after_receipt(
        self,
        *,
        run: object,
        frozen: object,
        snapshot: object,
        context: object,
        receipt: RoutingDecisionReceipt,
        slate: ScoredSlate,
    ) -> None:
        self.calls += 1
        stored = await self.store.get_routing_receipt(receipt.contract_id)
        self.seen_in_store.append(None if stored is None else stored.contract_id)
        self.slates.append(slate)
        if self.raises is not None:
            raise self.raises


def _service(execution, **overrides) -> DefaultNodeRoutingService:
    """Rebuild the fixture's routing service with different stage collaborators.

    Reconstructed from the fixture's own components rather than mutated in place, so a test
    that swaps one stage cannot leave the swapped object behind for the next one.
    """

    return DefaultNodeRoutingService(
        store=execution.service.store,
        snapshots=execution.service.snapshots,
        catalog_factory=execution.service.catalog_factory,
        runtimes=execution.service.runtimes,
        **overrides,
    )


async def _build_candidates(execution, *, excluded=()) -> CandidateBuildResult:
    """Run stages 2--8 against the fixture's real catalog, without persisting anything.

    A probe rather than a route: it answers "which configurations exist" so that a later
    assertion can exclude exactly those, and it writes nothing, so the route that follows it
    is still the first one for its request id.
    """

    catalog = await execution.service.catalog_factory(
        execution.frozen, execution.snapshot, execution.run, execution.task
    )
    snapshot: RoutingSnapshot = replace(
        execution.snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest
    )
    who = execution.frozen.node_contract.created_by
    builder = CandidateBuilder(
        gate=PolicyGate(CapabilityPolicyEngine(set()), snapshot.policy, created_by=who),
        evaluator=CompatibilityEngine(created_by=who),
        catalog=catalog,
        created_by=who,
    )
    return builder.build(
        routing_request_id="rrq_m5_exclusion_probe",
        node_contract=execution.frozen.node_contract,
        task=execution.task,
        principal=who,
        entitled_workspace_id=execution.frozen.node_contract.workspace_id,
        snapshot=snapshot,
        workspace_id=execution.frozen.node_contract.workspace_id,
        project_id=execution.run.project_id,
        clock=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        excluded_configuration_hashes=excluded,
    )


async def test_a_post_route_hook_that_raises_leaves_the_receipt_committed_and_returned(
    tmp_path: Path,
) -> None:
    """A hook runs after the commit, sees the stored receipt, and cannot fail the route.

    Both halves are asserted from the store rather than from the returned object: the hook
    reads the receipt back through the service's own store while it is running, so "already
    committed" is a fact about the database and not about the order of two Python lines.
    """

    execution = await _routable_execution(tmp_path)
    exploding = RecordingPostRouteHook(execution.store, raises=RuntimeError("shadow down"))
    quiet = RecordingPostRouteHook(execution.store)
    service = _service(execution, post_route=(exploding, quiet))

    receipt = await service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    assert exploding.calls == 1
    assert exploding.seen_in_store == [receipt.contract_id]
    # The second hook still ran: one failing observer must not silence the rest.
    assert quiet.calls == 1
    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.contract_id == receipt.contract_id
    assert stored.decision_type is DecisionType.FALLBACK


async def test_a_replayed_route_returns_the_stored_receipt_and_runs_no_hook(
    tmp_path: Path,
) -> None:
    """§8.2 replay is a lookup, so nothing downstream of the decision happens twice.

    A hook that fired on replay would record one shadow decision per retry of an idempotent
    request, which is how a duplicated observation becomes duplicated evidence.
    """

    execution = await _routable_execution(tmp_path)
    hook = RecordingPostRouteHook(execution.store)
    service = _service(execution, post_route=(hook,))

    first = await service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    second = await service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    assert second == first
    assert hook.calls == 1
    receipts = await execution.store.list_routing_receipts(
        workspace_id=first.workspace_id, project_id=first.project_id
    )
    assert [item.contract_id for item in receipts] == [first.contract_id]


async def test_the_deterministic_behaviour_policy_reports_a_propensity_of_one(
    tmp_path: Path,
) -> None:
    """The default policy takes the baseline choice and measures its own propensity at 1.0.

    Asserted twice over: directly on the collaborator, and on a receipt read back from the
    store, because ``selection_propensity`` is nullable in the contract and a service that
    stopped passing it would still produce a valid receipt.
    """

    execution = await _routable_execution(tmp_path)
    built = await _build_candidates(execution)
    objective = await execution.store.get_objective_contract(
        execution.frozen.objective_ref.objective_contract_id
    )
    assert objective is not None
    baseline = DeterministicSelector().select(
        built.candidates,
        built.rejected,
        verified_success_floor=objective.verified_success_floor,
        utility_weights=objective.utility_weights,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        created_by=execution.frozen.node_contract.created_by,
        workspace_id=execution.frozen.node_contract.workspace_id,
        project_id=execution.run.project_id,
    )

    decision = await DeterministicBehavior().select(
        context=None,  # type: ignore[arg-type]
        slate=ScoredSlate(
            candidates=built.candidates,
            calibration_version="cold-start-prior/1",
            evidence_ids=(),
            labels={},
        ),
        baseline=baseline,
        node=execution.frozen.node_contract,
        objective=objective,
        snapshot=execution.snapshot,
    )

    assert decision.propensity == 1.0
    assert decision.selection is baseline
    assert decision.labels == {}

    receipt = await _service(execution).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.selection_propensity == 1.0


async def test_an_excluded_configuration_is_refused_at_construct_tuple(
    tmp_path: Path,
) -> None:
    """§9.7's "must not repeat without new evidence", enforced before scoring.

    The probe learns which configurations the catalog can build; the route then excludes
    exactly those. What is asserted is that they are gone from the *candidate* set — not
    merely unranked — and that the refusal names the stage and the reason a recovery
    decision can be read back from.
    """

    execution = await _routable_execution(tmp_path)
    probe = await _build_candidates(execution)
    hashes = [item.configuration.configuration_hash for item in probe.candidates]
    assert hashes, "the fixture must build at least one candidate for the exclusion to bite"

    closed = await _build_candidates(execution, excluded=hashes)
    assert closed.candidates == ()
    refusals = [item for item in closed.rejected if item.reason_code == EXCLUSION_REASON]
    assert len(refusals) == len(hashes)
    assert {item.stage for item in refusals} == {ConstructionStage.CONSTRUCT_TUPLE}

    receipt = await _service(execution).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
        excluded_configuration_hashes=hashes,
    )
    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.decision_type is DecisionType.HUMAN_REVIEW_REQUIRED
    assert stored.selected_configuration_id is None
    assert EXCLUSION_REASON in {
        item.reason_code for item in stored.rejected_candidate_reasons
    }
    assert stored.candidate_summary_refs == []


async def test_the_status_resolver_names_the_deterministic_router_when_nothing_is_active() -> (
    None
):
    """An unpromoted workspace routes under ``deterministic-router/1`` and no adapter.

    This is the property that makes M5 a no-op for every existing workspace: the labels feed
    ``routing_request_id``, so a resolver that invented a different string here would change
    every request id and orphan every stored receipt.
    """

    store = MemoryStore()
    versions = await StatusActiveVersionResolver(store).resolve(
        workspace_id=new_id("workspace_entity"), project_id=new_id("project")
    )

    assert versions.router_version_id is None
    assert versions.adapter_version_id is None
    assert versions.router_label == WORKSPACE_ROUTER_VERSION
    assert versions.adapter_label is None


async def test_the_status_resolver_prefers_the_latest_active_version_in_each_scope() -> None:
    """Latest is ``(created_at, contract_id)`` and RETIRED is never live.

    Deterministic ordering rather than store order, because ``MemoryStore`` and
    ``PostgresStore`` must resolve the same version for the same rows: a resolver that took
    whichever row a backend returned first would pin one receipt to one model in memory and
    to another in PostgreSQL.
    """

    store = MemoryStore()
    workspace_id = new_id("workspace_entity")
    project_id = new_id("project")
    await store.create_project(
        Project(
            project_id=project_id,
            name="M5 resolver project",
            repository_path=Path("."),
        )
    )
    principal = PrincipalRef(
        principal_id=new_id("principal"),
        display_name="M5 resolver",
        status=PrincipalStatus.ACTIVE,
    )

    def version(
        *, scope: RouterScope, status: RouterStatus, at: datetime, project: str | None
    ) -> RouterModelVersion:
        return RouterModelVersion.model_validate(
            {
                "contract_id": new_id("router_model_version"),
                "created_at": at,
                "created_by": principal.model_dump(mode="python"),
                "workspace_id": workspace_id,
                "project_id": project,
                "scope": scope.value,
                "algorithm_id": "gbdt-bagged-v1",
                "feature_schema_version": "1.0.0",
                "training_snapshot_id": "rts_fixture",
                "artifact_digest": "a" * 64,
                "calibration_artifact_digest": "b" * 64,
                "status": status.value,
            }
        )

    older = version(
        scope=RouterScope.TEAM_WORKSPACE,
        status=RouterStatus.RETIRED,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        project=None,
    )
    newest = version(
        scope=RouterScope.TEAM_WORKSPACE,
        status=RouterStatus.ACTIVE,
        at=datetime(2026, 3, 1, tzinfo=UTC),
        project=None,
    )
    adapter = version(
        scope=RouterScope.PROJECT_ADAPTER,
        status=RouterStatus.ACTIVE,
        at=datetime(2026, 2, 1, tzinfo=UTC),
        project=project_id,
    )
    for record in (older, newest, adapter):
        await store.put_router_model_version(record)

    versions = await StatusActiveVersionResolver(store).resolve(
        workspace_id=workspace_id, project_id=project_id
    )

    assert versions.router_version_id == newest.contract_id
    assert versions.router_label == newest.contract_id
    assert versions.adapter_version_id == adapter.contract_id
    assert versions.adapter_label == adapter.contract_id


def test_the_availability_ladder_reports_the_most_consequential_loss() -> None:
    """§15.1's entries are alternatives, so a receipt names the one that decided.

    The precedence is asserted pairwise rather than as a list, because the failure this
    catches is a *reordering* — a ladder that reported ``ADAPTER_UNAVAILABLE`` while the
    workspace model was also missing would say a learned decision had been made.
    """

    assert worst_degradation() is None
    assert worst_degradation(None, None) is None
    assert worst_degradation(ADAPTER_UNAVAILABLE) == ADAPTER_UNAVAILABLE
    assert (
        worst_degradation(ADAPTER_UNAVAILABLE, WORKSPACE_MODEL_UNAVAILABLE)
        == WORKSPACE_MODEL_UNAVAILABLE
    )
    assert (
        worst_degradation(EVIDENCE_UNAVAILABLE, VOCABULARY_MISMATCH) == VOCABULARY_MISMATCH
    )
    assert (
        worst_degradation(ADAPTER_UNAVAILABLE, EVIDENCE_UNAVAILABLE) == EVIDENCE_UNAVAILABLE
    )
    with pytest.raises(ValueError, match="availability ladder"):
        worst_degradation("MOSTLY_FINE")


async def test_a_nodes_retrieval_signature_ignores_the_order_of_its_requirements(
    tmp_path: Path,
) -> None:
    """§7.10's key is a set of requirements, not the list the planner happened to emit.

    Two nodes that require the same capabilities at the same scopes are one retrieval
    subject; a digest that followed emission order would make each one's evidence invisible
    to the other, and the loss would look like an empty history rather than like a bug.
    """

    execution = await _routable_execution(tmp_path)
    node = execution.frozen.node_contract
    digest = execution.frozen.objective_ref.objective_contract_hash
    reversed_requirements = node.model_copy(
        update={"required_capabilities": list(reversed(node.required_capabilities))}
    )

    assert node_signature(node, objective_digest=digest) == node_signature(
        reversed_requirements, objective_digest=digest
    )
    assert node_signature(node, objective_digest=digest).verification_spec_hash == (
        node.verification_spec_ref.content_hash
    )
    assert node_signature(node, objective_digest="c" * 64) != node_signature(
        node, objective_digest=digest
    )
