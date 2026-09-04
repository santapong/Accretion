"""Export and check the committed JSON Schema for every v0.4 contract (registry §20 decision 1).

Registry §20 settles the schema language as "JSON Schema 2020-12 plus generated
Python/TypeScript types", and registry §19 gates a contract release on "JSON Schema or
equivalent machine-readable validation exists". The pydantic models are the source of
truth; this script is how that truth leaves Python.

The exported files are **committed**, and that is the point of the exercise. A schema that
lived only in memory would document nothing: it could not be diffed in review, it could not
be handed to the TypeScript twin the M9 Studio work owes, and a field quietly renamed in a
refactor would leave no trace. Committing them makes a schema change a reviewable line in a
pull request, and ``--check`` makes forgetting to regenerate a failure rather than a
surprise.

Determinism is doing real work here. The files are written with sorted keys, two-space
indentation and a trailing newline, so that regenerating produces byte-identical output on
any machine and ``git diff --exit-code`` means what it says. ``$schema`` is written
explicitly at the top of every file: pydantic emits 2020-12-shaped output but does not
declare the dialect, and a schema that does not say which dialect it is written in is a
schema a validator has to guess at.

Usage::

    uv run --no-sync python scripts/export_contract_schemas.py           # write
    uv run --no-sync python scripts/export_contract_schemas.py --check   # verify

``--check`` exits non-zero naming the **first** model whose committed file differs, together
with whether the file is missing, extra, or merely stale. One name and one reason is more
useful than nineteen diffs, because the fix is always the same command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import CONTRACT_INVENTORY

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "v0.4"

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
"""Registry §20 decision 1. Declared explicitly because pydantic does not declare it."""


def schema_for(model: type[CanonicalContract]) -> dict[str, Any]:
    """The 2020-12 schema for one contract, dialect-declared and deterministic.

    ``mode="validation"`` and not ``"serialization"``: the schema describes what a *reader*
    must accept, which is the question every consumer of these files is asking. The two
    differ wherever a field is computed — ``content_hash`` is optional on input and always
    present on output — and publishing the serialization view would tell an external writer
    it must supply a digest it cannot compute.
    """

    schema = model.model_json_schema(mode="validation")
    return {"$schema": JSON_SCHEMA_DIALECT, **schema}


def render(model: type[CanonicalContract]) -> str:
    """The exact bytes committed for ``model``: sorted keys, indent 2, trailing newline."""

    return json.dumps(schema_for(model), indent=2, sort_keys=True) + "\n"


def schema_path(model: type[CanonicalContract]) -> Path:
    """``docs/contracts/v0.4/<Model>.schema.json`` — the class name, not the module path."""

    return SCHEMA_ROOT / f"{model.__name__}.schema.json"


def expected_filenames() -> set[str]:
    """Every filename the inventory owns, so an orphan left by a rename is detected."""

    return {schema_path(model).name for model in CONTRACT_INVENTORY}


def write_all() -> int:
    """Write every schema. Returns the number of files written."""

    SCHEMA_ROOT.mkdir(parents=True, exist_ok=True)
    for model in CONTRACT_INVENTORY:
        schema_path(model).write_text(render(model), encoding="utf-8")
    return len(CONTRACT_INVENTORY)


def check_all() -> str | None:
    """Return a message naming the first disagreement, or ``None`` if everything matches."""

    for model in CONTRACT_INVENTORY:
        target = schema_path(model)
        if not target.exists():
            return (
                f"{model.__name__}: {target.relative_to(SCHEMA_ROOT.parents[2])} is missing; "
                "run scripts/export_contract_schemas.py"
            )
        if target.read_text(encoding="utf-8") != render(model):
            return (
                f"{model.__name__}: the committed schema differs from the model; "
                "run scripts/export_contract_schemas.py"
            )
    if SCHEMA_ROOT.exists():
        committed = {path.name for path in SCHEMA_ROOT.glob("*.schema.json")}
        orphans = sorted(committed - expected_filenames())
        if orphans:
            return (
                f"{orphans[0]}: a committed schema belongs to no contract in "
                "CONTRACT_INVENTORY; delete it or restore the contract"
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed schemas instead of writing them; exit 1 on a difference",
    )
    arguments = parser.parse_args(argv)

    if arguments.check:
        problem = check_all()
        if problem is not None:
            print(problem, file=sys.stderr)
            return 1
        print(f"{len(CONTRACT_INVENTORY)} committed contract schemas match their models")
        return 0

    written = write_all()
    print(f"wrote {written} contract schemas to {SCHEMA_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
