"""The activation ledger: contiguity, the head, the composite write, and the resolver.

"Active" stopped being a value in ``router_model_versions.status`` and became the head of an
append-only sequence (ADR-061). Three properties make that definition safe, and each one has
its own section below.

**Contiguity.** ``uq_router_activations_sequence`` makes a duplicate number impossible and
says nothing about a *gap*. A ledger with entries 1, 2 and 4 satisfies every constraint on
the table and is unreadable as history: nobody can tell whether entry 3 was never written or
was written and lost. Both stores therefore refuse ``head + 2`` and refuse a later entry that
claims to displace nothing, with the same message, built by the same module-level function.

**The head.** ``head_router_activation`` is a single-row read on the partition key, because
it is asked once per routing request. ``None`` for a family that has never been activated is
the cold-start case and not an error.

**The composite write.** Promotion writes two version rows and one ledger entry, and a
database holding some of those has no readable answer to "which version is active". The
refusal tests here assert the store is *unchanged*, not merely that an exception was raised.

There is no ``conftest.py``. Every builder is module-local, every store is a fresh
:class:`~accretion.persistence.store.MemoryStore`, and every assertion reads back from the
store rather than trusting the object that was passed to it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from accretion.contracts import PrincipalRef, PrincipalStatus, Project
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    RouterActivation,
    RouterActivationKind,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.activation import (
    WORKSPACE_FAMILY_KEY,
    ActivationLedger,
    LedgerActiveVersionResolver,
    LedgerActiveVersions,
    adapter_family_key,
    family_key_for,
)
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.train import ALGORITHM_ID

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "0019_v04_m8_router_activation_ledger.py"
)
RETIRED_INDEXES = (
    "uq_router_versions_active_workspace",
    "uq_router_versions_active_project_adapter",
)

APPROVER = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M8 approver",
    status=PrincipalStatus.ACTIVE,
)


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted to this run's ids and re-sealed.

    The same builder the freeze-delta tests use. Building these documents by hand would let
    them drift from the sealed shape the moment a field moved, and the drift would be
    invisible: every assertion below is about ``sequence`` and ``family_key``, which a
    hand-built object would carry just as happily under a schema that no longer matched.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def snake_case(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")


async def setup_ledger() -> tuple[MemoryStore, ActivationLedger, str]:
    """A fresh store, a ledger over it, and the workspace they are both about."""

    store = MemoryStore()
    workspace_id = f"wks_{uuid4().hex[:12]}"
    return store, ActivationLedger(store), workspace_id


async def setup_adapter_ledger(
    tmp_path: Path,
) -> tuple[MemoryStore, ActivationLedger, str, str]:
    """The same, plus a real ``projects`` row: a PROJECT_ADAPTER version needs one."""

    store, ledger, workspace_id = await setup_ledger()
    project_id = new_id("project")
    await store.create_project(
        Project(
            project_id=project_id,
            name="v0.4 M8 adapter project",
            repository_path=tmp_path,
        )
    )
    return store, ledger, workspace_id, project_id


def version(
    workspace_id: str,
    *,
    status: RouterStatus = RouterStatus.ACTIVE,
    scope: RouterScope = RouterScope.TEAM_WORKSPACE,
    project_id: str | None = None,
    algorithm_id: str = ALGORITHM_ID,
) -> RouterModelVersion:
    return build(
        RouterModelVersion,
        workspace_id=workspace_id,
        project_id=project_id,
        scope=scope.value,
        status=status.value,
        algorithm_id=algorithm_id,
        artifact_digest=digest(f"artifact-{uuid4().hex}"),
        calibration_artifact_digest=digest(f"calibration-{uuid4().hex}"),
    )


def digest(seed: str) -> str:
    from hashlib import sha256

    return sha256(seed.encode()).hexdigest()


def activation(
    workspace_id: str,
    *,
    sequence: int,
    router_version_id: str,
    previous_version_id: str | None = None,
    family_key: str = WORKSPACE_FAMILY_KEY,
    scope: RouterScope = RouterScope.TEAM_WORKSPACE,
    project_id: str | None = None,
    kind: RouterActivationKind = RouterActivationKind.PROMOTE,
    rollback_target_version_id: str | None = None,
    cause: str | None = None,
) -> RouterActivation:
    return build(
        RouterActivation,
        workspace_id=workspace_id,
        project_id=project_id,
        scope=scope.value,
        family_key=family_key,
        sequence=sequence,
        kind=kind.value,
        router_version_id=router_version_id,
        previous_version_id=previous_version_id,
        rollback_target_version_id=rollback_target_version_id,
        cause=cause,
        approved_by=APPROVER.model_dump(mode="json"),
    )


# ------------------------------------------------------------------- contiguity


async def test_the_first_entry_in_a_family_must_be_sequence_one() -> None:
    """An empty partition has head ``0``, so ``§7.14 requires 1`` and nothing else.

    The message spells the head as a number rather than as "no head" so that the arithmetic
    a reader has to do is the same in the empty case as in every other one.
    """

    store, _ledger, workspace_id = await setup_ledger()
    first = version(workspace_id)
    await store.put_router_model_version(first)

    with pytest.raises(ValueError) as refusal:
        await store.put_router_activation(
            activation(workspace_id, sequence=2, router_version_id=first.contract_id,
                       previous_version_id="rmv_earlier")
        )

    assert "is 0; §7.14 requires 1" in str(refusal.value)
    assert await store.list_router_activations(workspace_id=workspace_id) == []


async def test_an_entry_that_skips_a_number_is_refused_and_names_the_head() -> None:
    """The gap ``uq_router_activations_sequence`` cannot see (§7.14).

    A duplicate sequence is a database error. A *gap* satisfies every constraint on the
    table and destroys the one property that makes "the head" meaningful, so it is checked
    in Python on both backends and refused with the head's own number in the message.
    """

    store, _ledger, workspace_id = await setup_ledger()
    first = version(workspace_id)
    second = version(workspace_id)
    for record in (first, second):
        await store.put_router_model_version(record)
    await store.put_router_activation(
        activation(workspace_id, sequence=1, router_version_id=first.contract_id)
    )

    skipped = activation(
        workspace_id,
        sequence=3,
        router_version_id=second.contract_id,
        previous_version_id=first.contract_id,
    )
    with pytest.raises(ValueError) as refusal:
        await store.put_router_activation(skipped)

    assert str(refusal.value) == (
        f"activation {skipped.contract_id} has sequence 3; the head of "
        f"({workspace_id}, TEAM_WORKSPACE, {WORKSPACE_FAMILY_KEY}) is 1; §7.14 requires 2"
    )
    assert [entry.sequence for entry in
            await store.list_router_activations(workspace_id=workspace_id)] == [1]


async def test_a_later_entry_that_names_no_predecessor_is_refused() -> None:
    """The store half of "sequence 1 if and only if there is no predecessor".

    The contract already refuses sequence 1 *with* a predecessor — that half needs no store.
    The converse needs one: only the store knows the partition is not empty, and a later
    entry claiming to displace nothing would leave the version it actually displaced
    recorded nowhere.
    """

    store, _ledger, workspace_id = await setup_ledger()
    first = version(workspace_id)
    second = version(workspace_id)
    for record in (first, second):
        await store.put_router_model_version(record)
    await store.put_router_activation(
        activation(workspace_id, sequence=1, router_version_id=first.contract_id)
    )

    orphan = activation(workspace_id, sequence=2, router_version_id=second.contract_id)
    with pytest.raises(ValueError) as refusal:
        await store.put_router_activation(orphan)

    assert str(refusal.value) == (
        f"activation {orphan.contract_id} has sequence 2 and names no previous_version_id; "
        "§7.14 makes sequence 1 the only entry that displaces nothing"
    )
    assert len(await store.list_router_activations(workspace_id=workspace_id)) == 1


async def test_two_families_in_one_workspace_each_start_at_sequence_one(
    tmp_path: Path,
) -> None:
    """The partition is ``(workspace, scope, family_key)`` and not the workspace.

    A project adapter's first activation is sequence 1 even though the workspace prior is
    already at 1, because the two are separate sequences. Keyed on the workspace alone, the
    adapter would have had to claim sequence 2 and would then be describing the workspace
    prior as the thing it displaced.
    """

    store, _ledger, workspace_id, project_id = await setup_adapter_ledger(tmp_path)
    prior = version(workspace_id)
    adapter = version(
        workspace_id, scope=RouterScope.PROJECT_ADAPTER, project_id=project_id
    )
    for record in (prior, adapter):
        await store.put_router_model_version(record)

    await store.put_router_activation(
        activation(workspace_id, sequence=1, router_version_id=prior.contract_id)
    )
    await store.put_router_activation(
        activation(
            workspace_id,
            sequence=1,
            router_version_id=adapter.contract_id,
            scope=RouterScope.PROJECT_ADAPTER,
            project_id=project_id,
            family_key=adapter_family_key(project_id, ALGORITHM_ID),
        )
    )

    assert sorted(
        entry.family_key
        for entry in await store.list_router_activations(workspace_id=workspace_id)
    ) == sorted([WORKSPACE_FAMILY_KEY, adapter_family_key(project_id, ALGORITHM_ID)])


# -------------------------------------------------------------------- the head


async def test_an_unactivated_family_has_no_head() -> None:
    """Cold start is a state, not a failure: a workspace routes deterministically first."""

    store, ledger, workspace_id = await setup_ledger()

    assert (
        await ledger.head(
            workspace_id=workspace_id,
            scope=RouterScope.TEAM_WORKSPACE,
            family_key=WORKSPACE_FAMILY_KEY,
        )
        is None
    )


async def test_the_head_is_the_greatest_sequence_and_not_the_latest_written() -> None:
    """"Active" is a position in a sequence, not a timestamp.

    Written out of order on purpose. A head defined as "the most recently created row" would
    agree with this one on every ordinary ledger and disagree on exactly the ledger that
    matters — the one where a rollback landed while a promotion was in flight.
    """

    store, ledger, workspace_id = await setup_ledger()
    versions = [version(workspace_id) for _ in range(3)]
    for record in versions:
        await store.put_router_model_version(record)
    previous: str | None = None
    for index, record in enumerate(versions, start=1):
        await store.put_router_activation(
            activation(
                workspace_id,
                sequence=index,
                router_version_id=record.contract_id,
                previous_version_id=previous,
            )
        )
        previous = record.contract_id

    head = await ledger.head(
        workspace_id=workspace_id,
        scope=RouterScope.TEAM_WORKSPACE,
        family_key=WORKSPACE_FAMILY_KEY,
    )
    assert head is not None
    assert head.sequence == 3
    assert head.router_version_id == versions[-1].contract_id


async def test_one_workspaces_head_is_invisible_to_another() -> None:
    """Tenancy, at the read that decides who routes.

    A head that leaked across workspaces would not raise anything; it would quietly make one
    tenant's traffic run on another tenant's policy.
    """

    store, ledger, workspace_id = await setup_ledger()
    other_workspace = f"wks_{uuid4().hex[:12]}"
    ours = version(workspace_id)
    theirs = version(other_workspace)
    for record in (ours, theirs):
        await store.put_router_model_version(record)
    await store.put_router_activation(
        activation(workspace_id, sequence=1, router_version_id=ours.contract_id)
    )

    assert (
        await ledger.head(
            workspace_id=other_workspace,
            scope=RouterScope.TEAM_WORKSPACE,
            family_key=WORKSPACE_FAMILY_KEY,
        )
        is None
    )


# --------------------------------------------------------- the composite write


async def test_activate_writes_every_version_row_and_the_entry_together() -> None:
    """One act, and the store holds all of it or none of it (§10.3's "atomic").

    Read back from the store rather than from the returned object: a composite write that
    returned a well-formed activation without persisting the version rows would pass every
    assertion made on its return value.
    """

    store, ledger, workspace_id = await setup_ledger()
    promoted = version(workspace_id)
    retired = version(workspace_id, status=RouterStatus.RETIRED)

    entry = await ledger.activate(
        kind=RouterActivationKind.PROMOTE,
        version=promoted,
        previous=retired,
        rollback_target="rmv_TARGET0000000000000000000",
        promotion_report_id="rpr_REPORT0000000000000000000",
        approved_by=APPROVER,
        labels={"drill": "digest"},
    )

    stored = await store.get_router_activation(entry.contract_id)
    assert stored == entry
    assert stored is not None
    assert stored.sequence == 1
    assert stored.previous_version_id is None
    assert stored.approved_by == APPROVER
    assert stored.created_by == APPROVER
    assert stored.labels == {"drill": "digest"}
    assert sorted(
        version_row.contract_id
        for version_row in await store.list_router_model_versions(
            workspace_id=workspace_id
        )
    ) == sorted([promoted.contract_id, retired.contract_id])


async def test_the_second_activation_names_the_first_head_and_not_the_tombstone() -> None:
    """``previous_version_id`` is the version that was serving, not the row recording it.

    The tombstone a promotion writes for the displaced head is a *new* ``rmv_`` row with its
    own id. A ledger whose ``previous_version_id`` named the tombstone would chain to a
    record that was never active, and walking the history backwards would find a
    ``RETIRED`` row claiming to have been displaced by its own successor.
    """

    store, ledger, workspace_id = await setup_ledger()
    first = version(workspace_id)
    await ledger.activate(
        kind=RouterActivationKind.PROMOTE,
        version=first,
        approved_by=APPROVER,
        rollback_target=first.contract_id,
    )

    second = version(workspace_id)
    tombstone = version(workspace_id, status=RouterStatus.RETIRED)
    entry = await ledger.activate(
        kind=RouterActivationKind.PROMOTE,
        version=second,
        previous=tombstone,
        rollback_target=first.contract_id,
        approved_by=APPROVER,
    )

    stored = await store.get_router_activation(entry.contract_id)
    assert stored is not None
    assert stored.sequence == 2
    assert stored.previous_version_id == first.contract_id
    assert stored.previous_version_id != tombstone.contract_id


async def test_a_refused_activation_leaves_no_version_row_behind() -> None:
    """The composite write is all or nothing on ``MemoryStore`` too.

    The version rows are written before the ledger entry, so a contiguity refusal happens
    *after* two puts have already been made against the scoped copy. Publishing that copy
    anyway would leave two orphan versions and no record of why they exist, and every
    "which version is active" answer would still be correct — which is precisely how this
    would have gone unnoticed.
    """

    store, ledger, workspace_id = await setup_ledger()
    first = version(workspace_id)
    await ledger.activate(
        kind=RouterActivationKind.PROMOTE, version=first, approved_by=APPROVER
    )
    before = await store.list_router_model_versions(workspace_id=workspace_id)

    forged = activation(
        workspace_id,
        sequence=5,
        router_version_id=first.contract_id,
        previous_version_id=first.contract_id + "x",
    )
    orphan_a = version(workspace_id)
    orphan_b = version(workspace_id, status=RouterStatus.RETIRED)
    with pytest.raises(ValueError, match="§7.14 requires 2"):
        await store.activate_router_version(
            activation=forged, versions=[orphan_a, orphan_b]
        )

    assert await store.list_router_model_versions(workspace_id=workspace_id) == before
    assert [entry.sequence for entry in
            await store.list_router_activations(workspace_id=workspace_id)] == [1]


async def test_family_key_is_derived_from_the_version_and_not_supplied(
    tmp_path: Path,
) -> None:
    """A caller who could name the family separately could activate into the wrong one.

    The resulting ledger would be contiguous, unique, fully constrained and wrong: the
    workspace prior's sequence would be advanced by an entry naming a project adapter.
    """

    store, ledger, workspace_id, project_id = await setup_adapter_ledger(tmp_path)
    prior = version(workspace_id)
    adapter = version(
        workspace_id, scope=RouterScope.PROJECT_ADAPTER, project_id=project_id
    )

    assert family_key_for(prior) == WORKSPACE_FAMILY_KEY
    assert family_key_for(adapter) == f"{project_id}:{ALGORITHM_ID}"

    await ledger.activate(
        kind=RouterActivationKind.PROMOTE, version=prior, approved_by=APPROVER
    )
    entry = await ledger.activate(
        kind=RouterActivationKind.PROMOTE, version=adapter, approved_by=APPROVER
    )

    stored = await store.get_router_activation(entry.contract_id)
    assert stored is not None
    assert stored.family_key == f"{project_id}:{ALGORITHM_ID}"
    assert stored.sequence == 1
    assert stored.project_id == project_id


# ---------------------------------------------------------------- the resolver


async def test_a_workspace_with_no_promotion_resolves_to_the_deterministic_label() -> None:
    """Cold start, spelled the way a receipt has to spell it.

    ``router_label`` is never ``None``: a receipt sealed under a null label could not be told
    apart from one sealed before the field existed, and ``identity.py`` folds the label into
    a decision's identity.
    """

    store, _ledger, workspace_id = await setup_ledger()

    resolved = await LedgerActiveVersionResolver(store).resolve(
        workspace_id=workspace_id
    )

    assert resolved == LedgerActiveVersions(
        router_version_id=None,
        adapter_version_id=None,
        router_label=WORKSPACE_ROUTER_VERSION,
        adapter_label=None,
    )


async def test_the_resolver_returns_both_heads_and_labels_them(tmp_path: Path) -> None:
    """The workspace prior and the project adapter, in two single-row reads."""

    store, ledger, workspace_id, project_id = await setup_adapter_ledger(tmp_path)
    prior = version(workspace_id)
    adapter = version(
        workspace_id, scope=RouterScope.PROJECT_ADAPTER, project_id=project_id
    )
    for record in (prior, adapter):
        await ledger.activate(
            kind=RouterActivationKind.PROMOTE, version=record, approved_by=APPROVER
        )

    resolved = await LedgerActiveVersionResolver(store).resolve(
        workspace_id=workspace_id, project_id=project_id
    )

    assert resolved == LedgerActiveVersions(
        router_version_id=prior.contract_id,
        adapter_version_id=adapter.contract_id,
        router_label=prior.contract_id,
        adapter_label=adapter.contract_id,
    )


async def test_the_resolver_ignores_an_adapter_of_another_algorithm(
    tmp_path: Path,
) -> None:
    """``(project, algorithm)`` is the adapter family, so a different algorithm is a
    different sequence and not a competing head (§7.12, §13.1's fourth bullet).

    Two adapters fitted by different algorithms for one project are a comparison. A resolver
    that took "the project's latest adapter" would have picked whichever was promoted last
    and silently switched families.
    """

    store, ledger, workspace_id, project_id = await setup_adapter_ledger(tmp_path)
    other = version(
        workspace_id,
        scope=RouterScope.PROJECT_ADAPTER,
        project_id=project_id,
        algorithm_id="linear-thompson",
    )
    await ledger.activate(
        kind=RouterActivationKind.PROMOTE, version=other, approved_by=APPROVER
    )

    resolved = await LedgerActiveVersionResolver(store).resolve(
        workspace_id=workspace_id, project_id=project_id
    )
    assert resolved.adapter_version_id is None

    switched = await LedgerActiveVersionResolver(
        store, algorithm_id="linear-thompson"
    ).resolve(workspace_id=workspace_id, project_id=project_id)
    assert switched.adapter_version_id == other.contract_id


async def test_the_resolver_follows_the_head_after_a_second_promotion() -> None:
    """The read side tracks the write side, which is the whole point of the ledger."""

    store, ledger, workspace_id = await setup_ledger()
    first = version(workspace_id)
    second = version(workspace_id)
    await ledger.activate(
        kind=RouterActivationKind.PROMOTE, version=first, approved_by=APPROVER
    )
    await ledger.activate(
        kind=RouterActivationKind.PROMOTE,
        version=second,
        previous=version(workspace_id, status=RouterStatus.RETIRED),
        approved_by=APPROVER,
    )

    resolved = await LedgerActiveVersionResolver(store).resolve(
        workspace_id=workspace_id
    )
    assert resolved.router_version_id == second.contract_id
    assert resolved.router_label == second.contract_id


# ------------------------------------------------------------- migration shape


def test_migration_0019_declares_the_four_typed_globals_and_the_alembic_head() -> None:
    """The house shape 0013 and 0014 set, checked without a database.

    ``down_revision`` is ``0020_v04_experience_fk`` rather than ``0018_v04_freeze_delta``,
    because 0020 was the head when this landed; naming 0018 would have forked the history
    into two heads and ``alembic upgrade head`` would have refused to choose. This test is
    here rather than in ``test_v04_m8_migration.py`` so that it runs in the default suite:
    that module is skipped without ``ACCRETION_TEST_POSTGRES_URL``, and a structural check
    that only runs on the integration machine is a structural check nobody runs.
    """

    spec = importlib.util.spec_from_file_location("m8_migration_shape", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0019_v04_m8_activation"
    assert len(module.revision) <= 32
    assert module.down_revision == "0020_v04_experience_fk"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert [name for name, _columns, _where in module.M8_PARTIAL_INDEXES] == list(
        RETIRED_INDEXES
    )

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    for forbidden in ("create_table", "drop_table", "add_column", "drop_column"):
        assert forbidden not in source, forbidden
