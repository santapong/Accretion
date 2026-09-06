"""Protocol §14's required ablations, as ten switches a benchmark run can actually flip.

The research protocol lists ten ablations in a table (§14, A1 through A10). A table is a
promise; this module is the part that makes it a fact. Every row is a registered entry in
``evals/router/ablations.v1.json`` naming the component it removes, the *one* flag that
removes it and the question §14 says the removal answers, and every entry resolves to a
:class:`RouterFeatureFlags` the router benchmark runs under.

**One flag per ablation, and every flag spoken for.** :func:`load_ablations` refuses a
registry whose flags are not a bijection onto :data:`FLAG_NAMES`. That single rule catches
the three ways this file could quietly stop meaning anything: an ablation deleted from the
JSON (nine entries for ten flags), an ablation that clears a flag another one already
clears (two entries answering one question), and a flag nobody ablates (a component the
protocol requires an answer about and nobody asked). None of the three is visible by
reading a run's output, and all of them make the ablation table an assertion about work
that was not done.

**The flags say what was removed, not how.** A flag is read in three different places —
:mod:`accretion.routing.candidates` builds a different slate, :mod:`accretion.routing.
baselines` trains and ranks differently, :mod:`accretion.routing.service` records a
different receipt — because §14's components genuinely live in three layers. What the flag
guarantees is only that the component is gone in the run that clears it, and that
:data:`FULL` leaves every one of them in place.

**``FULL`` is the identity.** Every default is ``True``, so a caller that passes nothing
gets the shipped router. That is the property the whole design rests on: the ablations are
a change to the *comparison*, never to the production default, and a flag whose off-state
leaked into the default would make every earlier receipt in the store the output of a
router nobody reviewed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

ABLATIONS_PATH = (
    Path(__file__).resolve().parents[3] / "evals" / "router" / "ablations.v1.json"
)
"""Where the registered §14 table lives, beside the router benchmark corpus it is run over."""


FLAG_NAMES: tuple[str, ...] = (
    "hierarchical_construction",
    "compatibility_pruning",
    "experience_retrieval",
    "project_adapter",
    "node_feedback",
    "final_run_feedback",
    "guarded_exploration",
    "independent_verification",
    "uncertainty_gate",
    "evi_recovery_stop",
)
"""The ten switchable components, in protocol §14's own A1..A10 order.

A tuple and not a set: the order is the protocol's, the registry is checked against it, and
a report that listed the components in a hash order would be a different table from §14's
every time the interpreter changed."""


ABLATION_LABEL = "ablation"
"""The label key carrying which protocol §14 row a decision was made under."""

ABLATION_LABEL_PREFIX = "ablation."
"""The prefix under which each removed component is named on a receipt."""

REMOVED = "REMOVED"
"""The one value an ``ablation.<component>`` label ever takes. Present means absent."""


class AblationError(ValueError):
    """Base class for every refusal this module makes about the §14 registry."""


class UnknownAblation(AblationError):
    """An ablation id the registered §14 table does not name."""


class AblationRegistryError(AblationError):
    """The registry on disk is not a §14 table: a missing, doubled or unknown flag."""


@dataclass(frozen=True, slots=True)
class RouterFeatureFlags:
    """Which of §14's ten components are present in one run.

    ``ablation_id`` is not an eleventh component; it is the *name* of the configuration, so
    that a benchmark row can say which ablation produced it without the reader having to
    diff ten booleans against :data:`FULL` to find out. :data:`FULL` carries ``None``,
    because "no ablation" is not an ablation with an empty name.
    """

    hierarchical_construction: bool = True
    compatibility_pruning: bool = True
    experience_retrieval: bool = True
    project_adapter: bool = True
    node_feedback: bool = True
    final_run_feedback: bool = True
    guarded_exploration: bool = True
    independent_verification: bool = True
    uncertainty_gate: bool = True
    evi_recovery_stop: bool = True
    ablation_id: str | None = None

    def enabled(self, flag: str) -> bool:
        """Whether one named component is present. Raises on a name §14 does not have."""

        if flag not in FLAG_NAMES:
            raise AblationRegistryError(
                f"{flag!r} is not a protocol §14 component; the registered flags are "
                f"{list(FLAG_NAMES)!r}"
            )
        value = getattr(self, flag)
        assert isinstance(value, bool)
        return value

    @property
    def removed(self) -> tuple[str, ...]:
        """The components this configuration switched off, in :data:`FLAG_NAMES` order."""

        return tuple(flag for flag in FLAG_NAMES if not self.enabled(flag))

    def labels(self) -> dict[str, str]:
        """What a receipt records about this configuration: nothing at all, under ``FULL``.

        An empty mapping for the unablated router is the whole point. Every receipt written
        by a deployment that runs the shipped components is byte-identical to the one it
        would have written before §14's switches existed, so an ablation cannot be mistaken
        for a production decision and a production decision cannot quietly acquire an
        ablation's provenance. When a component *is* removed the receipt says which, because
        a decision made without experience retrieval is not the same decision as one made
        with it and a reader has no other way to tell.
        """

        removed = self.removed
        if not removed:
            return {}
        labels = {f"{ABLATION_LABEL_PREFIX}{flag}": REMOVED for flag in removed}
        if self.ablation_id is not None:
            labels[ABLATION_LABEL] = self.ablation_id
        return labels

    def without(self, flag: str, *, ablation_id: str | None = None) -> RouterFeatureFlags:
        """This configuration with one more component removed."""

        if flag not in FLAG_NAMES:
            raise AblationRegistryError(
                f"{flag!r} is not a protocol §14 component; the registered flags are "
                f"{list(FLAG_NAMES)!r}"
            )
        return replace(self, **{flag: False}, ablation_id=ablation_id or self.ablation_id)


FULL = RouterFeatureFlags()
"""Every §14 component present: the shipped router, and the default of every flagged path."""


@dataclass(frozen=True, slots=True)
class Ablation:
    """One row of protocol §14: what is removed, which flag removes it, and why it matters.

    ``question`` is carried verbatim from the protocol rather than paraphrased, because the
    question is the thing being pre-registered — an ablation whose question was rewritten
    after the numbers were seen is an ablation that answers whatever the numbers said.
    """

    ablation_id: str
    removed_component: str
    cleared_flag: str
    question: str

    def flags(self) -> RouterFeatureFlags:
        """:data:`FULL` with this row's one component removed."""

        return FULL.without(self.cleared_flag, ablation_id=self.ablation_id)


def _entries(payload: Mapping[str, Any], path: Path) -> list[Ablation]:
    raw = payload.get("ablations")
    if not isinstance(raw, list):
        raise AblationRegistryError(f"{path} carries no 'ablations' list")
    entries: list[Ablation] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AblationRegistryError(f"{path} has an ablation entry that is not an object")
        try:
            entries.append(
                Ablation(
                    ablation_id=str(item["ablation_id"]),
                    removed_component=str(item["removed_component"]),
                    cleared_flag=str(item["cleared_flag"]),
                    question=str(item["question"]),
                )
            )
        except KeyError as error:
            raise AblationRegistryError(
                f"{path} has an ablation entry missing {error.args[0]!r}"
            ) from error
    return entries


def load_ablations(path: Path = ABLATIONS_PATH) -> Mapping[str, Ablation]:
    """The registered §14 table, keyed by ablation id, proved to be a table of §14.

    Three refusals, and each of them is a way a run could report ten ablations and have
    performed something else. A flag no entry clears means a §14 component nobody ablated;
    a flag two entries clear means one component ablated twice and another not at all; a
    flag that is not a component at all means an entry that switches nothing. The count is
    therefore checked as a *bijection* onto :data:`FLAG_NAMES` and not as ``len() == 10``,
    which the second of those would pass.
    """

    if not path.exists():
        raise AblationRegistryError(
            f"{path} is missing; protocol §14's required ablations are not registered"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AblationRegistryError(f"{path} must contain a JSON object")
    entries = _entries(payload, path)

    by_id: dict[str, Ablation] = {}
    for entry in entries:
        if entry.ablation_id in by_id:
            raise AblationRegistryError(
                f"{path} registers ablation {entry.ablation_id!r} twice"
            )
        by_id[entry.ablation_id] = entry

    cleared = [entry.cleared_flag for entry in entries]
    unknown = sorted(set(cleared) - set(FLAG_NAMES))
    if unknown:
        raise AblationRegistryError(
            f"{path} clears {unknown!r}, which are not protocol §14 components"
        )
    doubled = sorted({flag for flag in cleared if cleared.count(flag) > 1})
    if doubled:
        raise AblationRegistryError(
            f"{path} clears {doubled!r} in more than one ablation; two entries that remove "
            "one component answer one question twice and another question never"
        )
    unablated = sorted(set(FLAG_NAMES) - set(cleared))
    if unablated:
        raise AblationRegistryError(
            f"{path} registers no ablation for {unablated!r}; protocol §14 requires an "
            "answer about every component, and a component nobody removed has none"
        )
    # Registry order and not sorted order: §14's ids sort as A1, A10, A2 under a string
    # sort, and a report that walked them that way would not be walking the protocol's
    # table. The file's own order is the table's order and is already deterministic.
    return MappingProxyType(by_id)


def from_ablation(ablation_id: str, *, path: Path = ABLATIONS_PATH) -> RouterFeatureFlags:
    """The flags one registered §14 ablation runs under.

    Every id resolves to a configuration that differs from :data:`FULL` in exactly one
    component, which is what makes the ten runs comparable to each other and to the whole:
    an "ablation" that removed two things at once would attribute both effects to one row.
    """

    registered = load_ablations(path)
    entry = registered.get(ablation_id)
    if entry is None:
        raise UnknownAblation(
            f"{ablation_id!r} is not a registered protocol §14 ablation; the registered "
            f"ids are {list(registered)!r}"
        )
    return entry.flags()


__all__ = [
    "ABLATIONS_PATH",
    "ABLATION_LABEL",
    "ABLATION_LABEL_PREFIX",
    "FLAG_NAMES",
    "FULL",
    "Ablation",
    "AblationError",
    "AblationRegistryError",
    "REMOVED",
    "RouterFeatureFlags",
    "UnknownAblation",
    "from_ablation",
    "load_ablations",
]
