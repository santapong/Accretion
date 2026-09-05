"""The v0.4 identity prefixes (ADR-055).

SDD v0.4 writes every identifier as ``uuid``. ADR-055 reads that as "globally unique opaque
id" and keeps the repository's prefixed base32 scheme, because a second identity scheme
would mean every join, every log line and every operator's eye had to know which kind of id
it was looking at. The cost of that decision is a namespace: three characters, shared with
sixty-odd existing kinds, and a collision would be silent — two kinds minting ids that
``has_prefix`` cannot tell apart, and a routing receipt that could be mistaken for a
connector definition.

So the first test here is the one ADR-055 actually asks for: every prefix is unique. The
rest check that the fourteen new kinds exist, that they mint ids of the shape the rest of
the repository expects, and that no v0.4 kind quietly took a prefix that was already spoken
for.
"""

from __future__ import annotations

import pytest

from accretion.contracts.routing import CONTRACT_INVENTORY
from accretion.ids import _PREFIXES, has_prefix, new_id

V04_PREFIXES: dict[str, str] = {
    "objective_contract": "obj",
    "node_contract": "nct",
    "verification_spec": "vsp",
    "routing_request": "rrq",
    "execution_configuration": "cfg",
    "configuration_candidate": "ccd",
    "compatibility_decision": "cmp",
    "routing_receipt": "rcp",
    "independent_verification_result": "ivr",
    "failure_event": "flr",
    "router_model_version": "rmv",
    "router_training_snapshot": "rts",
    "router_promotion_report": "rpr",
    "shadow_decision": "shd",
}
"""The exact fourteen ADR-055 names this milestone adds, transcribed from the decision.

Written out rather than derived from ``_PREFIXES`` on purpose: a test that read the table it
is checking would pass no matter what the table said.
"""


def test_every_id_prefix_in_the_registry_is_unique() -> None:
    """ADR-055's explicit requirement, over the whole table rather than the new rows.

    A duplicate anywhere breaks ``has_prefix`` for both kinds, so the assertion is global.
    The failure message lists the offending prefixes rather than just a count, because the
    only useful next question is "which two kinds".
    """

    seen: dict[str, list[str]] = {}
    for kind, prefix in _PREFIXES.items():
        seen.setdefault(prefix, []).append(kind)
    collisions = {prefix: sorted(kinds) for prefix, kinds in seen.items() if len(kinds) > 1}
    assert collisions == {}, f"prefixes shared by more than one kind: {collisions}"


def test_every_id_prefix_is_exactly_three_characters() -> None:
    """The width ``has_prefix`` assumes when it checks a total id length."""

    wrong = {kind: prefix for kind, prefix in _PREFIXES.items() if len(prefix) != 3}
    assert wrong == {}


def test_the_fourteen_v04_kinds_are_registered_with_the_prefixes_adr_055_names() -> None:
    for kind, prefix in V04_PREFIXES.items():
        assert kind in _PREFIXES, f"ADR-055 kind {kind!r} is missing from the prefix registry"
        assert _PREFIXES[kind] == prefix


def test_the_candidate_prefix_is_ccd_because_cnd_was_already_the_connector_definition() -> None:
    """The one prefix ADR-055 had to argue for, so the reason is pinned by a test."""

    assert _PREFIXES["configuration_candidate"] == "ccd"
    assert _PREFIXES["conndef"] == "cnd"


def test_the_experience_record_reuses_the_existing_experience_prefix() -> None:
    """ADR-054 (b): the projection is keyed by the P7 ``experience_id``, so it mints no new id."""

    assert _PREFIXES["experience"] == "exp"
    assert "experience_record" not in _PREFIXES


@pytest.mark.parametrize("kind", sorted(V04_PREFIXES))
def test_a_minted_v04_id_carries_its_prefix_and_the_repository_id_shape(kind: str) -> None:
    minted = new_id(kind)
    assert has_prefix(minted, kind)
    assert len(minted) == 30
    assert minted != new_id(kind)


def test_every_contract_that_declares_an_id_kind_names_a_registered_kind() -> None:
    """The link between the contract family and the prefix table, checked in that direction.

    A contract whose ``ID_KIND`` named an unregistered kind would raise ``KeyError`` deep
    inside ``has_prefix`` the first time anyone built one, which is a poor place to learn it.
    """

    for model in CONTRACT_INVENTORY:
        if model.ID_KIND is not None:
            assert model.ID_KIND in _PREFIXES, (
                f"{model.__name__}.ID_KIND is {model.ID_KIND!r}, which ids.py does not know"
            )


def test_no_two_contracts_share_an_id_kind() -> None:
    """Two contracts behind one prefix would make ``has_prefix`` unable to tell them apart."""

    kinds = [model.ID_KIND for model in CONTRACT_INVENTORY if model.ID_KIND is not None]
    assert len(kinds) == len(set(kinds))


def test_the_four_embedded_contracts_declare_no_id_kind() -> None:
    """ADR-055 mints no prefix for a value carried inside another contract.

    Named individually rather than counted, so that giving one of them an id space — or
    quietly dropping the prefix from a contract that should have one — is a red test.
    """

    without_prefix = sorted(
        model.__name__ for model in CONTRACT_INVENTORY if model.ID_KIND is None
    )
    assert without_prefix == [
        "ObjectiveContractRef",
        "ProjectFeatures",
        "StructuredExplanation",
        "TaskFeatures",
    ]
