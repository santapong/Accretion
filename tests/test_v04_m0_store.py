"""The append-only v0.4 contract store, through ``MemoryStore`` (SDD §13.1, ADR-058).

Everything here is a claim about *behaviour a later milestone could break*: that a record
written once cannot be rewritten, that a retry is free, that a revision is a second row
rather than an edit, that the two §13.1 partial unique rules hold in memory exactly as they
hold in PostgreSQL, and that no method exists anywhere in the store that could update or
delete one of these seventeen tables.

The contracts under test are built from the **committed golden fixtures**, not assembled
field by field here. A test that built its own ``NodeContract`` would prove that the store
can round-trip whatever this file happens to think a node contract looks like; building
from `tests/fixtures/contracts/v0.4/<model>/minimal.json` proves it can round-trip the
document the freeze actually froze. Only the id and the fields a given test is varying are
overridden, and the digest is always recomputed by the model rather than carried over.

The PostgreSQL twin of this file is ``test_v04_m0_postgres_store.py``; the parity tests at
the bottom are what keep the two honest, because they enumerate the protocol surface rather
than listing method names by hand.
"""

from __future__ import annotations

import inspect
import itertools
import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from accretion.contracts import Project, Provider, TaskType
from accretion.contracts.canonical import CONTRACT_SCHEMA_VERSION, CanonicalContract
from accretion.contracts.routing import (
    CONTRACT_INVENTORY,
    CompatibilityDecision,
    ConfigurationCandidate,
    ExperienceRecord,
    FailureEvent,
    IndependentVerificationResult,
    NodeContract,
    ObjectiveContract,
    RouterActivation,
    RouterModelVersion,
    RouterPromotionReport,
    RouterScope,
    RouterStatus,
    RouterTrainingSnapshot,
    RoutingContext,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutResult,
    VerificationSpec,
)
from accretion.experience.models import (
    Experience,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import _PREFIXES, has_prefix, new_id
from accretion.persistence.models import V04_M0_ROUTING_TABLES
from accretion.persistence.store import (
    REASON_CODE_PATTERN,
    ROUTING_OVERRIDE_DOCUMENT_TYPE,
    MemoryStore,
    PostgresStore,
    StateStore,
    _build_routing_override_payload,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"

# One contract per table that stores a frozen model, so that the round-trip, immutability
# and ordering tests below run over all sixteen rather than over a convenient one.
TABLE_CONTRACTS: dict[str, type[CanonicalContract]] = {
    "objective_contracts": ObjectiveContract,
    "node_contracts": NodeContract,
    "verification_specs": VerificationSpec,
    "routing_requests": RoutingContext,
    "configuration_candidates": ConfigurationCandidate,
    "compatibility_decisions": CompatibilityDecision,
    "routing_receipts": RoutingDecisionReceipt,
    "verification_results": IndependentVerificationResult,
    "experience_records": ExperienceRecord,
    "failure_events": FailureEvent,
    "router_model_versions": RouterModelVersion,
    "router_training_snapshots": RouterTrainingSnapshot,
    "router_promotion_reports": RouterPromotionReport,
    "shadow_decisions": ShadowDecision,
    "shadow_rollout_results": ShadowRolloutResult,
    "router_activations": RouterActivation,
}
TABLE_IDS = sorted(TABLE_CONTRACTS)

# A field that can be varied on each contract to produce a *different* document with the
# same shape — which is what a "revision" and a "tampered payload" both need.
VARIABLE_FIELD: dict[str, str] = {
    "objective_contracts": "goal",
    "node_contracts": "objective",
    "verification_specs": "revision",
    "routing_requests": "policy_snapshot_id",
    "configuration_candidates": "routing_request_id",
    "compatibility_decisions": "rule_version",
    "routing_receipts": "routing_request_id",
    "verification_results": "execution_instance_id",
    "experience_records": "source_node_execution_id",
    "failure_events": "affected_layer",
    "router_model_versions": "artifact_digest",
    "router_training_snapshots": "contradiction_treatment",
    "router_promotion_reports": "rollback_target",
    "shadow_decisions": "comparison_notes",
    "shadow_rollout_results": "fork_execution_id",
    "router_activations": "family_key",
}
VARIABLE_VALUE: dict[str, Any] = {
    # The golden fixture is already at revision 2, so a variant has to be somewhere else.
    "verification_specs": 7,
    "router_model_versions": "9" * 64,
}

_DIGESTS = itertools.count(1)


def unique_digest() -> str:
    """A distinct, well-formed 64-hex digest per call.

    Router versions have to differ in their *artifact*, not only in their id, or the
    §13.1 hash-uniqueness rule would fire before the active-router rule under test and
    the wrong assertion would pass.
    """

    return f"{next(_DIGESTS):064x}"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-sealed after whatever this test changed.

    ``content_hash`` is dropped rather than recomputed here: the model seals itself, so a
    test that computed the digest by hand would be testing its own arithmetic. Dropping it
    also means a test cannot accidentally assert against a stale digest.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def variant(table: str, **overrides: Any) -> CanonicalContract:
    """A second, genuinely different document of the same kind."""

    field = VARIABLE_FIELD[table]
    value = VARIABLE_VALUE.get(table, f"varied-{new_id('event')}")
    return build(TABLE_CONTRACTS[table], **{field: value}, **overrides)


# The one workspace and the one project every golden fixture in this family is scoped to.
# Read out of a fixture rather than typed twice, so a re-scoped fixture cannot leave the
# seeded project naming something no record points at.
FIXTURE_WORKSPACE_ID: str = json.loads(
    (FIXTURE_ROOT / "objective_contract" / "minimal.json").read_text(encoding="utf-8")
)["workspace_id"]
FIXTURE_PROJECT_ID: str = json.loads(
    (FIXTURE_ROOT / "objective_contract" / "minimal.json").read_text(encoding="utf-8")
)["project_id"]


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


async def new_store() -> MemoryStore:
    """A fresh ``MemoryStore`` holding the project every v0.4 record here references.

    Every v0.4 table has ``project_id -> projects.id`` and ``MemoryStore`` now mirrors that
    key, so a store with no projects in it refuses every project-scoped contract exactly as
    PostgreSQL would. Seeding the project is therefore part of setting up a v0.4 test, not
    an incidental detail: a test that skipped it would be testing the foreign key.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=FIXTURE_PROJECT_ID,
            name="v0.4 M0 fixtures",
            repository_path=Path("/tmp/accretion-v04-m0"),
        )
    )
    return store


async def seed_experience(store: MemoryStore, experience_id: str) -> None:
    """The v0.2 P7 experience an ``ExperienceRecord`` of that id projects (ADR-054 b).

    ``experience_records.experience_id`` is the foreign key into ``experiences`` (migration
    0020), so a projection of an experience that was never captured is a record of nothing.
    ``build`` mints a fresh ``exp_`` id per call, and a record put with no explicit parent
    is its own root, which is why this is seeded per record rather than once per store.
    """

    if experience_id in store.experiences:
        return
    experience = Experience(
        experience_id=experience_id,
        project_id=FIXTURE_PROJECT_ID,
        repository_identity=digest(FIXTURE_PROJECT_ID),
        task_id=new_id("task"),
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=new_id("run"),
        source_commit="b" * 40,
        architecture_version="2.0",
        manifest_digest=digest(f"manifest-{experience_id}"),
        policy_digest=digest(f"policy-{experience_id}"),
        verifier_digest=digest(f"verifier-{experience_id}"),
        prompt_digest=digest(f"prompt-{experience_id}"),
        context_digest=digest(f"context-{experience_id}"),
        tool_profile_digest=digest(f"tools-{experience_id}"),
        provider=Provider.FAKE,
        runtime_model="fake",
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest(f"experience-{experience_id}"),
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest(f"segment-{experience_id}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience_id,
        input_digest=digest(f"embedding-{experience_id}"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)


async def put(store: MemoryStore, table: str, record: Any) -> Any:
    """Write one record, having first made the rows its foreign keys name exist.

    The referenced project and experience are set up here rather than inside each test
    because they are the *precondition* of a v0.4 write, not the subject of one; the tests
    that prove the two keys are enforced call the store directly instead.
    """

    if table == "experience_records":
        await seed_experience(store, record.contract_id)
    return await getattr(store, f"put_{table[:-1]}")(record)


def get(store: MemoryStore, table: str, contract_id: str) -> Any:
    return getattr(store, f"get_{table[:-1]}")(contract_id)


def listing(store: MemoryStore, table: str, **scope: Any) -> Any:
    """A thin pass-through, with no default scope of its own.

    ``workspace_id`` is a required keyword on every ``list_`` method and this helper does
    not supply one, because a helper that filled it in would hide the very ergonomics the
    signature exists to enforce: a caller of the real store cannot omit it either.
    """

    return getattr(store, f"list_{table}")(**scope)


async def setup_store(table: str) -> tuple[MemoryStore, CanonicalContract]:
    """A fresh store holding exactly one contract of the given kind."""

    store = await new_store()
    record = build(TABLE_CONTRACTS[table])
    await put(store, table, record)
    return store, record


def router_version(
    *,
    status: RouterStatus,
    scope: RouterScope = RouterScope.TEAM_WORKSPACE,
    workspace_id: str = "wks_8G33T24F686H6EJPBHRSFYCC3C",
    project_id: str | None = None,
    algorithm_id: str = "gradient-boosted-ranker",
    artifact_digest: str | None = None,
) -> RouterModelVersion:
    overrides: dict[str, Any] = {
        "status": status.value,
        "scope": scope.value,
        "workspace_id": workspace_id,
        "algorithm_id": algorithm_id,
        "artifact_digest": artifact_digest or unique_digest(),
    }
    if project_id is not None:
        overrides["project_id"] = project_id
    return build(RouterModelVersion, **overrides)


# ------------------------------------------------------------------- round trip


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_a_contract_is_read_back_exactly_as_it_was_written(table: str) -> None:
    store, record = await setup_store(table)

    read_back = await get(store, table, record.contract_id)

    assert read_back == record
    assert read_back is not record
    assert read_back.content_hash == record.content_hash


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_an_unknown_contract_id_reads_back_as_none(table: str) -> None:
    store, _record = await setup_store(table)

    assert await get(store, table, "obj_NOTHINGSTOREDUNDERTHISID0") is None


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_a_stored_contract_lists_under_its_workspace_and_its_project(table: str) -> None:
    store, record = await setup_store(table)

    assert await listing(store, table, workspace_id=record.workspace_id) == [record]
    if record.project_id is not None:
        assert await listing(
            store, table, workspace_id=record.workspace_id, project_id=record.project_id
        ) == [record]
        assert (
            await listing(
                store, table, workspace_id=record.workspace_id, project_id="prj_elsewhere"
            )
            == []
        )
    assert await listing(store, table, workspace_id="wks_someone_else") == []


# ----------------------------------------------------------------- immutability


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_writing_the_same_contract_twice_is_a_no_op(table: str) -> None:
    """A retry, a replayed event, an at-least-once delivery: none of them is an error."""

    store, record = await setup_store(table)

    again = await put(store, table, record)

    assert again == record
    assert await listing(store, table, workspace_id=record.workspace_id) == [record]


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_a_different_payload_under_a_stored_id_is_refused(table: str) -> None:
    store, record = await setup_store(table)
    tampered = variant(table, contract_id=record.contract_id)
    assert tampered.content_hash != record.content_hash

    with pytest.raises(ValueError, match="is immutable"):
        await put(store, table, tampered)

    # Refused, not partially applied: the original is still the stored document.
    assert await get(store, table, record.contract_id) == record
    assert await listing(store, table, workspace_id=record.workspace_id) == [record]


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_the_same_document_cannot_be_filed_again_under_a_new_id(table: str) -> None:
    """§13.1: contract hash/version tuples are unique.

    ``contract_id`` is inside the hashed body, so two ids sharing a digest means one of
    them was written under a forged header rather than that the same thing happened twice.
    """

    store, record = await setup_store(table)
    forged = record.model_copy(
        update={"contract_id": build(TABLE_CONTRACTS[table]).contract_id}
    )

    # Matched on the hash-reuse wording and not merely on "is immutable": a forged copy of
    # a receipt also re-uses its ``routing_request_id``, so a looser match would let that
    # table pass this test on the strength of a different §13.1 rule and leave the hash
    # rule unproven for the one table where two rules overlap.
    with pytest.raises(
        ValueError, match=r"is immutable: content hash \w{64} at schema version"
    ):
        await put(store, table, forged)

    assert await listing(store, table, workspace_id=record.workspace_id) == [record]


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_a_revision_is_a_second_row_and_both_of_them_list(table: str) -> None:
    """Registry §17: historical records are never rewritten in place.

    The revision names its parent through ``supersedes_contract_id`` and gets its own id.
    The parent stays readable at its own id, unchanged, because a decision that was made
    on the old contract has to remain explicable.
    """

    store, parent = await setup_store(table)
    revision = variant(table, supersedes_contract_id=parent.contract_id)
    await put(store, table, revision)

    assert revision.contract_id != parent.contract_id
    assert await get(store, table, parent.contract_id) == parent
    assert await get(store, table, revision.contract_id) == revision
    listed = await listing(store, table, workspace_id=parent.workspace_id)
    assert len(listed) == 2
    assert {item.contract_id for item in listed} == {
        parent.contract_id,
        revision.contract_id,
    }


@pytest.mark.parametrize("table", TABLE_IDS)
async def test_a_stored_payload_that_lost_its_digest_is_refused_on_read(table: str) -> None:
    """``CanonicalContract`` seals an unsealed document; a reader must not let it.

    Simulates the partial write, the hand edit, the dropped column. Without this guard the
    record would come back as a validly sealed copy of whatever its body now said, with
    nothing to show it had ever been anything else.
    """

    store, record = await setup_store(table)
    store.v04_contracts[table][record.contract_id].payload.pop("content_hash")

    with pytest.raises(ValueError, match="carries no content_hash"):
        await get(store, table, record.contract_id)


# --------------------------------------------------------------------- ordering


async def test_contracts_list_in_a_deterministic_created_at_then_id_order() -> None:
    """The tie-break matters: fixtures share a pinned clock to the millisecond."""

    store = await new_store()
    base = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    later = build(ObjectiveContract, goal="second", created_at=base.isoformat())
    earlier = build(
        ObjectiveContract,
        goal="first",
        created_at=(base - timedelta(minutes=5)).isoformat(),
    )
    same_instant = build(ObjectiveContract, goal="third", created_at=base.isoformat())

    # Written in an order that is not the order they must come back in.
    for record in (same_instant, later, earlier):
        await store.put_objective_contract(record)

    listed = await store.list_objective_contracts(workspace_id=FIXTURE_WORKSPACE_ID)

    assert [item.contract_id for item in listed] == [
        earlier.contract_id,
        *sorted([later.contract_id, same_instant.contract_id]),
    ]


# ----------------------------------------------------------------------- scoping


@pytest.mark.parametrize("table", sorted(V04_M0_ROUTING_TABLES))
def test_a_v04_listing_cannot_be_taken_without_naming_a_workspace(table: str) -> None:
    """The tenancy filter is a required keyword, so forgetting it cannot compile.

    An earlier draft defaulted ``workspace_id`` to ``None`` and applied no filter when it
    was absent, which made an unscoped cross-tenant read of every provenance row in a
    table a legal, silent, type-checking call — a caller wiring one of these behind a
    route had only to forget an argument. There is no unscoped listing now, on any of the
    three surfaces, and this is what stops one coming back as a convenience.
    """

    store = MemoryStore()

    with pytest.raises(TypeError, match="workspace_id"):
        getattr(store, f"list_{table}")()


@pytest.mark.parametrize("table", sorted(V04_M0_ROUTING_TABLES))
def test_no_v04_listing_declares_a_default_workspace_on_any_of_the_three_surfaces(
    table: str,
) -> None:
    """The same rule as a property of the declaration, not only of ``MemoryStore``.

    ``PostgresStore`` needs a database to call and ``StateStore`` cannot be called at all,
    so the runtime check above reaches neither. A default reintroduced on either of them
    would be the same cross-tenant read; this reads it off the signature instead.
    """

    for implementation in (StateStore, MemoryStore, PostgresStore):
        parameter = inspect.signature(
            getattr(implementation, f"list_{table}")
        ).parameters["workspace_id"]
        assert parameter.default is inspect.Parameter.empty, implementation.__name__
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, implementation.__name__
        assert parameter.annotation == "str", implementation.__name__


async def test_a_listing_never_returns_another_workspaces_rows() -> None:
    """Two tenants, one table, and neither can see the other — registry §16.

    Asserted from both sides rather than only from the empty one: a filter that dropped
    everything would satisfy "workspace B sees none of A's rows" while breaking the store.
    """

    store = await new_store()
    ours = build(ObjectiveContract, goal="Ours.")
    theirs = build(
        ObjectiveContract, goal="Theirs.", workspace_id="wks_a_different_tenant"
    )
    await store.put_objective_contract(ours)
    await store.put_objective_contract(theirs)

    assert await store.list_objective_contracts(
        workspace_id=FIXTURE_WORKSPACE_ID
    ) == [ours]
    assert await store.list_objective_contracts(
        workspace_id="wks_a_different_tenant"
    ) == [theirs]


# ------------------------------------------------- §13.1 partial unique rules


async def test_a_workspace_may_hold_only_one_active_workspace_router() -> None:
    """§13.1, mirroring ``uq_router_versions_active_workspace``."""

    store = await new_store()
    active = router_version(status=RouterStatus.ACTIVE)
    await store.put_router_model_version(active)

    with pytest.raises(ValueError, match="already has an ACTIVE workspace router"):
        await store.put_router_model_version(router_version(status=RouterStatus.ACTIVE))

    assert [
        item.contract_id
        for item in await store.list_router_model_versions(
            workspace_id=active.workspace_id
        )
    ] == [active.contract_id]


async def test_a_second_workspace_may_hold_its_own_active_router() -> None:
    store = await new_store()
    await store.put_router_model_version(router_version(status=RouterStatus.ACTIVE))

    await store.put_router_model_version(
        router_version(status=RouterStatus.ACTIVE, workspace_id="wks_another_workspace")
    )

    # One listing per workspace, because there is no listing across workspaces: that the
    # two routers are invisible to each other's tenant is the point of the rule holding
    # per workspace.
    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 1
    assert (
        len(await store.list_router_model_versions(workspace_id="wks_another_workspace")) == 1
    )


async def test_non_active_router_versions_are_not_constrained_at_all() -> None:
    """The whole point of a *partial* index: candidates and shadows may pile up."""

    store = await new_store()
    await store.put_router_model_version(router_version(status=RouterStatus.ACTIVE))

    for status in (
        RouterStatus.CANDIDATE,
        RouterStatus.SHADOW,
        RouterStatus.RETIRED,
        RouterStatus.ROLLED_BACK,
    ):
        await store.put_router_model_version(router_version(status=status))

    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 5


async def test_a_project_may_hold_only_one_active_adapter_per_algorithm() -> None:
    """§13.1, mirroring ``uq_router_versions_active_project_adapter``."""

    store = await new_store()
    project_id = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
    await store.put_router_model_version(
        router_version(
            status=RouterStatus.ACTIVE,
            scope=RouterScope.PROJECT_ADAPTER,
            project_id=project_id,
        )
    )

    with pytest.raises(ValueError, match="already has an ACTIVE"):
        await store.put_router_model_version(
            router_version(
                status=RouterStatus.ACTIVE,
                scope=RouterScope.PROJECT_ADAPTER,
                project_id=project_id,
            )
        )

    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 1


async def test_two_algorithms_may_each_hold_an_active_adapter_for_one_project() -> None:
    """Two adapters fitted by different algorithms are a comparison, not a conflict."""

    store = await new_store()
    project_id = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
    for algorithm in ("gradient-boosted-ranker", "linear-thompson"):
        await store.put_router_model_version(
            router_version(
                status=RouterStatus.ACTIVE,
                scope=RouterScope.PROJECT_ADAPTER,
                project_id=project_id,
                algorithm_id=algorithm,
            )
        )

    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 2


async def test_an_active_adapter_does_not_collide_with_the_active_workspace_router() -> None:
    store = await new_store()
    await store.put_router_model_version(router_version(status=RouterStatus.ACTIVE))

    await store.put_router_model_version(
        router_version(
            status=RouterStatus.ACTIVE,
            scope=RouterScope.PROJECT_ADAPTER,
            project_id="prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
        )
    )

    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 2


async def test_re_putting_the_active_router_is_still_a_no_op() -> None:
    """The uniqueness guard must not turn an idempotent retry into a conflict."""

    store = await new_store()
    active = router_version(status=RouterStatus.ACTIVE)
    await store.put_router_model_version(active)

    assert await store.put_router_model_version(active) == active
    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 1


# ------------------------------------------------------- §13 referential rules


async def test_a_record_naming_a_project_the_store_does_not_hold_is_refused() -> None:
    """Every v0.4 table has ``project_id -> projects.id``, and both backends refuse alike.

    PostgreSQL raises an ``IntegrityError`` out of the commit for this row. Unmirrored,
    ``MemoryStore`` accepted it, so a unit test written against the memory backend could
    pass on a record the database will refuse — the exact divergence the module header
    forbids, and the reason the receipt rule is mirrored too.
    """

    store = MemoryStore()
    record = build(ObjectiveContract)
    assert record.project_id is not None

    with pytest.raises(ValueError, match="is not in projects"):
        await store.put_objective_contract(record)

    assert await store.list_objective_contracts(workspace_id=record.workspace_id) == []
    assert await store.get_objective_contract(record.contract_id) is None


async def test_a_workspace_scoped_record_needs_no_project_at_all() -> None:
    """The key is checked, not required: ``project_id`` is nullable in §13 and here."""

    store = MemoryStore()
    version = router_version(status=RouterStatus.ACTIVE)
    assert version.project_id is None

    assert await store.put_router_model_version(version) == version


async def test_an_experience_record_for_an_experience_that_was_never_captured_is_refused(
) -> None:
    """ADR-054 b: the projection's ``experience_id`` is its key into ``experiences``.

    A projection of an experience that does not exist is not a record with a dangling
    field, it is a record of nothing — which is why PostgreSQL refuses it and why this
    refuses it identically rather than storing an orphan the database could never hold.
    A record put with no explicit parent is its own root, so the refusal below is still
    reached by a record whose ``contract_id`` names no experience.
    """

    store = await new_store()
    record = build(ExperienceRecord)

    with pytest.raises(ValueError, match="is not in experiences"):
        await store.put_experience_record(record)

    assert await store.list_experience_records(workspace_id=record.workspace_id) == []

    # And it is accepted once the experience it projects exists.
    await seed_experience(store, record.contract_id)
    assert await store.put_experience_record(record) == record


# --------------------------------------------------------- receipts and overrides


async def test_a_receipt_is_found_by_the_routing_request_it_answers() -> None:
    """§8.2: ``routing_request_id`` is the idempotency key."""

    store = await new_store()
    receipt = build(RoutingDecisionReceipt)
    await store.put_routing_receipt(receipt)

    found = await store.get_routing_receipt_for_request(receipt.routing_request_id)

    assert found == receipt
    assert await store.get_routing_receipt_for_request("rrq_never_issued") is None


async def test_a_second_receipt_for_one_routing_request_is_refused() -> None:
    """§13.1 and §8.2, in memory, where ``routing_request_id UNIQUE`` does not exist.

    The twin of ``test_a_receipt_is_unique_per_routing_request_in_the_database``. Without
    the mirrored pre-check this store accepts both receipts, and every M2 unit test written
    against ``MemoryStore`` would see a retried dispatch quietly produce a second,
    differently-argued answer to one routing request — while the same code in production
    fails the insert. The two receipts here differ in ``policy_snapshot_id``, so they are
    genuinely different documents with genuinely different digests: nothing but the
    routing-request rule can refuse the second one.
    """

    store = await new_store()
    first = build(RoutingDecisionReceipt, routing_request_id="rrq_shared")
    second = build(
        RoutingDecisionReceipt,
        routing_request_id="rrq_shared",
        policy_snapshot_id="pol_a_second_snapshot",
    )
    await store.put_routing_receipt(first)

    assert first.content_hash != second.content_hash
    with pytest.raises(ValueError, match="already has receipt"):
        await store.put_routing_receipt(second)

    assert await store.list_routing_receipts(workspace_id=first.workspace_id) == [first]
    assert await store.get_routing_receipt_for_request("rrq_shared") == first


async def test_re_putting_one_receipt_is_still_a_no_op() -> None:
    """The routing-request guard must not turn an idempotent retry into a conflict.

    §8.2 is the reason the rule exists: repeated requests with identical immutable inputs
    return the same receipt. A guard that could not tell a replay from a rival would have
    inverted the very promise it was added to keep.
    """

    store = await new_store()
    receipt = build(RoutingDecisionReceipt, routing_request_id="rrq_replayed")
    await store.put_routing_receipt(receipt)

    assert await store.put_routing_receipt(receipt) == receipt
    assert await store.list_routing_receipts(
        workspace_id=receipt.workspace_id
    ) == [receipt]


async def setup_override(
    store: MemoryStore, **overrides: Any
) -> dict[str, Any]:
    """Arguments for one routing override, with the clock pinned unless a test unpins it.

    ``new_id("routing_override")`` and not ``new_id("override")``: ``override`` is the
    v0.1/v0.2 *strategy* override kind that ``planning.py`` mints, and minting routing
    override ids from it would leave an ``ovr_`` id unable to say which record class it
    names. See ``test_a_routing_override_id_is_minted_from_its_own_kind``.
    """

    arguments: dict[str, Any] = {
        "override_id": new_id("routing_override"),
        "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C",
        "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
        "receipt_id": "rcp_1YWV9H9QDV4D7S8EQ2J7M91K1Y",
        "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
        "candidate_id": "ccd_1YWV9H9QDV4D7S8EQ2J7M91K2Z",
        "reason_code": "EXPERIMENTAL_COMPARISON",
        "reason": "Testing a compatible lower-cost runtime.",
        "created_at": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
    }
    arguments.update(overrides)
    result: dict[str, Any] = await store.put_routing_override(**arguments)
    return result


async def test_a_routing_override_is_sealed_and_reads_back_as_stored() -> None:
    """The one §13 table with no frozen contract still gets a digest and a guard."""

    store = await new_store()
    override = await setup_override(store)

    assert len(override["content_hash"]) == 64
    assert override["document_type"] == ROUTING_OVERRIDE_DOCUMENT_TYPE
    assert await store.get_routing_override(override["contract_id"]) == override
    assert await store.list_routing_overrides(workspace_id=override["workspace_id"]) == [
        override
    ]


async def test_the_override_document_type_is_outside_the_frozen_namespace() -> None:
    """PR3 writes a pre-contract record; it must not claim a name M2 has to honour.

    Every one of PR2's nineteen ``CONTRACT_TYPE`` values is an ``accretion.<contract>``
    token matching ``CanonicalContract.contract_type``'s pattern, and a document filed
    under a value of that shape is a promise that it validates as the contract of that
    name. This one cannot: it carries none of ``created_by``, ``objective_contract_ref``,
    ``labels`` or ``retention_class``, and because ADR-056 hashes the whole body, a row
    sealed over the smaller field set can never be rescued by adding them later — the
    digest recomputes differently and the reader reports a record edited after sealing. So
    the value is held outside the namespace and outside the pattern, and it is *not*
    written to a field called ``contract_type``.
    """

    patterns = [
        item.pattern
        for item in CanonicalContract.model_fields["contract_type"].metadata
        if hasattr(item, "pattern")
    ]
    assert len(patterns) == 1, "CanonicalContract.contract_type lost its shape constraint"
    assert re.fullmatch(patterns[0], ROUTING_OVERRIDE_DOCUMENT_TYPE) is None
    assert ROUTING_OVERRIDE_DOCUMENT_TYPE not in {
        model.CONTRACT_TYPE for model in CONTRACT_INVENTORY
    }

    store = await new_store()
    override = await setup_override(store)
    assert "contract_type" not in override
    assert override["document_type"] == ROUTING_OVERRIDE_DOCUMENT_TYPE


async def test_an_m0_override_record_does_not_validate_as_a_contract() -> None:
    """The M0 -> M2 incompatibility as a red test, not as a sentence in the freeze record.

    ``docs/releases/v0.4/m0-freeze.md`` records that M2's ``RoutingOverride`` **replaces**
    this pre-contract document and that the replacement is Major and fail-closed under
    registry §3.2 — not the additive Minor change an earlier draft of that page claimed.
    Both shapes stamp ``schema_version`` ``1.0.0``, so the only discriminator is the field
    name: a row carrying ``document_type`` is an M0 record and must never be fed to a
    contract's ``model_validate``; an M2 row carries ``contract_type``.

    Asserted through ``CanonicalContract`` because that is the base every v0.4 contract
    inherits, ``RoutingOverride`` included: whatever M2 names its model, feeding an M0 row
    to it fails here first. ``extra="forbid"`` rejects ``document_type`` and the six
    override fields the header has no place for, and ``created_by`` — the registry §3
    field this record does not carry — is missing. The three header fields that *do* have
    registry defaults (``objective_contract_ref``, ``labels``, ``retention_class``) are no
    consolation: ADR-056 hashes the whole body, so a reader that filled them in would be
    validating a document whose digest was computed without them.
    """

    document = _build_routing_override_payload(
        override_id=new_id("routing_override"),
        workspace_id="wks_8G33T24F686H6EJPBHRSFYCC3C",
        project_id="prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
        receipt_id="rcp_1YWV9H9QDV4D7S8EQ2J7M91K1Y",
        principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
        candidate_id="ccd_1YWV9H9QDV4D7S8EQ2J7M91K2Z",
        reason_code="EXPERIMENTAL_COMPARISON",
        reason="Testing a compatible lower-cost runtime.",
        superseding_receipt_id=None,
        supersedes_contract_id=None,
        schema_version=CONTRACT_SCHEMA_VERSION,
        created_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(ValidationError) as refusal:
        CanonicalContract.model_validate(document)

    reported = {
        (str(error["loc"][0]), error["type"])
        for error in refusal.value.errors()
        if error["loc"]
    }
    # Both halves of the incompatibility, so neither can be fixed alone and look repaired:
    # the field name that discriminates the two shapes, and the header field an M0 row has
    # never carried.
    assert ("document_type", "extra_forbidden") in reported
    assert ("created_by", "missing") in reported
    # And the stored document is what a live put actually writes, not only what the
    # builder returns in isolation.
    store = await new_store()
    stored = await setup_override(store)
    assert "document_type" in stored and "contract_type" not in stored
    with pytest.raises(ValidationError):
        CanonicalContract.model_validate(stored)


async def test_a_routing_override_id_is_minted_from_its_own_kind_not_the_strategy_one() -> None:
    """ADR-055: an id prefix names exactly one record class.

    ``ids.py`` has mapped ``override -> ovr`` since v0.1 and ``planning.py`` mints it for
    the strategy override. Sharing it would mean an ``ovr_`` id no longer determined which
    table it named, and would give M2's ``RoutingOverride.ID_KIND`` prefix check a second
    claimant to accept.
    """

    assert _PREFIXES["override"] == "ovr"
    assert _PREFIXES["routing_override"] == "rov"
    assert has_prefix(new_id("routing_override"), "routing_override")

    store = await new_store()
    override = await setup_override(store)
    assert override["contract_id"].startswith("rov_")


async def test_a_routing_override_is_immutable_and_a_repeat_is_a_no_op() -> None:
    store = await new_store()
    override = await setup_override(store)

    assert await setup_override(store, override_id=override["contract_id"]) == override
    with pytest.raises(ValueError, match="routing override .* is immutable"):
        await setup_override(
            store, override_id=override["contract_id"], reason="A different reason."
        )

    assert await store.list_routing_overrides(
        workspace_id=override["workspace_id"]
    ) == [override]


async def test_a_routing_override_retried_without_a_clock_is_a_no_op() -> None:
    """The one table whose body the *store* builds, so the store can break its own no-op.

    ``created_at`` defaults to the wall clock and is inside the hashed body, so comparing
    whole documents would make two identical calls a millisecond apart look like a rewrite
    and raise ``... is immutable`` — turning the ordinary at-least-once redelivery of
    §11.1's override endpoint into a spurious conflict. Every other override test pins the
    clock, which is exactly why this one must not: a frozen clock hides the bug.
    """

    store = await new_store()
    arguments: dict[str, Any] = {
        "override_id": new_id("routing_override"),
        "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C",
        "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
        "receipt_id": "rcp_1YWV9H9QDV4D7S8EQ2J7M91K1Y",
        "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
        "candidate_id": "ccd_1YWV9H9QDV4D7S8EQ2J7M91K2Z",
        "reason_code": "EXPERIMENTAL_COMPARISON",
        "reason": "Testing a compatible lower-cost runtime.",
    }

    first = await store.put_routing_override(**arguments)
    second = await store.put_routing_override(**arguments)

    assert first == second
    assert await store.list_routing_overrides(
        workspace_id=first["workspace_id"]
    ) == [first]
    # The retry got the *stored* document back, clock and digest included, rather than its
    # own freshly stamped copy — which is what makes it a no-op and not a second answer.
    assert second["created_at"] == first["created_at"]
    assert second["content_hash"] == first["content_hash"]


async def test_a_routing_override_retry_that_changes_an_argument_is_still_refused() -> None:
    """Narrowing the retry comparison must not narrow the immutability guarantee.

    The clock is excluded because the caller does not supply it; every field the caller
    *does* supply is still compared, and the error names the ones that moved.
    """

    store = await new_store()
    override = await setup_override(store)

    with pytest.raises(ValueError, match="is immutable: candidate_id differ"):
        await setup_override(
            store,
            override_id=override["contract_id"],
            created_at=None,
            candidate_id="ccd_a_different_candidate",
        )

    assert await store.list_routing_overrides(
        workspace_id=override["workspace_id"]
    ) == [override]


async def test_a_routing_override_retried_with_a_different_explicit_clock_is_refused(
) -> None:
    """A ``created_at`` the caller supplied is one of the caller's arguments.

    The clock is excluded from the retry comparison only when the *store* stamped it, so
    that an at-least-once redelivery of §11.1's override endpoint is a no-op. Excluding a
    ``created_at`` the caller passed would make a replay under one id with a different
    instant a silent no-op returning the first document — handing the caller a
    ``content_hash`` that is not the digest of the document it asked to store, with
    nothing to say anything had been ignored, and no way back because the row is sealed.
    """

    store = await new_store()
    override = await setup_override(store)

    with pytest.raises(ValueError, match="is immutable: created_at differ"):
        await setup_override(
            store,
            override_id=override["contract_id"],
            created_at=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
        )

    # The same instant is still a no-op, and so is a retry that supplies no clock at all.
    assert await setup_override(store, override_id=override["contract_id"]) == override
    assert await setup_override(
        store, override_id=override["contract_id"], created_at=None
    ) == override
    assert await store.list_routing_overrides(
        workspace_id=override["workspace_id"]
    ) == [override]


@pytest.mark.parametrize(
    "reason_code",
    [
        "experimental comparison",  # lower case and a space
        "Experimental",  # title case
        "1_ABC",  # leading digit
        "_A",  # leading underscore
        "",  # empty
        "ABC-DEF",  # a hyphen is not in the alphabet
    ],
)
async def test_a_routing_override_reason_code_must_match_the_documented_pattern(
    reason_code: str,
) -> None:
    """The check must be the rule the error message quotes, not a looser approximation.

    ``1_ABC`` and ``_A`` are the two the earlier ``isupper()``/``isalnum()`` test accepted
    and ``^[A-Z][A-Z0-9_]*$`` rejects. They matter because ``reason_code`` is a promoted
    column *and* inside the ADR-056 hashed body: a code the freeze record says is
    impossible would be sealed into an immutable row that no method can correct, only
    supersede.
    """

    store = await new_store()

    with pytest.raises(ValueError, match="not an upper-case token"):
        await setup_override(store, reason_code=reason_code)

    assert await store.list_routing_overrides(workspace_id=FIXTURE_WORKSPACE_ID) == []


def test_the_override_reason_code_pattern_is_the_one_the_frozen_contracts_use() -> None:
    """One spelling of a reason code in the family, and ``routing_overrides`` shares it.

    The table has no frozen model to carry the constraint, so the store spells it out; if
    the two ever drifted, a value legal in a ``CompatibilityDecision`` would be refused as
    an override reason or the other way round, for no reason a caller could discover.
    """

    frozen = [
        item.pattern
        for item in CompatibilityDecision.model_fields["reason_code"].metadata
        if hasattr(item, "pattern")
    ]

    assert frozen == [f"^{REASON_CODE_PATTERN}$"]


async def test_a_stored_routing_override_has_exactly_the_frozen_key_set() -> None:
    """The shape a live store writes is the shape ``m0-freeze.md`` records.

    ``tests/test_v04_m0_persistence_models.py`` pins the golden document and its digest;
    this asserts that what actually lands in the table is the same shape, so a builder
    change cannot be frozen in the fixture and forked in the store.
    """

    store = await new_store()
    override = await setup_override(store)
    golden = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "records"
            / "v0.4"
            / "routing_override"
            / "minimal.json"
        ).read_text(encoding="utf-8")
    )

    assert set(override) == set(golden)


# ------------------------------------------------------ the shape of the surface


def v04_method_names() -> set[str]:
    """The method names the seventeen tables imply, derived and never listed by hand.

    A table added to ``V04_M0_ROUTING_TABLES`` without store methods is a red test here,
    which is the only way this file stays complete as v0.4 grows.
    """

    names = set()
    for table in V04_M0_ROUTING_TABLES:
        names |= {f"put_{table[:-1]}", f"get_{table[:-1]}", f"list_{table}"}
    return names


def test_the_protocol_declares_put_get_and_list_for_every_v04_table() -> None:
    declared = {
        name for name, _member in inspect.getmembers(StateStore, predicate=inspect.isfunction)
    }

    assert v04_method_names() <= declared


@pytest.mark.parametrize("name", sorted(v04_method_names()))
def test_both_backends_implement_every_v04_protocol_method_identically(name: str) -> None:
    """Parity by signature, not merely by name.

    The PostgreSQL round-trip test asserts that the two stores *behave* alike; this asserts
    that they can be called alike, which is the failure that would otherwise show up as a
    type error in a milestone nobody has written yet.
    """

    signatures = {
        implementation.__name__: inspect.signature(getattr(implementation, name))
        for implementation in (StateStore, MemoryStore, PostgresStore)
    }

    assert len(set(signatures.values())) == 1, signatures


def test_the_store_exposes_no_way_to_update_or_delete_a_v04_record() -> None:
    """§13.1: promotion reports are append-only — and so is everything beside them.

    Enforced by absence. There is no trigger and no check constraint; there is simply no
    method on any of the three surfaces that could rewrite or remove one of these rows, and
    this is the test that keeps it that way.
    """

    forbidden = ("update_", "delete_", "purge_", "retract_", "upsert_", "set_")
    for implementation in (StateStore, MemoryStore, PostgresStore):
        for table in V04_M0_ROUTING_TABLES:
            singular = table[:-1]
            surface = {
                name
                for name in dir(implementation)
                if singular in name and not name.startswith("_")
            }
            assert not {
                name for name in surface if name.startswith(forbidden)
            }, (implementation.__name__, table)
            assert {f"put_{singular}", f"get_{singular}", f"list_{table}"} <= surface


def test_the_v04_surface_is_exactly_put_get_list_plus_the_three_named_lookups() -> None:
    """Three documented extras, and nothing else.

    §8.2's receipt-by-request-id lookup, and M3a's revision listing — the read that walks
    one experience's projections after migration 0020 gave them a shared ``experience_id``
    instead of a shared primary key. M2 adds the workspace-scoped graph receipt
    reader so dispatch can resolve an operator-amended head. A fourth must be
    documented here rather than silently widening the frozen store surface.
    """

    extra: set[str] = set()
    for implementation in (StateStore, MemoryStore, PostgresStore):
        surface = {
            name
            for name in dir(implementation)
            if not name.startswith("_")
            and any(name.endswith(table) or table[:-1] in name for table in V04_M0_ROUTING_TABLES)
        }
        extra |= surface - v04_method_names()

    assert extra == {
        "get_routing_receipt_for_request",
        "list_experience_record_revisions",
        "list_routing_receipts_for_run_graph",
    }


def test_a_fresh_memory_store_has_a_bucket_for_every_v04_table() -> None:
    store = MemoryStore()

    assert set(store.v04_contracts) == set(V04_M0_ROUTING_TABLES)
    assert all(bucket == {} for bucket in store.v04_contracts.values())
