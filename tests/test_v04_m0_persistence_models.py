"""The seventeen v0.4 tables, their constraints, migrations 0017/0018 and the freeze record.

These are static tests: no database, no store, no clock. Everything asserted here is a
property of the *declaration* — which tables exist, which columns they promote, which
constraints are named what, which way a foreign key deletes — because those are the
properties that a later milestone can break silently. A migration that quietly gained an
``add_column`` still runs; a foreign key that quietly became ``CASCADE`` still passes every
round-trip test in the repository right up until the day someone deletes a project and
takes the receipts with it.

The freeze record ``docs/releases/v0.4/m0-freeze.md`` is checked here too, and belongs here
rather than beside the schema tests, because what it records is a *persistence* mapping:
which committed schema is stored in which table under which migration revision. A freeze
artifact nobody verifies is a freeze artifact that drifts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Table

from accretion.contracts.canonical import CONTRACT_SCHEMA_VERSION
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
    RouterTrainingSnapshot,
    RoutingContext,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutResult,
    VerificationSpec,
)
from accretion.ids import _PREFIXES, has_prefix, new_id
from accretion.persistence.models import (
    V04_FREEZE_DELTA_TABLES,
    V04_M0_ROUTING_TABLES,
    Base,
    ExperienceRecordRow,
    RouterModelVersionRow,
    RoutingOverrideRow,
    RoutingReceiptRow,
    V04ContractRow,
)
from accretion.persistence.store import _build_routing_override_payload

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "versions" / "0017_v04_m0_routing_contracts.py"
DELTA_MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "0018_v04_freeze_delta_shadow_rollouts_router_activations.py"
)
# 0020 creates no table. It moves ``experience_records``' foreign key off the primary key
# so that revisions of one projection can coexist, which is why the "no other migration
# names a v0.4 table" rule below checks it rather than exempting it.
EXPERIENCE_FK_MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "0020_v04_experience_record_revisions.py"
)
FREEZE_PATH = ROOT / "docs" / "releases" / "v0.4" / "m0-freeze.md"
SCHEMA_ROOT = ROOT / "docs" / "contracts" / "v0.4"
MIGRATION_REVISION = "0017_v04_m0_routing_contracts"
DELTA_MIGRATION_REVISION = "0018_v04_freeze_delta"
MIGRATION_REVISIONS = frozenset({MIGRATION_REVISION, DELTA_MIGRATION_REVISION})

# SDD v0.4 §13's table, transcribed by hand from the document rather than imported from
# the code. Importing it would have made this test assert that the code equals itself.
#
# §13's own table lists **fourteen** and begins at `node_contracts`; `objective_contracts`
# is the fifteenth here because §7.1 requires the contract and ADR-058 counts the table.
# It leads the tuple because the tuple is also the migration's creation order, and the
# objective contract is the root the rest of the family references.
# The two the freeze delta of 5 Sep 2026 added (ADR-060, ADR-061). They are *not* in §13:
# §13's table was written before the branched-rollout and activation-ledger decisions, and
# the SDD now carries them in §7.13a, §7.14 and §13 instead. Transcribed here by hand for
# the same reason the §13 list is — so the assertion is against the document, not the code.
DELTA_TABLES = (
    "shadow_rollout_results",
    "router_activations",
)

SDD_13_TABLES = (
    "objective_contracts",
    "node_contracts",
    "verification_specs",
    "routing_requests",
    "configuration_candidates",
    "compatibility_decisions",
    "routing_receipts",
    "routing_overrides",
    "verification_results",
    "experience_records",
    "failure_events",
    "router_model_versions",
    "router_training_snapshots",
    "router_promotion_reports",
    "shadow_decisions",
)

# Every column the registry §3 header contributes to every one of the fifteen tables.
HEADER_COLUMNS = frozenset(
    {
        "id",
        "workspace_id",
        "project_id",
        "content_hash",
        "schema_version",
        "supersedes_contract_id",
        "payload",
        "created_at",
    }
)

# The one row in `docs/contracts/v0.4/` that is not a contract schema.
NON_SCHEMA_FILES = frozenset({"README.md"})

# `routing_overrides` is the §13 table PR2 froze no contract for, so its row shape is
# frozen by a committed golden document instead of by a JSON Schema. It is kept out of
# `tests/fixtures/contracts/v0.4/` on purpose: that tree holds one directory per frozen
# contract, and this record is not one.
GOLDEN_OVERRIDE_RELATIVE_PATH = "tests/fixtures/records/v0.4/routing_override/minimal.json"
GOLDEN_OVERRIDE_PATH = ROOT / GOLDEN_OVERRIDE_RELATIVE_PATH


def freeze_document_key_set() -> set[str]:
    """The frozen key set, read out of the freeze record's prose rather than hard-coded.

    Hard-coding it here would let the document and the test drift apart while both stayed
    green; reading it means the sentence a reviewer reads is the sentence under test.
    """

    section = freeze_section("### The frozen shape")
    start = section.index("Its key set is the frozen shape, in full:")
    listing = section[start:].split(":", 1)[1].split(".\n", 1)[0]
    return {token.strip(" \n`,") for token in listing.split(",")}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("m0_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_delta_migration() -> Any:
    spec = importlib.util.spec_from_file_location("delta_migration", DELTA_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze_section(heading: str) -> str:
    """The text of one ``##``/``###`` section of the freeze record.

    The record now carries two five-column tables — the committed schemas, and the one
    golden document that freezes ``routing_overrides``' shape — and a parser that scanned
    the whole file would fold them into each other, so that adding a row to one silently
    changed what the other's tests assert. Slicing by heading keeps each table answerable
    for itself.
    """

    text = FREEZE_PATH.read_text(encoding="utf-8")
    start = text.index(heading)
    remainder = text[start + len(heading) :]
    depth = heading.split(" ", 1)[0]
    ends = [
        index
        for marker in ("\n## ", "\n### ")
        # A deeper heading is part of this section; a same-or-shallower one ends it.
        if len(marker.strip()) <= len(depth)
        for index in [remainder.find(marker)]
        if index != -1
    ]
    return remainder[: min(ends)] if ends else remainder


def parse_freeze_table(section: str) -> dict[str, tuple[str, str, str, str]]:
    """``name -> (digest, version, stored_in, revision)`` for one five-column table.

    A hand-written parser and not a YAML block, because the freeze record has to be
    readable by a person reviewing the pull request; a machine-readable file nobody reads
    would document the freeze to nobody.
    """

    rows: dict[str, tuple[str, str, str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        name, digest, version, stored_in, revision = cells
        rows[name.strip("`")] = (
            digest.strip("`"),
            version.strip("`"),
            stored_in,
            revision.strip("`"),
        )
    return rows


def freeze_rows() -> dict[str, tuple[str, str, str, str]]:
    """The committed JSON Schemas the freeze record accounts for, delta applied.

    Two tables and not one, and the later one wins. "The frozen schemas" is the record of
    what M0 froze and is left at the bytes it was written with; "The frozen schemas, as
    amended" records the freeze delta of 5 Sep 2026 — the two contracts it added and the
    one whose digest it moved. Overlaying them is what lets the M0 table stay a historical
    record instead of being rewritten every time the surface changes, which is the exact
    failure a freeze record exists to prevent.
    """

    return {
        **parse_freeze_table(freeze_section("## The frozen schemas")),
        **parse_freeze_table(freeze_section("### The frozen schemas, as amended")),
    }


def frozen_document_rows() -> dict[str, tuple[str, str, str, str]]:
    """The golden documents the freeze record accounts for.

    Exactly one today: ``routing_overrides`` is the §13 table whose row shape lives in
    Python rather than in a committed schema, so its shape is frozen by a golden file and
    a digest instead.
    """

    return parse_freeze_table(freeze_section("### The frozen shape"))


# --------------------------------------------------------------------------- tables


def test_the_v04_tables_are_the_sdd_13_fifteen_plus_the_two_the_freeze_delta_added() -> None:
    assert V04_M0_ROUTING_TABLES == SDD_13_TABLES + DELTA_TABLES
    assert len(set(V04_M0_ROUTING_TABLES)) == 17
    # Fourteen of the first fifteen are §13's own list; the fifteenth is
    # `objective_contracts`, which §7.1 requires and ADR-058 counts. Stated as an assertion
    # so that "the fifteen §13 tables" cannot creep back into the prose as a claim about §13.
    assert len(set(SDD_13_TABLES) - {"objective_contracts"}) == 14
    # The delta's two come last, because the tuple is also the creation order and they are
    # created by 0018. Re-ordering it would move fifteen tables that are already in the field.
    assert V04_M0_ROUTING_TABLES[-2:] == DELTA_TABLES
    assert V04_FREEZE_DELTA_TABLES == DELTA_TABLES


def test_every_v04_table_exists_in_the_metadata_the_migration_builds_from() -> None:
    for name in V04_M0_ROUTING_TABLES:
        assert name in Base.metadata.tables


def test_the_header_columns_are_identical_on_all_seventeen_tables() -> None:
    for name in V04_M0_ROUTING_TABLES:
        columns = set(table(name).columns.keys())
        assert HEADER_COLUMNS <= columns, name


def test_no_v04_table_carries_an_updated_at_because_none_of_them_is_updatable() -> None:
    for name in V04_M0_ROUTING_TABLES:
        assert "updated_at" not in table(name).columns.keys(), name


def test_the_v04_tables_are_the_only_subclasses_of_the_shared_header_row() -> None:
    """An eighteenth subclass would be a v0.4 table nobody added to the list."""

    mapped = {
        subclass.__tablename__
        for subclass in V04ContractRow.__subclasses__()
        if hasattr(subclass, "__tablename__")
    }
    assert mapped == set(V04_M0_ROUTING_TABLES)


# Which frozen contract each table stores. Sixteen entries and not seventeen:
# `routing_overrides` is the §13 table PR2 froze no contract for (see m0-freeze.md).
TABLE_CONTRACTS = {
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

# The four columns that are deliberately not a top-level field of the contract they sit
# beside. Three flatten something the contract nests and are therefore spelled
# differently from any field. The fourth, ``experience_records.experience_id``, is
# derived rather than flattened: the sealed ``ExperienceRecord`` declares no field naming
# the P7 experience — it is keyed by it through ``contract_id`` — so migration 0020's
# separate foreign key, which is what lets revisions of one projection coexist, projects
# no field at all and is filled in at the store boundary. Each is named so that a fifth
# cannot appear by accident.
FLATTENED_COLUMNS = {
    "routing_requests": {"node_contract_id", "node_contract_hash"},
    "configuration_candidates": {"configuration_hash"},
    "experience_records": {"experience_id"},
}


@pytest.mark.parametrize("name", sorted(TABLE_CONTRACTS))
def test_every_promoted_column_projects_a_field_the_contract_declares(name: str) -> None:
    """A promoted column is a projection of the payload, never a second source of truth.

    Checked by name: a column called ``status`` on a table whose contract calls the field
    ``state`` is a column that can disagree with the payload it claims to summarise, and
    nothing else in the system would ever notice. The two flattened cases are enumerated
    above, so adding a third is a decision somebody has to write down.
    """

    promoted = set(table(name).columns.keys()) - HEADER_COLUMNS - FLATTENED_COLUMNS.get(
        name, set()
    )
    assert promoted, name
    assert promoted <= set(TABLE_CONTRACTS[name].model_fields), name


def test_the_one_table_with_no_frozen_contract_is_routing_overrides() -> None:
    """ADR-055 mints no override prefix and PR2 froze no override model; §13 names the table.

    Recorded as an assertion rather than a comment so that M2, which owns the override
    endpoint, cannot add the contract without this test turning red and forcing the
    mapping above to be completed.
    """

    assert set(V04_M0_ROUTING_TABLES) - set(TABLE_CONTRACTS) == {"routing_overrides"}


def test_the_experience_record_is_keyed_by_the_p7_experience_row_it_projects() -> None:
    """ADR-054 b: one experience id, two tables, no copied fields.

    The key is on ``experience_id`` and no longer on ``id`` (migration 0020). It is still
    exactly one key into ``experiences`` and still the only thing this table says about the
    P7 record; what changed is that a projection may now have more than one row, so the
    column that names the experience cannot also be the column that tells the rows apart.
    """

    keys = {(fk.parent.name, fk.column.table.name, fk.column.name) for fk in
            ExperienceRecordRow.__table__.foreign_keys}
    assert ("experience_id", "experiences", "id") in keys
    # And the primary key is free to be a revision's own derived id.
    assert not [key for key in keys if key[0] == "id"]
    # Nothing from the P7 Experience is restated here.
    duplicated = {"repository_identity", "trust", "polarity", "task_id", "source_run_id"}
    assert duplicated.isdisjoint(ExperienceRecordRow.__table__.columns.keys())


# ---------------------------------------------------------------------- constraints


@pytest.mark.parametrize("name", V04_M0_ROUTING_TABLES)
def test_every_v04_table_makes_the_hash_and_version_tuple_unique(name: str) -> None:
    """§13.1: "Contract hash/version tuples are unique.""" ""

    tuples = {
        tuple(column.name for column in constraint.columns)
        for constraint in table(name).constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("content_hash", "schema_version") in tuples, name


def test_the_hash_uniqueness_constraints_are_named_one_per_table() -> None:
    names = set()
    for name in V04_M0_ROUTING_TABLES:
        for constraint in table(name).constraints:
            if constraint.__class__.__name__ != "UniqueConstraint":
                continue
            if tuple(column.name for column in constraint.columns) == (
                "content_hash",
                "schema_version",
            ):
                assert constraint.name is not None
                assert constraint.name.startswith("uq_")
                assert constraint.name.endswith("_hash_version")
                names.add(constraint.name)
    assert len(names) == 17


def test_one_immutable_receipt_per_routing_request_id() -> None:
    """§13.1, second bullet; §8.2's idempotency key as a database constraint."""

    assert RoutingReceiptRow.__table__.columns["routing_request_id"].unique is True


def test_the_two_partial_unique_indexes_exist_and_say_what_they_mean() -> None:
    """§13.1's third and fourth bullets; the repository's first partial unique indexes."""

    indexes = {index.name: index for index in RouterModelVersionRow.__table__.indexes}

    workspace = indexes["uq_router_versions_active_workspace"]
    assert workspace.unique is True
    assert [column.name for column in workspace.columns] == ["workspace_id"]
    clause = str(workspace.dialect_options["postgresql"]["where"])
    assert "ACTIVE" in clause and "TEAM_WORKSPACE" in clause

    adapter = indexes["uq_router_versions_active_project_adapter"]
    assert adapter.unique is True
    assert [column.name for column in adapter.columns] == ["project_id", "algorithm_id"]
    clause = str(adapter.dialect_options["postgresql"]["where"])
    assert "ACTIVE" in clause and "PROJECT_ADAPTER" in clause


def test_no_other_v04_table_declares_a_partial_unique_index() -> None:
    """Two, and only the two §13.1 names. A third would be an unreviewed rule."""

    partial = {
        index.name
        for name in V04_M0_ROUTING_TABLES
        for index in table(name).indexes
        if index.dialect_options["postgresql"].get("where") is not None
    }
    assert partial == {
        "uq_router_versions_active_workspace",
        "uq_router_versions_active_project_adapter",
    }


def test_every_index_on_the_v04_tables_follows_the_naming_convention() -> None:
    for name in V04_M0_ROUTING_TABLES:
        for index in table(name).indexes:
            assert index.name is not None
            assert index.name.startswith(("ix_", "uq_")), (name, index.name)


def test_no_foreign_key_out_of_the_v04_tables_cascades() -> None:
    """§13.1's last bullet and registry §16: nothing cascades into provenance.

    A delete that reaches a receipt, an evidence reference or a promotion report through
    a cascade is a delete nobody asked for and nobody sees. ``RESTRICT`` makes the
    retention path say out loud what it is removing.
    """

    seen = 0
    for name in V04_M0_ROUTING_TABLES:
        for key in table(name).foreign_keys:
            seen += 1
            assert key.ondelete == "RESTRICT", (name, key.parent.name, key.ondelete)
            assert key.onupdate is None, (name, key.parent.name)
    assert seen >= len(V04_M0_ROUTING_TABLES)


def test_the_v04_tables_reference_only_projects_and_experiences() -> None:
    targets = {
        key.column.table.name
        for name in V04_M0_ROUTING_TABLES
        for key in table(name).foreign_keys
    }
    assert targets == {"projects", "experiences"}


# ----------------------------------------------------------------------- migration


def test_the_m0_migration_follows_0016_and_drops_exactly_what_it_creates() -> None:
    module = load_migration()

    assert module.revision == MIGRATION_REVISION
    assert module.down_revision == "0016_v03_m7_enterprise_auth"
    for name in module.M0_TABLES:
        assert name in Base.metadata.tables

    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert "checkfirst=True" in upgrade_source
    assert "reversed(M0_TABLES)" in downgrade_source
    # Additive only: no existing table is altered in either direction.
    for source in (upgrade_source, downgrade_source):
        assert "add_column" not in source
        assert "drop_column" not in source
        assert "alter_column" not in source
        assert "execute" not in source


def test_0017_still_creates_exactly_the_fifteen_it_created_before_the_freeze_delta() -> None:
    """A migration already applied in the field cannot grow two tables retroactively.

    ``V04_M0_ROUTING_TABLES`` is seventeen names now, and 0017 reads it — so without the
    subtraction this revision would start creating the delta's two tables, and a database
    stamped 0017 would no longer be reproducible from the revision it records. Asserted as
    a set difference rather than as a hard-coded list so that the next table added to the
    family cannot pass by being appended in the right place by luck.
    """

    module = load_migration()

    assert set(module.M0_TABLES) == set(V04_M0_ROUTING_TABLES) - set(DELTA_TABLES)
    assert len(module.M0_TABLES) == 15
    assert set(module.M0_TABLES).isdisjoint(DELTA_TABLES)


def test_the_freeze_delta_migration_follows_0017_and_creates_only_its_own_two() -> None:
    """ADR-060/061's migration: additive, reversible, and it leaves 0017's indexes alone.

    The last assertion is the one that matters for M8.1: retiring the two partial unique
    indexes belongs to 0019, and a database sitting between the two revisions must satisfy
    both the old rule and the new one, which is only true while this migration does not
    touch ``router_model_versions`` at all.
    """

    module = load_delta_migration()

    assert module.revision == DELTA_MIGRATION_REVISION
    assert module.down_revision == MIGRATION_REVISION
    # Alembic stores the id in a ``VARCHAR(32)`` and 0018's file stem is longer than that,
    # which is why its revision id is shorter than its file name. Every revision in the
    # tree has to fit, or the stamp fails at the driver with a truncation error that names
    # no migration at all.
    for path in sorted((ROOT / "migrations" / "versions").glob("0*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("revision: str = "):
                assert len(line.split('"')[1]) <= 32, path.name
    assert module.FREEZE_DELTA_TABLES == DELTA_TABLES
    for name in module.FREEZE_DELTA_TABLES:
        assert name in Base.metadata.tables

    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert "checkfirst=True" in upgrade_source
    assert "reversed(FREEZE_DELTA_TABLES)" in downgrade_source
    for source in (upgrade_source, downgrade_source):
        assert "add_column" not in source
        assert "drop_column" not in source
        assert "alter_column" not in source
        assert "execute" not in source
        assert "drop_index" not in source
        assert "router_model_versions" not in source


def test_no_other_migration_names_a_v04_table() -> None:
    """No migration outside the two may create one of these under a different shape.

    0020 is the single exception, and it is *checked* rather than exempted: it names
    exactly one of these tables — ``experience_records``, whose foreign key it moves from
    the primary key onto its own column — and creates none of them. A migration that
    created a v0.4 table outside 0017 and 0018 would be a second definition of a table
    that already has one, and the two would drift; a migration that alters one is a
    different thing and has to say which table and prove it builds nothing.
    """

    versions = ROOT / "migrations" / "versions"
    owners = {MIGRATION_PATH.name, DELTA_MIGRATION_PATH.name}
    for path in sorted(versions.glob("0*.py")):
        if path.name in owners:
            continue
        text = path.read_text(encoding="utf-8")
        named = {name for name in V04_M0_ROUTING_TABLES if f'"{name}"' in text}
        if path.name == EXPERIENCE_FK_MIGRATION_PATH.name:
            assert named == {"experience_records"}, path.name
            assert "create_table" not in text, path.name
            assert ".create(bind" not in text, path.name
            continue
        assert not named, (path.name, sorted(named))


def test_the_migration_docstrings_name_every_table_and_every_constraint() -> None:
    doc = (load_migration().__doc__ or "") + (load_delta_migration().__doc__ or "")

    for name in V04_M0_ROUTING_TABLES:
        assert f"``{name}``" in doc, name
    assert "uq_router_versions_active_workspace" in doc
    assert "uq_router_versions_active_project_adapter" in doc
    assert "uq_router_activations_sequence" in doc
    assert "RESTRICT" in doc
    assert "append-only" in doc


# -------------------------------------------------------------------- freeze record


def test_the_freeze_record_covers_every_committed_schema_and_nothing_else() -> None:
    committed = {
        path.name for path in SCHEMA_ROOT.iterdir() if path.name not in NON_SCHEMA_FILES
    }
    assert committed == set(freeze_rows())
    assert len(committed) == len(CONTRACT_INVENTORY)


def test_the_freeze_record_digests_equal_the_committed_schema_files() -> None:
    """The freeze artifact cannot drift silently.

    A digest recorded in prose and never checked is a digest that is right on the day it
    is written and wrong from the first regeneration onwards. This is the check that makes
    ``m0-freeze.md`` evidence rather than decoration.
    """

    for filename, (digest, _version, _stored_in, _revision) in sorted(freeze_rows().items()):
        actual = hashlib.sha256((SCHEMA_ROOT / filename).read_bytes()).hexdigest()
        assert actual == digest, (
            f"{filename} has digest {actual}, but docs/releases/v0.4/m0-freeze.md records "
            f"{digest}; regenerate the schemas or correct the freeze record"
        )


def test_the_freeze_record_states_the_schema_version_every_contract_declares() -> None:
    for _filename, (_digest, version, _stored_in, _revision) in freeze_rows().items():
        assert version == CONTRACT_SCHEMA_VERSION


def test_the_freeze_record_maps_each_schema_to_a_real_table_or_says_not_persisted() -> None:
    mapped: set[str] = set()
    for filename, (_digest, _version, stored_in, revision) in freeze_rows().items():
        assert revision in MIGRATION_REVISIONS, filename
        if stored_in.startswith("not persisted"):
            continue
        name = stored_in.strip("`").split("`")[0]
        assert name in V04_M0_ROUTING_TABLES, (filename, name)
        mapped.add(name)
    # Sixteen of the seventeen tables store a frozen contract; `routing_overrides` is the
    # one that stores a canonical document with no model behind it (SDD §13, ADR-055).
    assert set(V04_M0_ROUTING_TABLES) - mapped == {"routing_overrides"}


def test_every_contract_in_the_inventory_appears_in_the_freeze_record() -> None:
    rows = freeze_rows()
    for model in CONTRACT_INVENTORY:
        assert f"{model.__name__}.schema.json" in rows, model.__name__


# ------------------------------------------------------- the fifteenth table's shape


def test_every_v04_table_has_a_frozen_shape() -> None:
    """table -> frozen shape, the direction nothing else in this file walks.

    The three tests above walk schema -> digest, schema -> table and inventory -> schema.
    All three are satisfied by a table that appears in no row at all, which is how
    ``routing_overrides`` — the one §13 table whose row shape lives in mutable Python
    rather than in a committed schema file — could have had its shape changed by an edit to
    ``_build_routing_override_payload`` with no red test anywhere. That is precisely the
    drift the freeze record's own opening paragraph says it exists to prevent, so the
    missing direction is walked here: every one of the seventeen tables is named in a
    "Stored in" column somewhere on the page.
    """

    stored_in: set[str] = set()
    for rows in (freeze_rows(), frozen_document_rows()):
        for _name, (_digest, _version, cell, _revision) in rows.items():
            if cell.startswith("not persisted"):
                continue
            stored_in.add(cell.strip("`").split("`")[0])

    assert set(V04_M0_ROUTING_TABLES) - stored_in == set(), (
        "a v0.4 table whose shape docs/releases/v0.4/m0-freeze.md does not freeze"
    )
    assert stored_in <= set(V04_M0_ROUTING_TABLES)


def test_the_frozen_routing_override_document_matches_its_recorded_digest() -> None:
    """The golden document is evidence only while its digest is checked, like the schemas."""

    rows = frozen_document_rows()
    assert set(rows) == {GOLDEN_OVERRIDE_RELATIVE_PATH}

    digest, version, stored_in, revision = rows[GOLDEN_OVERRIDE_RELATIVE_PATH]
    actual = hashlib.sha256(GOLDEN_OVERRIDE_PATH.read_bytes()).hexdigest()
    assert actual == digest, (
        f"{GOLDEN_OVERRIDE_RELATIVE_PATH} has digest {actual}, but "
        f"docs/releases/v0.4/m0-freeze.md records {digest}"
    )
    assert version == CONTRACT_SCHEMA_VERSION
    assert stored_in.strip("`") == "routing_overrides"
    assert revision == MIGRATION_REVISION


def test_a_freshly_built_routing_override_has_exactly_the_frozen_key_set() -> None:
    """A change to the builder is a red test, not a shape that quietly forks.

    Renaming ``reason`` to ``note``, dropping ``superseding_receipt_id`` or adding a field
    changes the shape *and the digest* of every row written afterwards, and the rows
    already in the table can never be reproduced from the new builder. The key list in the
    freeze record is the frozen shape; this compares it against both the committed golden
    file and a document the live builder produces right now, so neither can move alone.
    """

    golden = json.loads(GOLDEN_OVERRIDE_PATH.read_text(encoding="utf-8"))
    built = _build_routing_override_payload(
        override_id="rov_8F8D7PY3E21ZEG1XTT0P2TW7X3",
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

    recorded = freeze_document_key_set()
    assert set(golden) == recorded
    assert set(built) == recorded
    # Same arguments, pinned clock: the same bytes, therefore the same digest.
    assert built == golden


def test_the_golden_routing_override_lives_outside_the_frozen_contract_fixture_tree() -> None:
    """It is not a contract, and the tree it is kept out of holds only contracts.

    ``tests/test_v04_m0_fixtures.py`` asserts that ``tests/fixtures/contracts/v0.4/`` holds
    exactly one directory per entry in ``CONTRACT_INVENTORY``. Filing a pre-contract record
    there would re-assert the very claim ``ROUTING_OVERRIDE_DOCUMENT_TYPE`` withdraws.
    """

    assert GOLDEN_OVERRIDE_PATH.exists()
    assert SCHEMA_ROOT not in GOLDEN_OVERRIDE_PATH.parents
    assert (ROOT / "tests" / "fixtures" / "contracts" / "v0.4") not in (
        GOLDEN_OVERRIDE_PATH.parents
    )
    assert not (SCHEMA_ROOT / "RoutingOverride.schema.json").exists()


# ------------------------------------------------------------- identity, ADR-055


def test_the_freeze_record_tells_the_truth_about_the_routing_override_id_kind() -> None:
    """The freeze record's identity claims, tied to ``accretion.ids`` so they cannot rot.

    An earlier draft of this page and of ``RoutingOverrideRow``'s docstring both said
    ADR-055 mints no prefix for the routing override. ``ids.py`` had mapped
    ``override -> ovr`` since v0.1, and ``planning.py`` mints it for the *strategy*
    override, so the sentence was false and the table's ids were being minted from another
    record class's kind. The correction is a distinct kind; this test is what stops the
    sentence surviving being false a second time.
    """

    assert _PREFIXES["override"] == "ovr"
    assert _PREFIXES["routing_override"] == "rov"

    freeze = FREEZE_PATH.read_text(encoding="utf-8")
    assert '`"override" -> "ovr"`' in freeze
    assert '`"routing_override" -> "rov"`' in freeze
    assert "ADR-055 mints no id prefix for it" not in freeze

    override_doc = RoutingOverrideRow.__doc__ or ""
    assert "ADR-055 mints no override-class prefix" not in override_doc
    assert '``"routing_override" -> "rov"``' in override_doc


def test_the_freeze_record_calls_the_m2_routing_override_a_major_change() -> None:
    """The classification a later milestone will cite, checked rather than trusted.

    An earlier draft of this page recorded M2's ``RoutingOverride`` as "an additive Minor
    change under registry §3.2". Replacing ``document_type`` with ``contract_type`` and
    adding a required header field to rows already written under the same
    ``schema_version`` is Major and fail-closed by the registry's own rule, and the freeze
    record is the artifact M1 and M2 will cite for the answer. The discrimination rule has
    to be on the page too, because ``schema_version`` cannot tell the two shapes apart:
    both stamp ``CONTRACT_SCHEMA_VERSION``.

    ``tests/test_v04_m0_store.py::test_an_m0_override_record_does_not_validate_as_a_contract``
    is the executable half; this is the half that keeps the sentence from drifting back.
    """

    freeze = FREEZE_PATH.read_text(encoding="utf-8")

    assert "arrives as an additive Minor change under registry §3.2" not in freeze
    assert "Major and fail-closed" in freeze
    assert "`document_type`" in freeze and "`contract_type`" in freeze
    assert "must never be fed to a contract's `model_validate`" in freeze


def test_the_strategy_override_kind_is_still_the_one_planning_mints() -> None:
    """The new kind is additive: nothing that already minted ``ovr_`` changed meaning."""

    assert has_prefix(new_id("override"), "override")
    assert new_id("override").startswith("ovr_")
    assert new_id("routing_override").startswith("rov_")
    assert not has_prefix(new_id("routing_override"), "override")

