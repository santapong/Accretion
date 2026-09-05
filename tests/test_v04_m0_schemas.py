"""The committed JSON Schema export for the v0.4 contract family (registry §19, §20.1).

Registry §20 decision 1 fixes the schema language as JSON Schema 2020-12, and §19 gates a
contract release on machine-readable validation existing for every contract. The schemas
live under ``docs/contracts/v0.4/`` and are committed, so these tests do the one thing that
makes a committed artifact worth committing: regenerate in memory and compare, per model.

Per model, and not as one bulk comparison. A single "all schemas match" assertion would say
"something changed" and leave the reader to find out what; parametrizing over
``CONTRACT_INVENTORY`` means the failure names the contract, which is the first thing anyone
needs to know and usually the last.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import CONTRACT_INVENTORY

# The export script is not an importable package — it is a script, and the repository keeps
# scripts out of `src/` on purpose. Importing it here rather than reimplementing `render`
# means the tests compare against the exact bytes the script writes, which is the only
# comparison worth making: a test with its own renderer would only prove the two renderers
# agree with each other.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_contract_schemas import (  # noqa: E402
    JSON_SCHEMA_DIALECT,
    SCHEMA_ROOT,
    check_all,
    render,
    schema_path,
)

IDS = [model.__name__ for model in CONTRACT_INVENTORY]


def body_of(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the object schema for the contract itself, following a top-level ``$ref``.

    Eighteen of the nineteen schemas describe the contract inline. ``ObjectiveContractRef``
    does not: it is the type of the header's own ``objective_contract_ref`` field, so it
    refers to itself, and pydantic hoists a self-referential model into ``$defs`` and leaves
    a ``$ref`` at the root. Both shapes are valid 2020-12; the tests below care about the
    contract's properties, so the reference is resolved here once instead of every test
    growing a special case.
    """

    reference = schema.get("$ref")
    if reference is None:
        return schema
    name = reference.removeprefix("#/$defs/")
    resolved: dict[str, Any] = schema["$defs"][name]
    return resolved


def load(model: type[CanonicalContract]) -> dict[str, Any]:
    """The committed schema for ``model``, parsed."""

    parsed: dict[str, Any] = json.loads(schema_path(model).read_text(encoding="utf-8"))
    return parsed


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_committed_schema_equals_the_one_the_model_generates_now(
    model: type[CanonicalContract],
) -> None:
    """The whole point of committing them. Byte comparison, not structural."""

    target = schema_path(model)
    assert target.exists(), f"{target} is missing; run scripts/export_contract_schemas.py"
    assert target.read_text(encoding="utf-8") == render(model), (
        f"the committed schema for {model.__name__} is stale; "
        "run scripts/export_contract_schemas.py"
    )


def test_the_check_mode_of_the_export_script_agrees_with_the_committed_files() -> None:
    """``--check`` is what CI runs, so the suite exercises the same code path CI does."""

    assert check_all() is None


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_schema_declares_the_2020_12_dialect(model: type[CanonicalContract]) -> None:
    """A schema that does not name its dialect is one a validator has to guess at."""

    assert load(model)["$schema"] == JSON_SCHEMA_DIALECT


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_schema_carries_the_registry_header_and_forbids_unknown_fields(
    model: type[CanonicalContract],
) -> None:
    """Registry §3's header, checked in the published artifact rather than only in Python.

    ``additionalProperties: false`` is the schema half of ADR-057's ``extra="forbid"``: until
    M8 introduces a second writer and read-boundary upcasting, an unknown field is an error
    and the published schema has to say so, or an external producer would be told otherwise.
    """

    schema = body_of(load(model))
    properties = schema["properties"]
    for field in (
        "contract_type",
        "schema_version",
        "contract_id",
        "content_hash",
        "created_at",
        "created_by",
        "workspace_id",
        "project_id",
        "supersedes_contract_id",
        "objective_contract_ref",
        "labels",
        "retention_class",
    ):
        assert field in properties, f"{model.__name__} schema is missing header field {field}"
    assert schema["additionalProperties"] is False
    assert properties["contract_type"]["const"] == model.CONTRACT_TYPE


def test_the_schema_directory_holds_exactly_one_file_per_contract_and_the_read_me() -> None:
    """An orphan left by a rename is as much a defect as a missing file."""

    committed = sorted(path.name for path in SCHEMA_ROOT.glob("*.schema.json"))
    assert committed == sorted(f"{name}.schema.json" for name in IDS)
    assert (SCHEMA_ROOT / "README.md").exists()


def test_regenerating_a_schema_twice_produces_the_same_bytes() -> None:
    """Determinism, asserted rather than assumed: without it ``--check`` would be a coin toss."""

    for model in CONTRACT_INVENTORY:
        assert render(model) == render(model)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_schema_ends_with_a_single_trailing_newline(
    model: type[CanonicalContract],
) -> None:
    text = schema_path(model).read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
