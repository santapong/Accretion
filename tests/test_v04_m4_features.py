"""The versioned feature schema: its pinned digest, its shape, and its one domain rule.

Three claims, and each is a way a learned router quietly goes wrong.

**The schema digest is pinned, not recomputed.** A test that called
``feature_schema_digest()`` twice and compared the results would pass under every possible
edit to :data:`~accretion.routing.features.FEATURE_SCHEMA_V1`, including the one that
matters — swapping two columns, which leaves every stored model loadable and every one of
its weights pointing at the wrong thing. The digest is therefore written out as a literal
here. Changing the schema is *supposed* to break this test; the fix is a new
``FEATURE_SCHEMA_VERSION`` and a new literal, not a recomputation.

**Missing stays missing.** The sealed contracts distinguish "the profiler could not observe
this" from "the profiler scored this zero", and the second test holds the featurizer to the
same distinction by featurizing the ``minimal`` fixtures — whose optional scores are
genuinely absent — and naming, exactly, which columns come back ``None``. The same test
featurizes the ``complete`` fixtures and requires no ``None`` at all, so that a featurizer
which returned ``None`` for everything could not pass.

**Cross-domain evidence is counted apart.** ``n_cross_domain`` exists so that evidence about
another kind of node, under another verification spec, at another risk class can be
retrieved without being mistaken for evidence about *this* node. The third test gives the
summarizer two cross-domain records with wildly different outcomes and requires every
in-domain count, rate and mean to be exactly what the three in-domain records alone say.

Fixtures come from the committed ``tests/fixtures/contracts/v0.4`` documents rather than
being assembled here, for the reason the M0 tests give: this file should not get to decide
what a valid contract looks like.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    FEATURE_SCHEMA_VERSION,
    ContractSignature,
    ExecutionConfiguration,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    RoutingContext,
)
from accretion.ids import new_id
from accretion.routing.features import (
    FEATURE_SCHEMA_V1,
    OTHER_INDEX,
    EvidenceSummary,
    Vocabulary,
    feature_schema_digest,
    featurize,
    summarize_evidence,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"

PINNED_FEATURE_SCHEMA_DIGEST = (
    "d931c4bfd6b63e2a3f952522750f4c05630e0d3818e78f7e34700f77402df875"
)
"""The digest of feature schema 1.0.0, written out rather than computed.

See the module docstring for why this is a literal and not a call.
"""

# The seven groups of SDD §7.4/§7.12, in the order `FEATURE_SCHEMA_V1` declares them.
EXPECTED_GROUP_SIZES = {
    "task": 11,
    "project": 8,
    "graph": 21,
    "node": 16,
    "candidate": 11,
    "evidence": 9,
    "objective": 5,
}


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def build[C: CanonicalContract](model: type[C], variant: str, **overrides: Any) -> C:
    """One committed fixture, re-sealed after whatever this test changed.

    Every digest is dropped rather than recomputed here: the model seals itself, so a test
    that computed one by hand would be checking its own arithmetic.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / f"{variant}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    for name in ("content_hash", *model.DERIVED_HASH_FIELDS):
        document.pop(name, None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def digest_of(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def column_names() -> list[str]:
    return [spec.name for spec in FEATURE_SCHEMA_V1]


def value_by_name(values: list[float | None]) -> dict[str, float | None]:
    return dict(zip(column_names(), values, strict=True))


def setup_featurize_inputs(
    variant: str,
) -> tuple[RoutingContext, ExecutionConfiguration, NodeContract, ObjectiveContract, Vocabulary]:
    """The five sealed inputs `featurize` takes, plus a vocabulary that knows this candidate.

    ``variant`` selects ``minimal`` — whose optional scores are absent — or ``complete``,
    whose every optional score is populated. Returning both through one builder is what
    lets a single test assert that ``None`` tracks absence rather than being unconditional.
    """

    context = build(RoutingContext, variant)
    candidate = build(ExecutionConfiguration, variant)
    node = build(NodeContract, variant)
    objective = build(ObjectiveContract, variant)
    vocab = Vocabulary.frozen_over(
        model_ids=[candidate.model.model_id, "some-other-model"],
        adapter_versions=[candidate.runtime.adapter_version],
    )
    return context, candidate, node, objective, vocab


def setup_domain_split_records() -> tuple[
    ContractSignature, str, list[ExperienceRecord], list[ExperienceRecord]
]:
    """Three in-domain records and two cross-domain ones, with deliberately unlike outcomes.

    The cross-domain pair is given a latency two orders of magnitude larger than the
    in-domain rows and one failing verification, so that any leak of cross-domain evidence
    into an in-domain count, rate or mean moves a number this test names.
    """

    template = json.loads(
        (FIXTURE_ROOT / "experience_record" / "complete.json").read_text(encoding="utf-8")
    )
    signature = ContractSignature.model_validate(template["contract_signature"])
    configuration_hash = digest_of("the-configuration-under-consideration")
    other_configuration_hash = digest_of("a-different-configuration")
    moment = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

    in_domain = [
        build(
            ExperienceRecord,
            "complete",
            contract_signature=template["contract_signature"],
            configuration_hash=hash_value,
            created_at=(moment - timedelta(days=days)).isoformat(),
            outcomes={"cost": "2.00", "latency_ms": 1_000, "quality": quality},
        )
        for hash_value, days, quality in (
            (configuration_hash, 1, 0.9),
            (configuration_hash, 3, 0.6),
            (other_configuration_hash, 5, 0.3),
        )
    ]
    cross_domain = [
        build(
            ExperienceRecord,
            "complete",
            contract_signature={**template["contract_signature"], "node_kind": "VERIFIER"},
            configuration_hash=configuration_hash,
            created_at=moment.isoformat(),
            outcomes={"cost": "900.00", "latency_ms": 900_000, "quality": 0.01},
        ),
        build(
            ExperienceRecord,
            "complete",
            contract_signature={
                **template["contract_signature"],
                "risk_class": "PHYSICAL_HIGH",
            },
            configuration_hash=configuration_hash,
            created_at=moment.isoformat(),
            eligible_for_learning=False,
            local_verification_status="FAIL",
            final_run_status="FAIL",
            outcomes={"cost": "900.00", "latency_ms": 900_000, "quality": 0.01},
        ),
    ]
    return signature, configuration_hash, in_domain, cross_domain


def test_feature_schema_digest_is_pinned() -> None:
    """The digest of the ordered schema is a committed constant, so a reorder is a red test."""

    assert feature_schema_digest() == PINNED_FEATURE_SCHEMA_DIGEST

    names = column_names()
    assert len(names) == 81
    assert len(set(names)) == len(names)
    for group, size in EXPECTED_GROUP_SIZES.items():
        assert sum(1 for name in names if name.split(".")[0] == group) == size


def test_featurize_returns_schema_length_rows_with_none_for_missing() -> None:
    """A row is as long as the schema, and an unobserved score arrives absent, not zero."""

    context, candidate, node, objective, _ = setup_featurize_inputs("minimal")
    sparse = featurize(
        context, candidate, node, objective, EvidenceSummary(), Vocabulary()
    )
    assert len(sparse.values) == len(FEATURE_SCHEMA_V1)
    assert sparse.schema_version == FEATURE_SCHEMA_VERSION

    absent = {name for name, value in value_by_name(sparse.values).items() if value is None}
    assert absent == {
        # `TaskFeatures` leaves an unobserved dimension null rather than scoring it zero.
        "task.complexity",
        "task.structure_certainty",
        "task.feedback_dependency",
        "task.dependency_complexity",
        "task.parallelism_potential",
        "task.uncertainty",
        "task.verifier_strength",
        # `ProjectFeatures` does the same for every aggregate over its window.
        "project.mean_complexity",
        "project.mean_uncertainty",
        "project.mean_verifier_strength",
        "project.irreversible_action_rate",
        "project.maximum_risk_rank",
        "project.dominant_expected_horizon_rank",
        # No evidence was retrieved, so there is no rate and no mean — but the counts
        # below are genuinely zero and are therefore present.
        "evidence.verified_success_rate",
        "evidence.mean_quality",
        "evidence.mean_cost",
        "evidence.mean_latency_ms",
        "evidence.recency_days",
    }
    sparse_values = value_by_name(sparse.values)
    assert sparse_values["evidence.n_same_signature"] == 0.0
    assert sparse_values["evidence.n_cross_domain"] == 0.0
    # An empty vocabulary knows no token, so the candidate's model is a stranger.
    assert sparse_values["candidate.model_vocab_index"] == float(OTHER_INDEX)

    # And the same schema over fully populated inputs leaves nothing absent, which is what
    # stops a featurizer that returned `None` unconditionally from passing the block above.
    context, candidate, node, objective, vocab = setup_featurize_inputs("complete")
    _, configuration_hash, in_domain, _ = setup_domain_split_records()
    dense = featurize(
        context,
        candidate,
        node,
        objective,
        summarize_evidence(
            in_domain,
            signature=in_domain[0].contract_signature,
            configuration_hash=configuration_hash,
            as_of=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        ),
        vocab,
    )
    assert len(dense.values) == len(FEATURE_SCHEMA_V1)
    assert [name for name, value in value_by_name(dense.values).items() if value is None] == []
    assert value_by_name(dense.values)["candidate.model_vocab_index"] != float(OTHER_INDEX)


def test_cross_domain_evidence_is_never_counted_into_in_domain_n() -> None:
    """Evidence about another kind of node inflates no in-domain count, rate or mean."""

    signature, configuration_hash, in_domain, cross_domain = setup_domain_split_records()
    as_of = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

    summary = summarize_evidence(
        [*cross_domain, *in_domain],
        signature=signature,
        configuration_hash=configuration_hash,
        as_of=as_of,
    )

    assert summary.n_same_signature == 3
    assert summary.n_same_config == 2
    assert summary.n_cross_domain == 2

    # Every aggregate is exactly what the three in-domain rows alone say. The cross-domain
    # pair carries a 900-second latency and a failing verification, so a summarizer that
    # pooled them could not land on any of these values.
    assert summary.mean_latency_ms == 1_000.0
    assert summary.mean_cost == 2.0
    assert summary.mean_quality == (0.9 + 0.6 + 0.3) / 3
    assert summary.verified_success_rate == 1.0
    assert summary.recency_days == 1.0

    # Dropping the cross-domain rows entirely changes nothing but the ninth number, which
    # is the whole claim: they were retrieved, counted once, and then not used.
    in_domain_only = summarize_evidence(
        in_domain,
        signature=signature,
        configuration_hash=configuration_hash,
        as_of=as_of,
    )
    assert in_domain_only.n_cross_domain == 0
    assert summary == EvidenceSummary(
        n_same_config=in_domain_only.n_same_config,
        n_same_signature=in_domain_only.n_same_signature,
        verified_success_rate=in_domain_only.verified_success_rate,
        mean_quality=in_domain_only.mean_quality,
        mean_cost=in_domain_only.mean_cost,
        mean_latency_ms=in_domain_only.mean_latency_ms,
        recency_days=in_domain_only.recency_days,
        n_contradictions_resolved=in_domain_only.n_contradictions_resolved,
        n_cross_domain=2,
    )
