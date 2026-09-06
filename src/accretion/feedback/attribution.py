"""SDD §9.6: dividing one run's outcome between the nodes that produced it (OQ-407).

A run succeeds or fails once; a router learns per node. Something has to say how much of that
single outcome each node is answerable for, and OQ-407 leaves the method open while §7.10
requires whatever answers it to be **versioned** — ``AttributionSummary.method_version`` is
mandatory even when the score is null, precisely so that a later, better attributor can tell
the records it has already replaced from the ones it has not.

This module implements the first such method, ``dep-heuristic+retry-delta/1``, and it is
deliberately a heuristic that says so:

* **Dependency share.** A node with more graph parents did less of the work that reached the
  outcome, because more of it arrived already done. Its weight is ``1 / (1 + parents)`` and the
  weights are normalised across the run, so the shares sum to one and no node can be credited
  with a run twice. The graph is read from ``store.get_run_graph``, which is the only structure
  in the repository that says which node fed which.
* **Retry delta.** ADR-041 makes each attempt at a node its own routable action with its own
  receipt and its own experience record, so a retry that fixed what the previous attempt broke
  must be credited for the *change* rather than for the state. Attempts are paired by
  ``(node_key, attempt)`` — the two labels ``routing/freeze.py`` writes onto every node
  contract — and the later attempt carries ``(direction − previous direction) / 2`` on top of
  its own direction.
* **Confidence never exceeds one half.** ``_HEURISTIC_CONFIDENCE`` is 0.5 and every uncertainty
  halves it again: an undecided local verdict has no direction to attribute, and a run whose
  final status is not yet known is evidence about a node that has not finished being judged.
  A heuristic that reported high confidence would be indistinguishable, downstream, from a
  causal method that had earned it.

**Nothing here rewrites a record.** Registry §17 forbids editing a historical row, and §9.6
makes attribution a *derived, versioned view* rather than a property of the outcome. So a
recomputation appends a revision through
:meth:`~accretion.feedback.experience.ExperienceProjector.reattribute` and the root row's bytes
are exactly the bytes it was written with — which is what ``AC4-M3-026`` asserts, by counting
the store's in-place writes and requiring zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from accretion.contracts import PrincipalRef, Run, RunGraph
from accretion.contracts.routing import (
    AttributionSummary,
    ExperienceRecord,
    VerificationState,
)
from accretion.feedback.experience import (
    ATTEMPT_LABEL,
    EXPERIENCE_ID_LABEL,
    NODE_ID_LABEL,
    NODE_KEY_LABEL,
    ExperienceProjector,
)
from accretion.persistence.store import StateStore

__all__ = [
    "METHOD_VERSION",
    "AttributionInput",
    "DependencyAttributor",
]

METHOD_VERSION = "dep-heuristic+retry-delta/1"
"""OQ-407's first answer, versioned because §7.10 requires the method to be nameable.

The ``/1`` is not decoration: a second method, or a changed constant inside this one, is a
different view of the same evidence and must produce a *different* ``method_version`` so that
its revisions are distinguishable from the ones this method wrote. Changing the arithmetic
below without changing this string would silently mix two methods' scores in one snapshot.
"""

_HEURISTIC_CONFIDENCE = 0.5
"""The ceiling a heuristic may claim. Halved once per source of ignorance; see the module
docstring."""

_DIRECTION: Mapping[VerificationState, int] = {
    VerificationState.PASS: 1,
    VerificationState.FAIL: -1,
}
"""Which way a node's own verdict points. Everything absent from this table points nowhere:
``INCONCLUSIVE`` and ``PENDING`` are undecided, and ``ERROR`` and ``QUARANTINED`` are statements
about the verification rather than about the work."""


@dataclass(frozen=True, slots=True)
class AttributionInput:
    """One node's share of the evidence, reduced to what the method actually reads.

    A dataclass and not the record, because the attributor runs *before* the records exist —
    :meth:`~accretion.feedback.service.DefaultFeedbackPipeline.record_final` computes the
    attribution and then projects each node with it, so that the first generation of a record
    is already attributed and no run leaves an unattributed row behind for a snapshot to pick
    up. :meth:`DependencyAttributor.inputs_from` rebuilds these from stored records when the
    same method is re-run later.
    """

    execution_instance_id: str
    node_id: str
    node_key: str
    attempt: int
    local_status: VerificationState
    final_status: VerificationState | None


@dataclass(frozen=True, slots=True)
class DependencyAttributor:
    """``dep-heuristic+retry-delta/1``: graph dependency share, adjusted by retry delta."""

    store: StateStore

    async def compute_for_run(
        self, run: Run, entries: Sequence[AttributionInput]
    ) -> dict[str, AttributionSummary]:
        """The attribution of every entry, keyed by ``execution_instance_id``.

        Reads the graph once for the whole run rather than once per node: the parent counts are
        a property of the graph and a per-node read would let two nodes of one run be attributed
        against two different graph revisions if a re-plan landed in between.
        """

        graph = await self.store.get_run_graph(run.run_id)
        return self.compute(entries, graph=graph)

    def compute(
        self, entries: Sequence[AttributionInput], *, graph: RunGraph | None
    ) -> dict[str, AttributionSummary]:
        """The pure half: no store, no clock, and the same answer for the same inputs.

        ``graph`` is nullable because a run whose graph has been pruned still has experience
        records to attribute, and refusing to attribute them would lose the evidence entirely.
        With no graph every node has zero known parents, so the weights are equal and the share
        is a flat ``1/n`` — which is the honest answer to "we cannot see the dependencies"
        rather than a guess at them.

        Entries are sorted by ``execution_instance_id`` before anything is summed, so that a
        caller passing the same nodes in a different order gets identical floats: the shares are
        divided by a sum, and floating-point addition is not associative.
        """

        ordered = sorted(entries, key=lambda entry: entry.execution_instance_id)
        if not ordered:
            return {}
        parents = _parent_counts(graph)
        weights = {
            entry.execution_instance_id: 1.0 / (1.0 + parents.get(entry.node_id, 0))
            for entry in ordered
        }
        total_weight = sum(weights.values())
        previous = _previous_attempts(ordered)

        summaries: dict[str, AttributionSummary] = {}
        for entry in ordered:
            share = weights[entry.execution_instance_id] / total_weight
            direction = _DIRECTION.get(entry.local_status, 0)
            earlier = previous.get((entry.node_key, entry.attempt))
            delta = 0.0
            if earlier is not None:
                delta = (direction - _DIRECTION.get(earlier.local_status, 0)) / 2.0
            score = max(-1.0, min(1.0, share * (direction + delta)))
            confidence = _HEURISTIC_CONFIDENCE
            if direction == 0:
                confidence /= 2.0
            if entry.final_status is None:
                confidence /= 2.0
            summaries[entry.execution_instance_id] = AttributionSummary(
                # Rounded, and rounded here rather than at the call site: the score is inside
                # a derived id and inside a content hash, so two runs of one computation that
                # differed in the sixteenth decimal would be two rows about one attribution.
                score=round(score, 6),
                confidence=round(confidence, 6),
                method_version=METHOD_VERSION,
            )
        return summaries

    def inputs_from(self, records: Sequence[ExperienceRecord]) -> list[AttributionInput]:
        """Rebuild the method's inputs from records that are already stored.

        Reads ``node_id``, ``node_key`` and ``attempt`` from the header labels the projector
        wrote, because none of the three is recoverable from the record's own fields:
        ``source_node_execution_id`` is a digest over exactly those parts and digests do not
        invert. A record written by something else — an M0 fixture, a hand-built row — has no
        such labels, and it falls back to its execution instance as its own node with attempt
        one, which attributes it as an isolated node rather than dropping it.
        """

        return [
            AttributionInput(
                execution_instance_id=record.source_node_execution_id,
                node_id=record.labels.get(
                    NODE_ID_LABEL, record.source_node_execution_id
                ),
                node_key=record.labels.get(
                    NODE_KEY_LABEL, record.source_node_execution_id
                ),
                attempt=_attempt(record),
                local_status=record.local_verification_status,
                final_status=record.final_run_status,
            )
            for record in records
        ]

    async def reattribute(
        self,
        *,
        run: Run,
        records: Sequence[ExperienceRecord],
        projector: ExperienceProjector,
        principal: PrincipalRef,
    ) -> list[ExperienceRecord]:
        """Recompute over stored records and append a revision wherever the answer moved.

        Returns only the revisions written, in ``contract_id`` order, and writes nothing for a
        record whose attribution is already what this method computes. Silence is the correct
        result of a re-run that changed nothing: appending an identical-but-for-the-clock
        revision would grow the chain on every pass and make "has the attribution changed?"
        unanswerable from the history.

        A record with no ``experience_id`` label is skipped rather than guessed at. Filing a
        revision under the wrong parent is the quietest possible corruption of this table — the
        row would be valid, stored, and attributed to somebody else's experience forever.
        """

        summaries = await self.compute_for_run(run, self.inputs_from(records))
        written: list[ExperienceRecord] = []
        for record in sorted(records, key=lambda item: item.contract_id):
            summary = summaries.get(record.source_node_execution_id)
            if summary is None or summary == record.attribution:
                continue
            experience_id = record.labels.get(EXPERIENCE_ID_LABEL)
            if experience_id is None:
                continue
            written.append(
                await projector.reattribute(
                    record,
                    experience_id=experience_id,
                    attribution=summary,
                    principal=principal,
                )
            )
        return written


def _attempt(record: ExperienceRecord) -> int:
    """The attempt number from the label, defaulting to one on anything unreadable.

    A label is a free-form string and this one is written by the projector from another
    free-form string on the node contract, so it can in principle be absent or not a number.
    Refusing to attribute the whole run because one label is malformed would trade a wrong
    retry pairing for no attribution at all.
    """

    raw = record.labels.get(ATTEMPT_LABEL, "1")
    try:
        value = int(raw)
    except ValueError:
        return 1
    return value if value >= 1 else 1


def _parent_counts(graph: RunGraph | None) -> dict[str, int]:
    """How many distinct nodes feed each node, by ``node_id``.

    Counted over *distinct* sources: two edges between one pair — a normal edge and a retry
    edge, say — are one dependency, and counting them twice would halve that node's weight for
    a reason that has nothing to do with how much work arrived already done.
    """

    if graph is None:
        return {}
    sources: dict[str, set[str]] = {}
    for edge in graph.edges:
        sources.setdefault(edge.target, set()).add(edge.source)
    return {target: len(values) for target, values in sources.items()}


def _previous_attempts(
    entries: Sequence[AttributionInput],
) -> dict[tuple[str, int], AttributionInput]:
    """For every entry, the entry for the immediately preceding attempt at the same node.

    Keyed by the *later* attempt's ``(node_key, attempt)`` so the lookup is direct. Only the
    immediately preceding attempt counts: the delta is what the reselection changed, and
    measuring attempt four against attempt one would credit one retry with three retries' worth
    of change.
    """

    by_key: dict[tuple[str, int], AttributionInput] = {
        (entry.node_key, entry.attempt): entry for entry in entries
    }
    return {
        (entry.node_key, entry.attempt): by_key[(entry.node_key, entry.attempt - 1)]
        for entry in entries
        if (entry.node_key, entry.attempt - 1) in by_key
    }
