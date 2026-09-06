"""Promotion, rollback, and the drill that has to pass before either writes a row.

Two acceptance criteria live here.

**AC4-M8-038** — a report whose ``rollback_target`` cannot be loaded or scored is not
promotable, and refusing it writes nothing. The negative half is easy to make vacuously
true, so every refusal test below asserts the *state* the store is in afterwards: the ledger
has no new head, the version table has no new row, and the drill's own error code came out.
Mutating the service to skip the drill fails the first assertion; moving the drill to after
the write fails the second.

**AC4-M8-039, first half** — a rollback is history-preserving. Receipts written before a
rollback keep their ``(contract_id, content_hash)`` and re-seal to the same digest
afterwards, and exactly one ledger head survives. (M8.2 adds the other half: that the next
routing request pins the restored version.)

**Why the corpus is trained once.** The drill assembles a real predictor from real bytes —
that is the whole point of it — so a fixture that could not be loaded would make every test
here pass for the wrong reason. The trained corpus and its artefact store are imported from
``test_v04_m4_train``, which caches one training run per process, and the two extra versions
these tests need are re-headers over the *same* artefact digests rather than second fits.

There is no ``conftest.py``. Every builder is module-local, and the HTTP tests install and
tear down ``app.state`` themselves.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from test_v04_m2_service import _routable_execution
from test_v04_m4_train import frozen_clock, trained_once
from test_v04_m5_coldstart import install_router, learned_once

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.contracts import (
    EventType,
    Principal,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TaskType,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.canonical import CanonicalContract, content_hash
from accretion.contracts.routing import (
    RouterActivation,
    RouterActivationKind,
    RouterModelVersion,
    RouterPromotionReport,
    RouterScope,
    RouterStatus,
    RoutingDecisionReceipt,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.activation import (
    WORKSPACE_FAMILY_KEY,
    ActivationLedger,
    LedgerActiveVersionResolver,
)
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.coldstart import ColdStartScorer
from accretion.routing.features import EvidenceSummary, Vocabulary, featurize
from accretion.routing.promotion import (
    DRILL_DIGEST_LABEL,
    ActivationConflictError,
    PromotionNotApprovedError,
    PromotionService,
    RollbackDrill,
    RollbackDrillError,
    _probe,
    build_promotion_service,
)
from accretion.routing.protocols import RoutingMode
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.train import LearnedPredictorLoader

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
PROMOTE_PATH = "/api/v1/router-promotions/{report_id}/promote"
ROLLBACK_PATH = "/api/v1/router-models/{version_id}/rollback"

APPROVER = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M8 approver",
    status=PrincipalStatus.ACTIVE,
)
OPERATOR = "usr_m8_promotion_service"
MISSING_DIGEST = "0" * 64
UNACTIVATED_INSTANT = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
"""A day after ``frozen_clock``: the stamp on the one row here no activation ever named."""


def snake_case(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted to this run's ids and re-sealed."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def reheader(
    source: RouterModelVersion,
    workspace_id: str,
    *,
    status: RouterStatus,
    artifact_digest: str | None = None,
    created_at: datetime | None = None,
) -> RouterModelVersion:
    """A second version row over the *same* trained bytes, under a new id and status.

    Cheap on purpose: fitting a second predictor would add seconds to every test here and
    prove nothing extra, because what the drill exercises is whether the bytes a version
    *pins* still load. ``artifact_digest`` is overridable so that a target which cannot be
    drilled can be built the same way as one that can.

    ``created_at`` is overridable for the one test that needs a row *younger* than
    everything the promotion service wrote: the whole suite shares ``frozen_clock``, so
    without it every row here carries one instant and any statement about which row is the
    most recent would be decided by the id tie-break rather than by the timeline.
    """

    document = source.model_dump(mode="json")
    document.pop("content_hash", None)
    document["contract_id"] = new_id("router_model_version")
    document["workspace_id"] = workspace_id
    document["status"] = status.value
    document["parent_version_id"] = source.contract_id
    if artifact_digest is not None:
        document["artifact_digest"] = artifact_digest
    if created_at is not None:
        document["created_at"] = created_at.isoformat()
    return RouterModelVersion.model_validate(document)


def promotion_report(
    workspace_id: str,
    *,
    candidate: RouterModelVersion,
    rollback_target: RouterModelVersion,
    decision: str = "PROMOTE",
) -> RouterPromotionReport:
    """A golden report re-pointed at this test's two versions.

    ``approved_by`` is filled because the contract refuses a ``PROMOTE`` report without one
    (OQ-411): §10.3 makes promotion a human act, and the evaluation that authorised it has to
    name the human. The golden fixture decides ``REJECT`` and so does not carry one.
    """

    return build(
        RouterPromotionReport,
        workspace_id=workspace_id,
        candidate_version=candidate.contract_id,
        baseline_version=rollback_target.contract_id,
        rollback_target=rollback_target.contract_id,
        decision=decision,
        approved_by=APPROVER.model_dump(mode="json"),
    )



def routing_service(execution: Any, learned: Any) -> DefaultNodeRoutingService:
    """The M2 routing stack with the M8.1 resolver and a real cold-start scorer attached.

    ``active_versions`` is stated here rather than defaulted because it is the subject: the
    default is still ``StatusActiveVersionResolver``, and a test that took it would prove the
    opposite of what AC4-M8-039's second half claims.
    """

    return DefaultNodeRoutingService(
        store=execution.service.store,
        snapshots=execution.service.snapshots,
        catalog_factory=execution.service.catalog_factory,
        runtimes=execution.service.runtimes,
        active_versions=LedgerActiveVersionResolver(execution.store),
        scorer=ColdStartScorer(
            LearnedPredictorLoader(execution.store, learned.artifacts),
            learned.artifacts,
            lambda: datetime(2026, 5, 1, tzinfo=UTC),
        ),
        default_mode=RoutingMode.AUTO,
    )


class Bench:
    """One promotable workspace: a candidate, a drillable target, and a wired service."""

    def __init__(
        self,
        store: MemoryStore,
        artifacts: ArtifactStore,
        workspace_id: str,
        project_id: str,
        candidate: RouterModelVersion,
        target: RouterModelVersion,
        service: PromotionService,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.candidate = candidate
        self.target = target
        self.service = service
        self.ledger = ActivationLedger(store)

    def report(self, **overrides: Any) -> RouterPromotionReport:
        return promotion_report(
            self.workspace_id,
            candidate=self.candidate,
            rollback_target=self.target,
            **overrides,
        )

    async def head(self) -> RouterActivation | None:
        return await self.ledger.head(
            workspace_id=self.workspace_id,
            scope=RouterScope.TEAM_WORKSPACE,
            family_key=WORKSPACE_FAMILY_KEY,
        )

    async def entries(self) -> list[RouterActivation]:
        return await self.store.list_router_activations(workspace_id=self.workspace_id)

    async def versions(self) -> list[RouterModelVersion]:
        return await self.store.list_router_model_versions(
            workspace_id=self.workspace_id
        )


async def setup_bench(
    tmp_path_factory: pytest.TempPathFactory,
    *,
    target_artifact_digest: str | None = None,
    private_artifacts: bool = False,
) -> Bench:
    """A fresh store and workspace, over the one training run this process performs.

    **The store is fresh and the artefacts are shared.** Every test here needs a version
    whose bytes really load, and fitting one costs seconds — so the trained artefacts are
    built once by ``test_v04_m4_train.trained_once`` and re-used. The *store*, though, is a
    new :class:`~accretion.persistence.store.MemoryStore` per bench: these tests count rows
    and ledger entries, and sharing the trained corpus's store would make every count depend
    on how many other tests had run first.

    ``private_artifacts`` copies the artefact tree so that a test may delete bytes out of it
    without breaking every later test in the process.
    """

    _corpus, shared, trained, _calls = await trained_once(tmp_path_factory)
    artifacts = shared
    if private_artifacts:
        root = tmp_path_factory.mktemp("m8-artifacts")
        shutil.copytree(shared.root, root, dirs_exist_ok=True)
        artifacts = ArtifactStore(root)

    store = MemoryStore()
    workspace_id = f"wks_{uuid4().hex[:12]}"
    project_id = new_id("project")
    await store.create_project(
        Project(
            project_id=project_id,
            name="v0.4 M8 promotion project",
            repository_path=Path(tmp_path_factory.mktemp("m8-project")),
        )
    )
    # ``ACTIVE`` and not ``RETIRED``: the rollback target a report names is the version that
    # *was serving*, and promotion never rewrites that row — it appends a ``RETIRED``
    # tombstone beside it. ``LOADABLE_STATUSES`` (M4) excludes ``RETIRED`` precisely so a
    # tombstone can never be routed on, and the drill assembles the target exactly as
    # routing would, so a target row in a status routing refuses would never drill.
    target = reheader(
        trained.version,
        workspace_id,
        status=RouterStatus.ACTIVE,
        artifact_digest=target_artifact_digest,
    )
    candidate = reheader(trained.version, workspace_id, status=RouterStatus.CANDIDATE)
    for record in (target, candidate):
        await store.put_router_model_version(record)
    service = build_promotion_service(
        store, artifacts, operator_identity=OPERATOR, clock=frozen_clock
    )
    return Bench(
        store, artifacts, workspace_id, project_id, candidate, target, service
    )


# ------------------------------------------------------------------- the drill


async def test_the_drill_probe_is_the_committed_golden_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The four documents in ``promotion.py`` are the ones on disk, field for field.

    Asserted through ``featurize`` rather than by comparing JSON, because the feature row is
    what the drill actually measures: an embedded copy that drifted in a field ``featurize``
    reads would change every drill digest silently, and one that drifted in a field it does
    not read would not matter. Comparing rows tests exactly the part that does.
    """

    context, candidate, node, objective = _probe()
    embedded = featurize(
        context, candidate, node, objective, EvidenceSummary(), Vocabulary()
    )
    on_disk = featurize(
        build(type(context), contract_id=context.contract_id),
        build(type(candidate), contract_id=candidate.contract_id),
        build(type(node), contract_id=node.contract_id),
        build(type(objective), contract_id=objective.contract_id),
        EvidenceSummary(),
        Vocabulary(),
    )
    assert embedded.values == on_disk.values


async def test_the_drill_returns_a_stable_digest_for_a_loadable_target(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A rehearsal that ran twice on the same bytes says the same thing twice.

    An unstable digest would make the label a ledger entry carries meaningless as evidence
    that *this* promotion drilled *this* target.
    """

    bench = await setup_bench(tmp_path_factory)
    drill = RollbackDrill(
        LearnedPredictorLoader(bench.store, bench.artifacts), bench.artifacts
    )

    first = drill.run(bench.target)
    second = drill.run(bench.target)

    assert first == second
    assert len(first) == 64
    assert int(first, 16) >= 0


async def test_the_drill_refuses_a_target_whose_artefact_is_missing(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The failure the drill exists to find, and the code an operator branches on."""

    bench = await setup_bench(tmp_path_factory, target_artifact_digest=MISSING_DIGEST)
    drill = RollbackDrill(
        LearnedPredictorLoader(bench.store, bench.artifacts), bench.artifacts
    )

    with pytest.raises(RollbackDrillError) as refusal:
        drill.run(bench.target)

    assert refusal.value.code == "ROUTER_ROLLBACK_DRILL_FAILED"
    assert bench.target.contract_id in refusal.value.message
    assert "ArtifactNotFoundError" in refusal.value.message


# ----------------------------------------------------------------- promotion


@pytest.mark.acceptance("AC4-M8-038")
async def test_a_report_whose_rollback_target_cannot_be_drilled_writes_no_row(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC4-M8-038. Not promotable, and the refusal leaves the store exactly as it was.

    The state assertions are the test. Raising the right exception is easy to arrange after
    the fact; what §10.3 needs is that a promotion whose reversal could not be rehearsed
    never became a promotion at all. Skipping the drill fails the ``pytest.raises``; drilling
    after the write fails the ledger and version-count assertions below.
    """

    bench = await setup_bench(tmp_path_factory, target_artifact_digest=MISSING_DIGEST)
    report = await bench.store.put_router_promotion_report(bench.report())
    before = await bench.versions()

    with pytest.raises(RollbackDrillError) as refusal:
        await bench.service.promote(report.contract_id, APPROVER)

    assert refusal.value.code == "ROUTER_ROLLBACK_DRILL_FAILED"
    assert await bench.head() is None
    assert (
        await bench.entries() == []
    )
    assert await bench.versions() == before


@pytest.mark.acceptance("AC4-M8-038")
async def test_a_report_whose_rollback_target_is_not_stored_writes_no_row(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The other way a target fails to load: it is not there at all.

    Reported as a drill failure rather than as a 404, because the resource the caller named
    is the *report*, and the report is present; what is missing is something the report
    points at. A ``KeyError`` here would have told the operator their report id was wrong.
    """

    bench = await setup_bench(tmp_path_factory)
    absent = reheader(bench.candidate, bench.workspace_id, status=RouterStatus.ACTIVE)
    report = await bench.store.put_router_promotion_report(
        promotion_report(
            bench.workspace_id, candidate=bench.candidate, rollback_target=absent
        )
    )
    before = await bench.versions()

    with pytest.raises(RollbackDrillError) as refusal:
        await bench.service.promote(report.contract_id, APPROVER)

    assert absent.contract_id in refusal.value.message
    assert await bench.head() is None
    assert await bench.versions() == before


async def test_a_report_that_did_not_decide_promote_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """``REJECT`` and ``REQUIRE_REVIEW`` are both refusals, not slower approvals."""

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(
        bench.report(decision="REQUIRE_REVIEW")
    )

    with pytest.raises(PromotionNotApprovedError) as refusal:
        await bench.service.promote(report.contract_id, APPROVER)

    assert refusal.value.code == "ROUTER_PROMOTION_NOT_APPROVED"
    assert "REQUIRE_REVIEW" in refusal.value.message
    assert await bench.head() is None


async def test_promotion_writes_the_head_the_active_row_and_the_drill_digest(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """One act: a ledger entry, a new ACTIVE version, and the rehearsal it passed.

    Everything is read back from the store. A service that returned a well-formed activation
    without persisting the version row would satisfy every assertion made on its return
    value and none of these.
    """

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())

    activation = await bench.service.promote(report.contract_id, APPROVER)

    head = await bench.head()
    assert head == activation
    assert head is not None
    assert head.sequence == 1
    assert head.kind is RouterActivationKind.PROMOTE
    assert head.previous_version_id is None
    assert head.promotion_report_id == report.contract_id
    assert head.rollback_target_version_id == bench.target.contract_id
    assert head.approved_by == APPROVER
    assert len(head.labels[DRILL_DIGEST_LABEL]) == 64

    promoted = await bench.store.get_router_model_version(head.router_version_id)
    assert promoted is not None
    assert promoted.status is RouterStatus.ACTIVE
    assert promoted.parent_version_id == bench.candidate.contract_id
    assert promoted.supersedes_contract_id == bench.candidate.contract_id
    assert promoted.artifact_digest == bench.candidate.artifact_digest
    assert promoted.created_by.principal_id == OPERATOR


async def test_a_second_promotion_retires_the_previous_head(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The displaced version gets a tombstone; nothing is updated in place.

    The ledger's ``previous_version_id`` names the version that *was serving* and not the
    ``RETIRED`` row recording that it stopped, because the tombstone was never active.
    """

    bench = await setup_bench(tmp_path_factory)
    first_report = await bench.store.put_router_promotion_report(
        bench.report()
    )
    first = await bench.service.promote(first_report.contract_id, APPROVER)

    challenger = reheader(bench.candidate, bench.workspace_id, status=RouterStatus.CANDIDATE)
    await bench.store.put_router_model_version(challenger)
    second_report = await bench.store.put_router_promotion_report(
        promotion_report(
            bench.workspace_id, candidate=challenger, rollback_target=bench.target
        )
    )
    second = await bench.service.promote(second_report.contract_id, APPROVER)

    head = await bench.head()
    assert head == second
    assert head is not None
    assert head.sequence == 2
    assert head.previous_version_id == first.router_version_id

    statuses = {
        version.contract_id: version.status for version in await bench.versions()
    }
    assert statuses[head.router_version_id] is RouterStatus.ACTIVE
    retired = [
        version_id
        for version_id, status in statuses.items()
        if status is RouterStatus.RETIRED
    ]
    # Exactly one tombstone, and it is *not* the row that was serving: that row keeps its
    # ACTIVE status forever, because this table is append-only and the ledger — not the
    # column — is what says which of the ACTIVE rows is current.
    assert len(retired) == 1
    assert first.router_version_id not in retired
    assert statuses[first.router_version_id] is RouterStatus.ACTIVE
    tombstone = await bench.store.get_router_model_version(retired[0])
    assert tombstone is not None
    assert tombstone.parent_version_id == first.router_version_id


async def test_promoting_the_same_report_twice_returns_the_first_activation(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A report authorises one release, so a replay is a read.

    The ledger is the witness rather than the return value: a service that promoted again
    would return a *different* activation and leave a second entry at sequence 2.
    """

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())

    first = await bench.service.promote(report.contract_id, APPROVER)
    second = await bench.service.promote(report.contract_id, APPROVER)

    assert second.contract_id == first.contract_id
    assert [entry.sequence for entry in await bench.entries()] == [1]


async def test_an_unknown_report_is_a_key_error(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The 404 convention, which is also what makes another tenant's report invisible."""

    bench = await setup_bench(tmp_path_factory)

    with pytest.raises(KeyError):
        await bench.service.promote("rpr_does_not_exist", APPROVER)


# ------------------------------------------------------------------ rollback


@pytest.mark.acceptance("AC4-M8-039")
async def test_a_rollback_preserves_every_receipt_and_leaves_one_head(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC4-M8-039, first half. Withdrawing a router rewrites no history.

    Two claims. The receipts written while the withdrawn version was serving keep their
    ``(contract_id, content_hash)`` and **re-seal to the same digest** — recomputing the hash
    from the stored payload is what proves the row was not quietly amended, because an
    unchanged ``content_hash`` beside a changed body is exactly what a silent rewrite looks
    like. And the ledger has one head afterwards, not two: a rollback appends, and a
    withdrawal that left the promotion also claiming to be current would make "active"
    ambiguous at the moment an incident most needs it to be exact.

    (M8.2 adds the other half of this criterion: that the next routing request pins the
    restored version.)
    """

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    promoted = await bench.service.promote(report.contract_id, APPROVER)

    receipts = [
        await bench.store.put_routing_receipt(
            build(
                RoutingDecisionReceipt,
                workspace_id=bench.workspace_id,
                project_id=bench.project_id,
                routing_request_id=new_id("routing_request"),
                workspace_router_version=promoted.router_version_id,
            )
        )
        for _ in range(3)
    ]
    sealed = {receipt.contract_id: receipt.content_hash for receipt in receipts}

    withdrawal = await bench.service.rollback(
        promoted.router_version_id, "elevated false acceptance in production", APPROVER
    )

    for contract_id, digest in sealed.items():
        stored = await bench.store.get_routing_receipt(contract_id)
        assert stored is not None
        assert stored.content_hash == digest
        assert content_hash(stored) == digest

    assert [entry.sequence for entry in await bench.entries()] == [1, 2]
    head = await bench.head()
    assert head == withdrawal
    assert head is not None
    assert head.kind is RouterActivationKind.ROLLBACK
    assert head.previous_version_id == promoted.router_version_id
    assert head.rollback_target_version_id == bench.target.contract_id
    assert head.cause == "elevated false acceptance in production"


@pytest.mark.acceptance("AC4-M8-039")
async def test_routing_pins_the_head_before_and_after_a_rollback_and_rewrites_no_receipt(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """AC4-M8-039, second half. A rollback changes new decisions and only new decisions.

    The routing service is wired with
    :class:`~accretion.routing.activation.LedgerActiveVersionResolver` — the resolver
    ``bootstrap.py`` now installs — and the whole claim is what that swap buys. A request
    routed while the promotion is the head pins the promoted version in
    ``workspace_router_version``; the same node routed after the withdrawal pins the restored
    one; and the first receipt is byte for byte what it was, re-sealed from its stored body
    rather than merely reporting an unchanged digest.

    What makes the resolver's identity observable is the ``ACTIVE`` row below that the
    ledger never activated. Migration 0019 retired the partial unique indexes that used to
    forbid a second ``ACTIVE`` row per family precisely because promotion and rollback both
    *append* ``ACTIVE`` rows and rewrite none, so the status column cannot say which of
    several is serving — and a row can reach ``ACTIVE`` without an activation at all: a
    promotion made before the ledger existed, a restored backup, an operator repairing a
    table by hand. ``stray`` is that row, stamped a day after everything the promotion
    service wrote so that "the latest ACTIVE row" names it beyond any tie-break, and it is
    asserted to be that row at both checkpoints. A resolver reading
    ``router_model_versions.status`` therefore pins a version nobody approved and no drill
    ever rehearsed, at both checkpoints, every run; the ledger resolver pins the head.

    Without the stray row this test discriminates nothing: the whole suite shares
    ``frozen_clock``, so every version row carries one instant, and a status resolver would
    fall back to comparing randomly minted ``contract_id``s and land on the right answer
    about half the time. Under a monotone clock it would land on the right answer *always*,
    since the rows an activation appends are always the youngest — which is why the
    discriminator has to be a row the ledger does not know about rather than a later clock.

    ``mode=AUTO`` and a real cold-start scorer, because the label under test is an
    *attribution*: a receipt naming a version that never scored anything would credit the
    deterministic fallback's outcomes to a learned router in every later evaluation.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    incumbent = await install_router(
        execution.store, workspace_id, learned, status=RouterStatus.ACTIVE
    )
    challenger = await install_router(
        execution.store, workspace_id, learned, status=RouterStatus.CANDIDATE
    )
    service = build_promotion_service(
        execution.store, learned.artifacts, operator_identity=OPERATOR, clock=frozen_clock
    )
    report = await execution.store.put_router_promotion_report(
        promotion_report(
            workspace_id, candidate=challenger, rollback_target=incumbent
        )
    )
    promoted = await service.promote(report.contract_id, APPROVER)

    stray = await execution.store.put_router_model_version(
        reheader(
            incumbent,
            workspace_id,
            status=RouterStatus.ACTIVE,
            created_at=UNACTIVATED_INSTANT,
        )
    )

    async def latest_active_row() -> str:
        """What a resolver reading the status column would answer: ``(created_at, id)``."""

        versions = await execution.store.list_router_model_versions(
            workspace_id=workspace_id
        )
        active = [
            version
            for version in versions
            if version.scope is RouterScope.TEAM_WORKSPACE
            and version.status is RouterStatus.ACTIVE
        ]
        return max(
            active, key=lambda version: (version.created_at, version.contract_id)
        ).contract_id

    assert await latest_active_row() == stray.contract_id

    routing = routing_service(execution, learned)
    first = await routing.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )
    assert first.workspace_router_version == promoted.router_version_id
    assert first.workspace_router_version != stray.contract_id

    withdrawal = await service.rollback(
        promoted.router_version_id, "regression found in production", APPROVER
    )
    assert await latest_active_row() == stray.contract_id
    second = await routing.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    assert second.contract_id != first.contract_id
    assert second.workspace_router_version != stray.contract_id
    assert second.workspace_router_version == withdrawal.router_version_id
    assert second.workspace_router_version != first.workspace_router_version

    replayed = await execution.store.get_routing_receipt(first.contract_id)
    assert replayed is not None
    assert replayed.content_hash == first.content_hash
    assert content_hash(replayed) == first.content_hash
    assert replayed.workspace_router_version == promoted.router_version_id


async def test_a_rollback_marks_the_withdrawn_version_and_restores_the_target(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Two new rows: ``ROLLED_BACK`` for what was withdrawn, ``ACTIVE`` for what returns.

    The restored row is a *new* record whose ``parent_version_id`` is the target, not the
    target row mutated back to ``ACTIVE``: this table is append-only, and rewriting the
    target's status would erase the evidence that it had ever been retired.
    """

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    promoted = await bench.service.promote(report.contract_id, APPROVER)

    withdrawal = await bench.service.rollback(
        promoted.router_version_id, "regression", APPROVER
    )

    versions = {version.contract_id: version for version in await bench.versions()}
    restored = versions[withdrawal.router_version_id]
    assert restored.status is RouterStatus.ACTIVE
    assert restored.parent_version_id == bench.target.contract_id
    assert restored.artifact_digest == bench.target.artifact_digest
    # The target row itself is untouched: restoring it means appending a new ACTIVE row
    # whose parent it is, not rewriting its status back.
    assert versions[bench.target.contract_id].status is RouterStatus.ACTIVE

    rolled_back = [
        version
        for version in versions.values()
        if version.status is RouterStatus.ROLLED_BACK
    ]
    assert len(rolled_back) == 1
    assert rolled_back[0].parent_version_id == promoted.router_version_id


async def test_rolling_back_a_version_that_is_not_the_head_is_a_conflict(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A rollback is a statement about the activation the operator was looking at.

    If a promotion landed in between, withdrawing the version they named would restore a
    router two promotions old without anyone having asked for that — so it is refused with a
    conflict rather than performed.
    """

    bench = await setup_bench(tmp_path_factory)
    first_report = await bench.store.put_router_promotion_report(
        bench.report()
    )
    first = await bench.service.promote(first_report.contract_id, APPROVER)

    challenger = reheader(bench.candidate, bench.workspace_id, status=RouterStatus.CANDIDATE)
    await bench.store.put_router_model_version(challenger)
    second_report = await bench.store.put_router_promotion_report(
        promotion_report(
            bench.workspace_id, candidate=challenger, rollback_target=bench.target
        )
    )
    await bench.service.promote(second_report.contract_id, APPROVER)

    with pytest.raises(ActivationConflictError) as refusal:
        await bench.service.rollback(first.router_version_id, "too late", APPROVER)

    assert refusal.value.code == "ROUTER_ACTIVATION_CONFLICT"
    assert "is not the head" in refusal.value.message
    head = await bench.head()
    assert head is not None
    assert head.sequence == 2


async def test_a_promotion_landing_during_the_rollback_drill_is_a_conflict(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The head is checked again inside the activation transaction, as ``promote`` does.

    ``rollback`` reads the head, drills the target, and only then takes the per-workspace
    lock. A promotion that lands between the read and the lock must turn the rollback into
    a conflict, not into a withdrawal of the *new* head under the old version's name. The
    race is staged at the real await boundary where the drill target is fetched: dropping
    the in-transaction re-check lets the rollback succeed and this test fails on
    ``pytest.raises``.
    """

    bench = await setup_bench(tmp_path_factory)
    first_report = await bench.store.put_router_promotion_report(bench.report())
    first = await bench.service.promote(first_report.contract_id, APPROVER)

    challenger = reheader(bench.candidate, bench.workspace_id, status=RouterStatus.CANDIDATE)
    await bench.store.put_router_model_version(challenger)
    second_report = await bench.store.put_router_promotion_report(
        promotion_report(
            bench.workspace_id, candidate=challenger, rollback_target=bench.target
        )
    )

    real_get = bench.store.get_router_model_version
    fired = False

    async def racing_get(version_id: str) -> RouterModelVersion | None:
        nonlocal fired
        row = await real_get(version_id)
        if not fired and version_id == first.rollback_target_version_id:
            fired = True
            await bench.service.promote(second_report.contract_id, APPROVER)
        return row

    monkeypatch.setattr(bench.store, "get_router_model_version", racing_get)

    with pytest.raises(ActivationConflictError) as refusal:
        await bench.service.rollback(first.router_version_id, "incident", APPROVER)

    assert fired
    assert refusal.value.code == "ROUTER_ACTIVATION_CONFLICT"
    assert "is not the head" in refusal.value.message
    head = await bench.head()
    assert head is not None
    assert head.sequence == 2
    assert head.promotion_report_id == second_report.contract_id
    assert head.kind is RouterActivationKind.PROMOTE
    assert len(await bench.entries()) == 2
    assert all(
        version.status is not RouterStatus.ROLLED_BACK for version in await bench.versions()
    )


async def test_rolling_back_twice_returns_the_first_withdrawal(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A retry during an incident must not append a second withdrawal.

    The natural key is "this version has already been withdrawn", so the replay is a read
    whatever ``Idempotency-Key`` the client used the second time.
    """

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    promoted = await bench.service.promote(report.contract_id, APPROVER)

    first = await bench.service.rollback(promoted.router_version_id, "incident", APPROVER)
    second = await bench.service.rollback(
        promoted.router_version_id, "incident", APPROVER
    )

    assert second.contract_id == first.contract_id
    assert [entry.sequence for entry in await bench.entries()] == [1, 2]


async def test_a_rollback_onto_an_unloadable_target_is_refused_before_it_writes(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The drill runs at rollback time too, and for the sharper reason.

    The drill that ran at promotion proved the target loaded *then*. An incident an hour
    later is exactly when that stops being evidence, and a rollback onto a target that no
    longer assembles would replace a bad router with a dead one.
    """

    bench = await setup_bench(tmp_path_factory, private_artifacts=True)
    report = await bench.store.put_router_promotion_report(bench.report())
    promoted = await bench.service.promote(report.contract_id, APPROVER)

    # The bytes rot after the promotion: the digest the target pins no longer resolves.
    bench.artifacts.path_for(bench.target.artifact_digest).unlink()
    before = await bench.versions()

    with pytest.raises(RollbackDrillError) as refusal:
        await bench.service.rollback(promoted.router_version_id, "incident", APPROVER)

    assert refusal.value.code == "ROUTER_ROLLBACK_DRILL_FAILED"
    assert await bench.versions() == before
    head = await bench.head()
    assert head is not None
    assert head.sequence == 1
    assert head.kind is RouterActivationKind.PROMOTE


# -------------------------------------------------------------------- events


async def run_in(bench: Bench) -> Run:
    """A real run to announce into: ``AgentEvent`` has no unattached form."""

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=bench.project_id,
            objective="Promote a router version from inside a run.",
            task_type=TaskType.IMPLEMENT,
        )
    )
    await bench.store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=bench.project_id,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    await bench.store.create_run(run)
    return run


async def test_a_promotion_and_a_rollback_each_emit_their_event(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """§12's two router events, with the ids a reader needs to walk the ledger.

    The payload is asserted key for key: §12 says events carry references and no hidden
    state, and a payload that grew a field would otherwise reach the event stream unreviewed.
    """

    bench = await setup_bench(tmp_path_factory)
    run = await run_in(bench)
    report = await bench.store.put_router_promotion_report(bench.report())

    promoted = await bench.service.promote(report.contract_id, APPROVER, run.run_id)
    withdrawal = await bench.service.rollback(
        promoted.router_version_id, "regression", APPROVER, run.run_id
    )

    events = await bench.store.list_events(run.run_id)
    assert [event.normalized_type for event in events] == [
        EventType.ROUTER_VERSION_PROMOTED,
        EventType.ROUTER_VERSION_ROLLED_BACK,
    ]
    promotion_payload = events[0].payload
    assert promotion_payload["activation_id"] == promoted.contract_id
    assert promotion_payload["router_version_id"] == promoted.router_version_id
    assert promotion_payload["promotion_report_id"] == report.contract_id
    assert promotion_payload["sequence"] == 1
    assert set(promotion_payload) == {
        "activation_id",
        "drill_digest",
        "family_key",
        "previous_version_id",
        "promotion_report_id",
        "rollback_target_version_id",
        "router_version_id",
        "scope",
        "sequence",
    }
    rollback_payload = events[1].payload
    assert rollback_payload["activation_id"] == withdrawal.contract_id
    assert rollback_payload["cause"] == "regression"
    assert rollback_payload["previous_version_id"] == promoted.router_version_id
    assert rollback_payload["sequence"] == 2


async def test_a_promotion_without_a_run_emits_nothing_and_still_promotes(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """There is no event without a run, and that must not block the promotion.

    ``AgentEvent`` requires a ``run_id`` and ``append_event`` locks the run row, so a
    synthesised id would invent a run that never executed. A promotion from the admin route
    with no run context is recorded by the ledger entry, which is the stronger record.
    """

    bench = await setup_bench(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())

    activation = await bench.service.promote(
        report.contract_id, APPROVER, "run_does_not_exist"
    )

    assert activation.sequence == 1
    assert await bench.head() == activation
    assert await bench.store.list_events("run_does_not_exist") == []


# ---------------------------------------------------------------------- HTTP


async def setup_http(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Bench, Principal, Principal]:
    """The bench, one workspace owner, and one principal who is not a member."""

    bench = await setup_bench(tmp_path_factory)
    suffix = uuid4().hex[:8]
    owner = Principal(
        principal_id=f"usr_owner_{suffix}", issuer="test", subject=f"owner-{suffix}"
    )
    outsider = Principal(
        principal_id=f"usr_outsider_{suffix}", issuer="test", subject=f"outsider-{suffix}"
    )
    for principal in (owner, outsider):
        await bench.store.upsert_principal(principal)
    await bench.store.upsert_workspace(
        WorkspaceEntity(workspace_id=bench.workspace_id, name="v0.4 M8")
    )
    await bench.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=bench.workspace_id,
            principal_id=owner.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    return bench, owner, outsider


def install_app_state(bench: Bench, who: Principal) -> None:
    app.state.router_admin = bench.service
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(bench.store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )


def clear_app_state() -> None:
    for attribute in ("auth", "router_admin"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


async def call(method: str, url: str, **kwargs: Any) -> Any:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        return await client.request(method, url, **kwargs)


async def test_a_non_admin_cannot_promote(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """403 and not 404: the caller named a workspace, not a resource.

    The membership lookup is the same one production uses, against real ``principals`` and
    ``workspace_memberships`` rows, so this is not a stubbed decision.
    """

    bench, _owner, outsider = await setup_http(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    install_app_state(bench, outsider)
    try:
        response = await call(
            "POST",
            PROMOTE_PATH.format(report_id=report.contract_id),
            params={"workspace_id": bench.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"
        assert await bench.head() is None
    finally:
        clear_app_state()


async def test_a_report_in_another_workspace_is_invisible_rather_than_forbidden(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Tenancy is a 404 here, so administering workspace A discovers nothing about B."""

    bench, owner, _outsider = await setup_http(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    other_workspace = f"wks_{uuid4().hex[:12]}"
    await bench.store.upsert_workspace(
        WorkspaceEntity(workspace_id=other_workspace, name="v0.4 M8 other")
    )
    await bench.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=other_workspace,
            principal_id=owner.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    install_app_state(bench, owner)
    try:
        response = await call(
            "POST",
            PROMOTE_PATH.format(report_id=report.contract_id),
            params={"workspace_id": other_workspace},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 404, response.text
        assert await bench.head() is None
    finally:
        clear_app_state()


async def test_promoting_without_an_idempotency_key_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """SDD §11: a mutating endpoint states its key or is refused."""

    bench, owner, _outsider = await setup_http(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    install_app_state(bench, owner)
    try:
        response = await call(
            "POST",
            PROMOTE_PATH.format(report_id=report.contract_id),
            params={"workspace_id": bench.workspace_id},
        )
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        assert await bench.head() is None
    finally:
        clear_app_state()


async def test_a_replayed_promotion_returns_the_same_activation_id(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The property a client retrying a timed-out request depends on."""

    bench, owner, _outsider = await setup_http(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    install_app_state(bench, owner)
    try:
        key = f"idem-{uuid4().hex[:8]}"
        first = await call(
            "POST",
            PROMOTE_PATH.format(report_id=report.contract_id),
            params={"workspace_id": bench.workspace_id},
            headers={"Idempotency-Key": key},
        )
        second = await call(
            "POST",
            PROMOTE_PATH.format(report_id=report.contract_id),
            params={"workspace_id": bench.workspace_id},
            headers={"Idempotency-Key": key},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert second.json()["contract_id"] == first.json()["contract_id"]
        assert [entry.sequence for entry in await bench.entries()] == [1]
    finally:
        clear_app_state()


async def test_a_refused_promotion_returns_the_drill_code_and_not_a_500(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The refusal reaches the wire as itself.

    ``RollbackDrillError`` is a ``RoutingError``, so ``main.py``'s existing handler renders
    it in the ``{code, message, correlation_id, retryable}`` envelope. An unhandled exception
    here would be a 500, and an operator would have no way to tell "the target rotted" from
    "the service is broken".
    """

    bench, owner, _outsider = await setup_http(
        tmp_path_factory
    )
    rotten = reheader(
        bench.candidate,
        bench.workspace_id,
        status=RouterStatus.ACTIVE,
        artifact_digest=MISSING_DIGEST,
    )
    await bench.store.put_router_model_version(rotten)
    report = await bench.store.put_router_promotion_report(
        promotion_report(
            bench.workspace_id, candidate=bench.candidate, rollback_target=rotten
        )
    )
    install_app_state(bench, owner)
    try:
        response = await call(
            "POST",
            PROMOTE_PATH.format(report_id=report.contract_id),
            params={"workspace_id": bench.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "ROUTER_ROLLBACK_DRILL_FAILED"
        assert body["retryable"] is False
        assert body["correlation_id"]
        assert await bench.head() is None
    finally:
        clear_app_state()


async def test_the_rollback_route_records_the_cause_it_was_given(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """§10.3's reversibility is worth nothing if the ledger cannot say what it was for."""

    bench, owner, _outsider = await setup_http(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    promoted = await bench.service.promote(report.contract_id, APPROVER)
    install_app_state(bench, owner)
    try:
        response = await call(
            "POST",
            ROLLBACK_PATH.format(version_id=promoted.router_version_id),
            params={"workspace_id": bench.workspace_id},
            json={"cause": "false acceptance above the ceiling"},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["kind"] == "ROLLBACK"

        head = await bench.head()
        assert head is not None
        assert head.cause == "false acceptance above the ceiling"
        assert head.approved_by.principal_id == owner.principal_id
    finally:
        clear_app_state()


async def test_the_rollback_route_refuses_a_body_without_a_cause(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A withdrawal nobody explained is the row an incident review most needs to read."""

    bench, owner, _outsider = await setup_http(tmp_path_factory)
    report = await bench.store.put_router_promotion_report(bench.report())
    promoted = await bench.service.promote(report.contract_id, APPROVER)
    install_app_state(bench, owner)
    try:
        response = await call(
            "POST",
            ROLLBACK_PATH.format(version_id=promoted.router_version_id),
            params={"workspace_id": bench.workspace_id},
            json={},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 422, response.text
        head = await bench.head()
        assert head is not None
        assert head.kind is RouterActivationKind.PROMOTE
    finally:
        clear_app_state()


async def test_the_route_refuses_when_no_promotion_service_is_wired(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A missing service is a 409 with a code, not an ``AttributeError`` and a 500."""

    bench, owner, _outsider = await setup_http(tmp_path_factory)
    install_app_state(bench, owner)
    clear_app_state()
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(bench.store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=owner,
    )
    try:
        response = await call(
            "POST",
            PROMOTE_PATH.format(report_id="rpr_anything"),
            params={"workspace_id": bench.workspace_id},
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "ROUTER_ADMIN_UNAVAILABLE"
    finally:
        clear_app_state()


# ------------------------------------------------------ EventType is reachable


def test_the_two_promotion_event_types_exist() -> None:
    """§12 named them at M0; M8.1 is the first milestone that can emit either."""

    assert EventType.ROUTER_VERSION_PROMOTED.value == "ROUTER_VERSION_PROMOTED"
    assert EventType.ROUTER_VERSION_ROLLED_BACK.value == "ROUTER_VERSION_ROLLED_BACK"
