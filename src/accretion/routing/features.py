"""The versioned feature schema a router learns under (SDD §7.12, §10.1).

A feature vector is not a list of numbers, it is a *schema*. ``FEATURE_SCHEMA_VERSION``
already exists at :data:`accretion.contracts.routing.FEATURE_SCHEMA_VERSION` and is
recorded on every :class:`~accretion.contracts.routing.RoutingContext` and every
:class:`~accretion.contracts.routing.RouterModelVersion`, but a version number is only
worth something if there is one place that says what the version *is*. This module is that
place: :data:`FEATURE_SCHEMA_V1` is the ordered column list, and
:func:`feature_schema_digest` is the value that changes the moment the list does.

**Order is data.** A trained model's weights are positional. Reordering two columns
without changing the version would leave every stored model still loadable and quietly
wrong about which number meant which thing, which is why the digest is over the ordered
specs rather than over a set of names, and why the pinned digest is written into a test as
a literal instead of recomputed there.

**Nothing here trains, and nothing here reads a clock.** :func:`featurize` is a pure
function of the five sealed contracts it is handed plus a frozen :class:`Vocabulary`, and
:func:`summarize_evidence` takes the instant it measures recency against as an argument.
Both properties exist so that a training snapshot can be rebuilt byte for byte a year
later (§10.1) rather than approximately.

**Missing is ``None``, never zero.** The sealed feature contracts are careful about this —
:class:`~accretion.contracts.routing.TaskFeatures` keeps ``float | None`` rather than
defaulting an unobserved dimension to zero, because zero is a confident score of "none of
this" — and a featurizer that collapsed the two would erase the distinction on the way in.
So :class:`FeatureRow` holds ``float | None`` and every absence is propagated.

**The one rule that is a claim rather than a convention.** ``n_cross_domain`` counts
evidence whose contract signature does *not* match the node being routed, and it is never
added into ``n_same_signature`` or ``n_same_config``. Evidence from another kind of node,
under another verification spec, at another risk class is not weak evidence about this
node — it is evidence about something else, and letting it inflate the in-domain count
would make a router look confident exactly where it has learned nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from accretion.contracts import (
    RISK_RANK,
    ExpectedHorizon,
    GraphNodeKind,
    Provider,
    RiskLevel,
)
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import (
    FEATURE_SCHEMA_VERSION,
    ContractSignature,
    ContradictionStatus,
    ExecutionConfiguration,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    RoutingContext,
    VerificationState,
    risk_level_for,
)

__all__ = [
    "FEATURE_SCHEMA_V1",
    "OTHER_INDEX",
    "OTHER_TOKEN",
    "EvidenceSummary",
    "FeatureKind",
    "FeatureRow",
    "FeatureSpec",
    "Vocabulary",
    "feature_schema_digest",
    "featurize",
    "summarize_evidence",
]


FeatureKind = Literal["float", "ordinal", "onehot", "count"]
"""What a column *is*, so that a later learner can bin, scale or split it correctly.

Four kinds and no more. ``float`` is a bounded continuous score, ``count`` a non-negative
integer magnitude, ``ordinal`` a small ordered ladder encoded as its rank, and ``onehot``
one indicator of a closed enumeration. A learner that treated a one-hot indicator as a
continuous score would be interpolating between two enum members, which is meaningless;
recording the kind beside the name is what lets it avoid that without hard-coding column
indices.
"""


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """One column of the feature vector: what it is called, what it is, where it came from.

    ``source`` is a dotted path onto the sealed contract the value is read from, and it is
    part of the schema digest on purpose. Two columns can carry the same name and the same
    kind and still mean different things if one is read from the node's cap and the other
    from the objective's budget, and a digest that ignored provenance would call those two
    schemas equal.
    """

    name: str
    kind: FeatureKind
    source: str


# The ordered enumerations every one-hot and every kind-count block is generated from.
# Sorted by value rather than taken in declaration order: a contributor who reorders the
# members of `GraphNodeKind` for readability must not silently permute a trained model's
# columns, and sorting is the cheapest way to make declaration order stop mattering.
_GRAPH_NODE_KINDS: tuple[GraphNodeKind, ...] = tuple(sorted(GraphNodeKind))
_PROVIDERS: tuple[Provider, ...] = tuple(sorted(Provider))

_HORIZON_RANK: dict[ExpectedHorizon, int] = {
    ExpectedHorizon.SHORT: 0,
    ExpectedHorizon.MEDIUM: 1,
    ExpectedHorizon.LONG: 2,
}
"""The planning horizon as an ordered ladder, for exactly the reason ``RISK_RANK`` exists.

``ExpectedHorizon`` is a ``StrEnum`` and its members compare alphabetically, which makes
``LONG < MEDIUM < SHORT`` true and a plausible-looking ordering bug easy to write. The rank
is stated once here instead.
"""

_ISOLATION_RANK: dict[str, int] = {
    "none": 0,
    "worktree": 1,
    "container": 2,
    "vm": 3,
}
"""How strongly a configuration is isolated from the developer's checkout, as a ladder.

This is a *feature encoding* and deliberately not a contract vocabulary.
``EnvironmentBinding.workspace_isolation`` is a free ``str`` in the sealed contract and
registry §5 defines no isolation enum, so freezing one here would create the second source
of truth registry §21 forbids. What this dict does instead is state the ordering of the
spellings the repository actually writes (v0.1's ``WorkspaceLease.isolation`` defaults to
``WORKTREE``; the v0.4 fixtures write ``worktree``), matched case-insensitively.

An unrecognised spelling yields ``None`` — missing — and **not** ``0``. Ranking an unknown
isolation as the weakest one would tell a learner that a mode nobody has described is the
least isolated thing there is, which is a claim this module has no basis for.
"""

OTHER_TOKEN = "OTHER"
"""The reserved name of vocabulary index 0, held for every token the vocabulary lacks."""

OTHER_INDEX = 0
"""Where an unknown token lands. A frozen vocabulary must have somewhere to put a stranger.

A snapshot freezes its vocabulary, and a model trained under it is then asked to score a
configuration naming a model id that did not exist when the snapshot was cut. Growing the
vocabulary at scoring time would shift every index above the insertion point and silently
re-map a trained model's learned splits; refusing the candidate would make a new model
unroutable. Index 0 is the third option: the stranger is scored as a stranger, the indices
of every known token are stable for the life of the snapshot, and the fact that a token was
unknown is recoverable from the row.
"""


def _sorted_unique(tokens: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(tokens)))


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """The frozen token tables a snapshot's categorical indices are read against.

    Frozen *per snapshot*, which is what makes an index reproducible: the same experience,
    re-featurized a year later under the vocabulary the snapshot recorded, lands in the
    same column with the same value. :meth:`digest` is what a snapshot stores so that a
    later caller cannot quietly hand a different table to the same rows.

    ``model_ids`` is indexed from two token spaces — ``ExecutionConfiguration.model.model_id``
    at routing time and ``Experience.runtime_model`` at training time — so a snapshot must
    freeze it over the *union* of both, or one side maps every token to ``OTHER_TOKEN``.

    Both tuples must arrive sorted and duplicate-free, and neither may contain
    :data:`OTHER_TOKEN`. That is checked rather than fixed up, because silently sorting a
    caller's list would mean two callers who passed the same tokens in different orders got
    the same indices while believing they had chosen them — use :meth:`frozen_over` when
    the order genuinely does not matter to the caller.
    """

    model_ids: tuple[str, ...] = ()
    adapter_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name, tokens in (
            ("model_ids", self.model_ids),
            ("adapter_versions", self.adapter_versions),
        ):
            if tokens != _sorted_unique(tokens):
                raise ValueError(
                    f"{field_name} is not sorted and duplicate-free; a vocabulary whose "
                    "order depends on how the caller happened to collect its tokens gives "
                    "two callers different indices for the same token"
                )
            if OTHER_TOKEN in tokens:
                raise ValueError(
                    f"{field_name} contains {OTHER_TOKEN!r}, which is reserved for index "
                    f"{OTHER_INDEX} and holds every token this vocabulary does not know"
                )

    @classmethod
    def frozen_over(
        cls,
        *,
        model_ids: Iterable[str] = (),
        adapter_versions: Iterable[str] = (),
    ) -> Vocabulary:
        """Freeze a vocabulary over whatever tokens were observed, in canonical order."""

        return cls(
            model_ids=_sorted_unique(model_ids),
            adapter_versions=_sorted_unique(adapter_versions),
        )

    def model_index(self, token: str) -> int:
        """The stable index of a model id, or :data:`OTHER_INDEX` if it is a stranger."""

        return self._index(self.model_ids, token)

    def adapter_index(self, token: str) -> int:
        """The stable index of a runtime adapter version, or :data:`OTHER_INDEX`."""

        return self._index(self.adapter_versions, token)

    @staticmethod
    def _index(tokens: tuple[str, ...], token: str) -> int:
        try:
            return tokens.index(token) + 1
        except ValueError:
            return OTHER_INDEX

    def digest(self) -> str:
        """The digest a snapshot records, so a mismatched vocabulary is caught, not absorbed."""

        return content_hash(
            {
                "adapter_versions": list(self.adapter_versions),
                "model_ids": list(self.model_ids),
                "other_token": OTHER_TOKEN,
            },
            exclude=(),
        )


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """What the store already knows about configurations like this one, at one instant.

    Nine numbers, and the ninth is the one that matters. ``n_cross_domain`` is evidence
    that was retrieved and then *not* counted: it never appears in ``n_same_signature`` or
    ``n_same_config``, and none of the means or rates below are computed over it. Build one
    with :func:`summarize_evidence` rather than by hand, because that function is where the
    rule lives and a hand-built summary is a number without a provenance.

    Every aggregate is nullable and every count is not. A rate over no observations is
    absent, not zero; a count over no observations is genuinely zero.
    """

    n_same_config: int = 0
    n_same_signature: int = 0
    verified_success_rate: float | None = None
    mean_quality: float | None = None
    mean_cost: float | None = None
    mean_latency_ms: float | None = None
    recency_days: float | None = None
    n_contradictions_resolved: int = 0
    n_cross_domain: int = 0


def summarize_evidence(
    records: Iterable[ExperienceRecord],
    *,
    signature: ContractSignature,
    configuration_hash: str,
    as_of: datetime,
) -> EvidenceSummary:
    """Summarise retrieved experience about one node, keeping the domains apart.

    ``signature`` is the node's retrieval key (SDD §7.10): the node kind, the objective and
    capability digests, the verification spec hash and the risk class that two nodes must
    share before one's outcome is evidence about the other. A record matching it is
    in-domain and contributes to every count and every mean; a record that does not is
    counted once, in ``n_cross_domain``, and contributes to nothing else.

    ``as_of`` is passed in rather than read from the clock so that the same records
    summarised twice give the same numbers — which is the whole of §10.1's reproducibility
    requirement applied to one function.
    """

    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ValueError(
            "as_of is naive and so names no instant; recency measured against it would "
            "depend on the machine that computed it"
        )

    # Materialised once and sorted, so that a caller passing a generator, a set or a
    # differently-ordered list gets the same summary. `mean_cost` is a sum of decimals and
    # decimal addition is exact, but the sort costs nothing and removes the question.
    observed = sorted(records, key=lambda record: record.contract_id)
    in_domain = [record for record in observed if record.contract_signature == signature]
    n_cross_domain = len(observed) - len(in_domain)

    if not in_domain:
        return EvidenceSummary(n_cross_domain=n_cross_domain)

    total = len(in_domain)
    qualities = [
        record.outcomes.quality for record in in_domain if record.outcomes.quality is not None
    ]
    cost_total = sum((record.outcomes.cost for record in in_domain), start=Decimal(0))
    return EvidenceSummary(
        n_same_config=sum(
            1 for record in in_domain if record.configuration_hash == configuration_hash
        ),
        n_same_signature=total,
        verified_success_rate=(
            sum(
                1
                for record in in_domain
                if record.local_verification_status is VerificationState.PASS
            )
            / total
        ),
        mean_quality=(sum(qualities) / len(qualities) if qualities else None),
        mean_cost=float(cost_total / total),
        mean_latency_ms=sum(record.outcomes.latency_ms for record in in_domain) / total,
        recency_days=(
            (as_of - max(record.created_at for record in in_domain)).total_seconds() / 86_400.0
        ),
        n_contradictions_resolved=sum(
            1
            for record in in_domain
            if record.contradiction_status is ContradictionStatus.RESOLVED
        ),
        n_cross_domain=n_cross_domain,
    )


def _task_specs() -> tuple[FeatureSpec, ...]:
    scored = (
        "complexity",
        "structure_certainty",
        "feedback_dependency",
        "dependency_complexity",
        "parallelism_potential",
        "uncertainty",
        "verifier_strength",
    )
    specs = [
        FeatureSpec(f"task.{name}", "float", f"TaskFeatures.{name}") for name in scored
    ]
    specs.append(FeatureSpec("task.risk_rank", "ordinal", "TaskFeatures.risk"))
    specs.append(
        FeatureSpec("task.irreversible_actions", "ordinal", "TaskFeatures.irreversible_actions")
    )
    specs.append(
        FeatureSpec("task.expected_horizon_rank", "ordinal", "TaskFeatures.expected_horizon")
    )
    specs.append(
        FeatureSpec("task.profile_confidence", "float", "TaskFeatures.profile_confidence")
    )
    return tuple(specs)


def _project_specs() -> tuple[FeatureSpec, ...]:
    return (
        FeatureSpec(
            "project.feature_window_days", "count", "ProjectFeatures.feature_window_days"
        ),
        FeatureSpec(
            "project.observed_task_count", "count", "ProjectFeatures.observed_task_count"
        ),
        FeatureSpec("project.mean_complexity", "float", "ProjectFeatures.mean_complexity"),
        FeatureSpec("project.mean_uncertainty", "float", "ProjectFeatures.mean_uncertainty"),
        FeatureSpec(
            "project.mean_verifier_strength", "float", "ProjectFeatures.mean_verifier_strength"
        ),
        FeatureSpec(
            "project.irreversible_action_rate",
            "float",
            "ProjectFeatures.irreversible_action_rate",
        ),
        FeatureSpec("project.maximum_risk_rank", "ordinal", "ProjectFeatures.maximum_risk"),
        FeatureSpec(
            "project.dominant_expected_horizon_rank",
            "ordinal",
            "ProjectFeatures.dominant_expected_horizon",
        ),
    )


def _graph_specs() -> tuple[FeatureSpec, ...]:
    specs = [
        FeatureSpec("graph.depth", "count", "GraphFeatures.depth"),
        FeatureSpec("graph.critical_path", "ordinal", "GraphFeatures.critical_path"),
        FeatureSpec("graph.retry_number", "count", "GraphFeatures.retry_number"),
    ]
    specs.extend(
        FeatureSpec(
            f"graph.parent_kind_count.{kind.value}", "count", "GraphFeatures.parent_node_types"
        )
        for kind in _GRAPH_NODE_KINDS
    )
    specs.extend(
        FeatureSpec(
            f"graph.child_kind_count.{kind.value}", "count", "GraphFeatures.child_node_types"
        )
        for kind in _GRAPH_NODE_KINDS
    )
    return tuple(specs)


def _node_specs() -> tuple[FeatureSpec, ...]:
    specs = [
        FeatureSpec(f"node.kind_is.{kind.value}", "onehot", "NodeContract.node_kind")
        for kind in _GRAPH_NODE_KINDS
    ]
    specs.append(
        FeatureSpec(
            "node.allowed_risk_class_rank", "ordinal", "NodeContract.allowed_risk_class"
        )
    )
    specs.append(
        FeatureSpec(
            "node.required_capability_count", "count", "NodeContract.required_capabilities"
        )
    )
    specs.append(
        FeatureSpec(
            "node.evidence_requirement_count", "count", "NodeContract.evidence_requirements"
        )
    )
    specs.extend(
        (
            FeatureSpec(
                "node.resource_cap.maximum_cost", "float", "NodeContract.resource_cap.maximum_cost"
            ),
            FeatureSpec(
                "node.resource_cap.maximum_latency_ms",
                "count",
                "NodeContract.resource_cap.maximum_latency_ms",
            ),
            FeatureSpec(
                "node.resource_cap.maximum_attempts",
                "count",
                "NodeContract.resource_cap.maximum_attempts",
            ),
            FeatureSpec(
                "node.resource_cap.maximum_tool_calls",
                "count",
                "NodeContract.resource_cap.maximum_tool_calls",
            ),
        )
    )
    return tuple(specs)


def _candidate_specs() -> tuple[FeatureSpec, ...]:
    specs = [
        FeatureSpec(
            f"candidate.provider_is.{provider.value}",
            "onehot",
            "ExecutionConfiguration.model.provider",
        )
        for provider in _PROVIDERS
    ]
    specs.extend(
        (
            FeatureSpec(
                "candidate.model_vocab_index", "ordinal", "ExecutionConfiguration.model.model_id"
            ),
            FeatureSpec(
                "candidate.adapter_vocab_index",
                "ordinal",
                "ExecutionConfiguration.runtime.adapter_version",
            ),
            FeatureSpec("candidate.tool_count", "count", "ExecutionConfiguration.tools"),
            FeatureSpec("candidate.skill_count", "count", "ExecutionConfiguration.skills"),
            FeatureSpec(
                "candidate.environment_isolation_rank",
                "ordinal",
                "ExecutionConfiguration.environment.workspace_isolation",
            ),
        )
    )
    return tuple(specs)


def _evidence_specs() -> tuple[FeatureSpec, ...]:
    return (
        FeatureSpec("evidence.n_same_config", "count", "EvidenceSummary.n_same_config"),
        FeatureSpec("evidence.n_same_signature", "count", "EvidenceSummary.n_same_signature"),
        FeatureSpec(
            "evidence.verified_success_rate", "float", "EvidenceSummary.verified_success_rate"
        ),
        FeatureSpec("evidence.mean_quality", "float", "EvidenceSummary.mean_quality"),
        FeatureSpec("evidence.mean_cost", "float", "EvidenceSummary.mean_cost"),
        FeatureSpec("evidence.mean_latency_ms", "float", "EvidenceSummary.mean_latency_ms"),
        FeatureSpec("evidence.recency_days", "float", "EvidenceSummary.recency_days"),
        FeatureSpec(
            "evidence.n_contradictions_resolved",
            "count",
            "EvidenceSummary.n_contradictions_resolved",
        ),
        FeatureSpec("evidence.n_cross_domain", "count", "EvidenceSummary.n_cross_domain"),
    )


def _objective_specs() -> tuple[FeatureSpec, ...]:
    return (
        FeatureSpec(
            "objective.utility_weight_quality",
            "float",
            "ObjectiveContract.utility_weights.quality",
        ),
        FeatureSpec(
            "objective.utility_weight_cost", "float", "ObjectiveContract.utility_weights.cost"
        ),
        FeatureSpec(
            "objective.utility_weight_latency",
            "float",
            "ObjectiveContract.utility_weights.latency",
        ),
        FeatureSpec(
            "objective.verified_success_floor",
            "float",
            "ObjectiveContract.verified_success_floor",
        ),
        FeatureSpec(
            "objective.false_acceptance_ceiling",
            "float",
            "ObjectiveContract.false_acceptance_ceiling",
        ),
    )


FEATURE_SCHEMA_V1: tuple[FeatureSpec, ...] = (
    *_task_specs(),
    *_project_specs(),
    *_graph_specs(),
    *_node_specs(),
    *_candidate_specs(),
    *_evidence_specs(),
    *_objective_specs(),
)
"""The ordered columns of feature schema ``1.0.0``, in seven groups (SDD §7.4, §7.12).

Task 11, project 8, graph 21, node contract 16, candidate configuration 11, evidence 9,
objective 5 — eighty-one columns. The groups are ordered by how far the value is from the
node being routed: what the task is, what the project is like, where the node sits in the
graph, what the node's own contract demands, what this candidate configuration offers, what
past evidence says about configurations like it, and finally what the objective is willing
to trade. That ordering is a readability choice; the *digest* is what makes it binding.

Changing this tuple in any way — adding, removing, reordering or re-sourcing a column —
changes :func:`feature_schema_digest` and is a new
:data:`~accretion.contracts.routing.FEATURE_SCHEMA_VERSION`, because a router model records
the schema it learned under and refuses evidence gathered under another (§7.12).
"""


def feature_schema_digest() -> str:
    """The digest of the ordered schema: the value a router version pins its weights to.

    Over a *list* of specs and not a mapping of them, so that order is inside the digest.
    A schema digest that ignored order would be satisfied by a permutation, and a
    permutation is precisely the change that leaves every stored model loadable and every
    one of its weights pointing at the wrong column.
    """

    return content_hash(
        [
            {"kind": spec.kind, "name": spec.name, "source": spec.source}
            for spec in FEATURE_SCHEMA_V1
        ],
        exclude=(),
    )


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """One featurized routing decision: the values, and the schema they are values of.

    ``schema_version`` travels with the values rather than being looked up, because a row
    that has been written to a snapshot, read back and handed to a model is exactly the
    place where "which schema was this?" stops being obvious.
    """

    values: list[float | None]
    schema_version: str = FEATURE_SCHEMA_VERSION


def _rank_risk(risk: RiskLevel | None) -> float | None:
    return None if risk is None else float(RISK_RANK[risk])


def _rank_horizon(horizon: ExpectedHorizon | None) -> float | None:
    return None if horizon is None else float(_HORIZON_RANK[horizon])


def _rank_isolation(isolation: str) -> float | None:
    rank = _ISOLATION_RANK.get(isolation.strip().casefold())
    return None if rank is None else float(rank)


def _optional(value: float | None) -> float | None:
    return None if value is None else float(value)


def featurize(
    context: RoutingContext,
    candidate: ExecutionConfiguration,
    node: NodeContract,
    objective: ObjectiveContract,
    evidence: EvidenceSummary,
    vocab: Vocabulary,
) -> FeatureRow:
    """Project five sealed contracts and one evidence summary onto :data:`FEATURE_SCHEMA_V1`.

    Pure, total and order-preserving: the returned list is exactly as long as the schema,
    position *i* is always the column ``FEATURE_SCHEMA_V1[i]`` describes, and a value the
    contracts leave unobserved arrives as ``None`` rather than as a zero the caller would
    have no way to tell from a measurement.

    The row is *not* checked against the contracts' own ``feature_schema_version``.
    Deciding whether a context recorded under another schema may be featurized under this
    one is a policy question that belongs to the caller that owns the model — this function
    would have to guess, and guessing is what the version exists to prevent.
    """

    task = context.task_features
    project = context.project_features
    graph = context.graph_features
    parents = list(graph.parent_node_types)
    children = list(graph.child_node_types)

    values: list[float | None] = [
        _optional(task.complexity),
        _optional(task.structure_certainty),
        _optional(task.feedback_dependency),
        _optional(task.dependency_complexity),
        _optional(task.parallelism_potential),
        _optional(task.uncertainty),
        _optional(task.verifier_strength),
        _rank_risk(task.risk),
        float(task.irreversible_actions),
        _rank_horizon(task.expected_horizon),
        float(task.profile_confidence),
        float(project.feature_window_days),
        float(project.observed_task_count),
        _optional(project.mean_complexity),
        _optional(project.mean_uncertainty),
        _optional(project.mean_verifier_strength),
        _optional(project.irreversible_action_rate),
        _rank_risk(project.maximum_risk),
        _rank_horizon(project.dominant_expected_horizon),
        float(graph.depth),
        float(graph.critical_path),
        float(graph.retry_number),
    ]
    values.extend(float(parents.count(kind)) for kind in _GRAPH_NODE_KINDS)
    values.extend(float(children.count(kind)) for kind in _GRAPH_NODE_KINDS)
    values.extend(float(node.node_kind is kind) for kind in _GRAPH_NODE_KINDS)
    values.extend(
        [
            float(RISK_RANK[risk_level_for(node.allowed_risk_class)]),
            float(len(node.required_capabilities)),
            float(len(node.evidence_requirements)),
            float(node.resource_cap.maximum_cost),
            float(node.resource_cap.maximum_latency_ms),
            float(node.resource_cap.maximum_attempts),
            float(node.resource_cap.maximum_tool_calls),
        ]
    )
    values.extend(float(candidate.model.provider is provider) for provider in _PROVIDERS)
    values.extend(
        [
            float(vocab.model_index(candidate.model.model_id)),
            float(vocab.adapter_index(candidate.runtime.adapter_version)),
            float(len(candidate.tools)),
            float(len(candidate.skills)),
            _rank_isolation(candidate.environment.workspace_isolation),
            float(evidence.n_same_config),
            float(evidence.n_same_signature),
            _optional(evidence.verified_success_rate),
            _optional(evidence.mean_quality),
            _optional(evidence.mean_cost),
            _optional(evidence.mean_latency_ms),
            _optional(evidence.recency_days),
            float(evidence.n_contradictions_resolved),
            float(evidence.n_cross_domain),
            float(objective.utility_weights.quality),
            float(objective.utility_weights.cost),
            float(objective.utility_weights.latency),
            float(objective.verified_success_floor),
            float(objective.false_acceptance_ceiling),
        ]
    )

    if len(values) != len(FEATURE_SCHEMA_V1):
        # Unreachable while the block above and the spec builders agree, which is exactly
        # why it is here: they are two hand-maintained lists, and a row of the wrong length
        # would otherwise reach a learner as a silently shifted vector rather than as an
        # error naming the milestone that broke it.
        raise ValueError(
            f"featurize produced {len(values)} values for a schema of "
            f"{len(FEATURE_SCHEMA_V1)} columns; the value block and FEATURE_SCHEMA_V1 "
            "have drifted apart"
        )
    return FeatureRow(values=values)
