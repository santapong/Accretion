"""The recovery guard: whose authority, and whether another attempt is justified at all.

SDD §9.7 makes three promises that a guard can break silently, so each is proven here
against decisions the guard actually returned rather than against the table it reads from:

**Authority does not widen.** A failure the router owns is recovered under
``ROUTER_RESELECT``. The property test drives randomly generated signals through the real
classifier, keeps the ones that come back owned by ``CONFIGURATION``, and requires that none
of them ever produces ``HUMAN`` authority — and that while the router's search space is
intact, none of them produces ``PLANNER_REPLAN`` either. The generator deliberately produces
both a single attempted configuration and two distinct ones, and the test asserts both
buckets are non-empty, because a generator that never produced a second distinct hash would
make the escalation rule invisible and the property vacuous. That property is a negative one,
so it has a positive control beside it: a table drives one *really classified* failure per
owner class through the guard and pins the literal ``(action, authority_scope)`` pair each
one gets, which is what stops "no configuration failure reaches ``HUMAN``" from being true
merely because nothing in the suite reaches ``HUMAN`` at all.

**The one escalation has a condition.** Two *distinct* failed configurations on one node
re-type the failure to ``STRUCTURAL``; the same configuration failing twice does not, which
is the difference between "the router has run out of answers" and "the router has been
unlucky twice".

**Recovery stops on either gate independently.** The hard cap is exact — the last permitted
attempt failing means no more attempts, so the comparison is ``>=`` — and the EVI gate is a
*lower bound*: the one-in-twenty case below has a 0.05 point estimate, comfortably over the
0.02 threshold, and stops anyway because the bound is 0.009. The gate has three inputs and
each one stops recovery on its own, so each is varied alone against a reselecting baseline:
the untried fraction, the ``prior_success`` term of ``EVI_v1`` (a node whose configurations
almost never work stops with half its candidates untried), and the caller's ``epsilon``
(inputs that reselect under the default stop under a threshold an operator tightened). And an
attempted configuration never comes back into the eligible set until evidence arrives for it,
which the seeded loop walks candidate by candidate.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from accretion.contracts import PrincipalRef, PrincipalStatus
from accretion.contracts.routing import (
    FailureEvent,
    FailureOwner,
    FailureType,
    ResourceBudget,
    VerificationState,
)
from accretion.feedback.failures import FailureClassifier, FailureRule, FailureSignals
from accretion.feedback.recovery import (
    AUTHORITY_SCOPE_BY_OWNER,
    DEFAULT_EPSILON,
    RecoveryGuard,
)

WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
EXECUTION_INSTANCE_ID = "run_9ZAQAYEBNE6NQ3P27YWG8M082Y"
FIXED_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
SEED = 20260905


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def principal() -> PrincipalRef:
    return PrincipalRef(
        principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
        display_name="v0.4 feedback service",
        status=PrincipalStatus.ACTIVE,
    )


def classify(signals: FailureSignals) -> FailureEvent:
    return FailureClassifier(created_by=principal()).classify(
        signals=signals,
        execution_instance_id=EXECUTION_INSTANCE_ID,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=lambda: FIXED_TIME,
    )


def configuration_failure(*attempted: str) -> FailureEvent:
    """A failure the router owns, carrying the configurations already tried on this node."""

    return classify(
        FailureSignals(
            error_code="RUNTIME_VERSION_DRIFT",
            error_message="runtime pinned 1.4.0, resolved 1.5.2",
            attempted_configuration_hashes=tuple(attempted),
        )
    )


def transient_failure() -> FailureEvent:
    """A retryable failure the *environment* owns.

    The EVI gate governs every reselectable owner, but it can only be *observed* on one that
    the escalation rule leaves alone: a configuration failure that has already burned two
    distinct configurations re-types to structural before the gate is ever consulted, by
    design. A transient provider failure keeps its owner however many configurations have
    been spent, so the stopping arithmetic is visible on its own.
    """

    return classify(
        FailureSignals(error_code="PROVIDER_FAILURE", error_message="upstream 503")
    )


def budget(maximum_attempts: int) -> ResourceBudget:
    return ResourceBudget(
        maximum_cost=Decimal("5.00"),
        maximum_latency_ms=600_000,
        maximum_attempts=maximum_attempts,
        maximum_tool_calls=64,
    )


@pytest.mark.acceptance("AC4-M3-029")
def test_a_configuration_failure_never_yields_planner_or_policy_authority() -> None:
    """SDD §9.7: the router recovers its own failures, and cannot promote itself."""

    assert AUTHORITY_SCOPE_BY_OWNER[FailureOwner.CONFIGURATION] == "ROUTER_RESELECT"

    rng = random.Random(SEED)
    guard = RecoveryGuard()
    codes = [
        "RUNTIME_VERSION_DRIFT",
        "DISPATCH_WITHOUT_RECEIPT",
        "PROVIDER_FAILURE",
        "CAPABILITY_UNKNOWN",
        "NODE_TIMEOUT_EXCEEDED",
        "SOMETHING_NOBODY_HAS_SEEN",
        None,
    ]
    decisions = [
        "ALLOW",
        "DENY",
        "REQUIRE_APPROVAL",
        None,
    ]
    pool = [digest(f"candidate-{index}") for index in range(8)]

    first_failures = 0
    exhausted_failures = 0
    for iteration in range(400):
        attempted = rng.sample(pool, rng.choice([0, 1, 1, 2, 3]))
        signals = FailureSignals(
            error_code=rng.choice(codes),
            error_message=f"generated signal {iteration}",
            local_status=None,
            conflict_count=0,
            policy_decision=rng.choice(decisions),
            schema_findings=rng.choice([0, 0, 1]),
            attempted_configuration_hashes=tuple(attempted),
        )
        failure = classify(signals)
        if failure.assigned_owner is not FailureOwner.CONFIGURATION:
            continue
        decision = guard.decide(
            failure=failure,
            budget=budget(5),
            attempt=rng.randint(1, 4),
            candidate_hashes=pool,
            attempted=attempted,
            new_evidence_since={},
            prior_success=0.8,
        )
        assert decision.authority_scope != "HUMAN", signals
        assert decision.action != "ESCALATE", signals
        if len(set(attempted)) < 2:
            first_failures += 1
            assert decision.owner is FailureOwner.CONFIGURATION, signals
            assert decision.authority_scope == "ROUTER_RESELECT", signals
            assert decision.action in {"RESELECT", "STOP"}, signals
        else:
            # The one documented widening, and it is a re-typing of the failure rather than
            # the router acquiring planner authority for a failure it still owns.
            exhausted_failures += 1
            assert decision.owner is FailureOwner.STRUCTURAL, signals
            assert decision.authority_scope == "PLANNER_REPLAN", signals

    assert first_failures > 0
    assert exhausted_failures > 0


@pytest.mark.acceptance("AC4-M3-029")
def test_each_owner_class_routes_to_the_one_action_and_authority_it_is_assigned() -> None:
    """§9.7's four routes, each driven by a real classified failure through the real guard.

    The property test above states negatives — a configuration failure never reaches ``HUMAN``
    authority, never escalates — and a negative property is also satisfied by a guard that
    never reaches those outcomes for anybody. This is the positive control: every owner the
    rule table can produce is classified for real and put through :meth:`RecoveryGuard.decide`,
    and the expected pair is written out as literals rather than read back out of
    ``AUTHORITY_SCOPE_BY_OWNER``, which would only prove the guard can read its own table.
    """

    guard = RecoveryGuard()
    # Six untried candidates for every row: the EVI gate then clears comfortably, so the row
    # that reselects does so because of its owner and not because of its search space.
    candidates = [digest(f"owner-candidate-{index}") for index in range(6)]

    rows: tuple[tuple[FailureSignals, FailureOwner, str, str, str], ...] = (
        (
            FailureSignals(error_code="RUNTIME_VERSION_DRIFT", error_message="pin drift"),
            FailureOwner.CONFIGURATION,
            "RESELECT",
            "ROUTER_RESELECT",
            "EVI_ABOVE_THRESHOLD",
        ),
        (
            FailureSignals(error_code="CAPABILITY_UNKNOWN", error_message="no binding"),
            FailureOwner.CAPABILITY,
            "RESELECT",
            "ROUTER_RESELECT",
            "EVI_ABOVE_THRESHOLD",
        ),
        (
            FailureSignals(error_code="PROVIDER_FAILURE", error_message="upstream 503"),
            FailureOwner.ENVIRONMENT,
            "RESELECT",
            "ROUTER_RESELECT",
            "EVI_ABOVE_THRESHOLD",
        ),
        (
            FailureSignals(schema_findings=1, error_message="required claim unsatisfiable"),
            FailureOwner.STRUCTURAL,
            "REPLAN",
            "PLANNER_REPLAN",
            "STRUCTURAL_FAILURE_OWNED_BY_PLANNER",
        ),
        (
            FailureSignals(conflict_count=2, error_message="verifiers disagree"),
            FailureOwner.VERIFICATION,
            "RESOLVE_EVIDENCE",
            "EVIDENCE_RESOLUTION",
            "VERIFICATION_CONFLICT_UNRESOLVED",
        ),
        (
            FailureSignals(policy_decision="DENY", error_message="policy refused the call"),
            FailureOwner.AUTHORITY,
            "ESCALATE",
            "HUMAN",
            "HUMAN_AUTHORITY_REQUIRED",
        ),
        (
            FailureSignals(
                local_status=VerificationState.QUARANTINED,
                error_message="quarantined by a human",
            ),
            FailureOwner.SAFETY,
            "ESCALATE",
            "HUMAN",
            "HUMAN_AUTHORITY_REQUIRED",
        ),
        (
            FailureSignals(
                error_code="SOMETHING_NOBODY_HAS_SEEN", error_message="unrecognised"
            ),
            FailureOwner.UNKNOWN,
            "STOP",
            "HUMAN",
            "UNCLASSIFIED_FAILURE",
        ),
    )

    covered: set[FailureOwner] = set()
    for signals, expected_owner, action, scope, reason_code in rows:
        failure = classify(signals)
        assert failure.assigned_owner is expected_owner, signals
        decision = guard.decide(
            failure=failure,
            # A generous budget on the first attempt, so the cap and the EVI gate are both
            # silent and the owner is the only thing deciding the row.
            budget=budget(50),
            attempt=1,
            candidate_hashes=candidates,
            attempted=[],
            new_evidence_since={},
            prior_success=1.0,
        )
        assert decision.owner is expected_owner, signals
        assert decision.action == action, signals
        assert decision.authority_scope == scope, signals
        assert decision.reason_code == reason_code, signals
        covered.add(expected_owner)

    # Every owner the taxonomy can currently assign is exercised above. `RESOURCE` is the one
    # member no rule in `RULES` emits — a budget overrun is reported by the budget holder and
    # never inferred from failure signals — so it has no real classified failure to drive, and
    # this assertion fails the day a rule starts producing one without a row here.
    assert covered == set(FailureOwner) - {FailureOwner.RESOURCE}


@pytest.mark.acceptance("AC4-M3-030")
def test_two_distinct_configuration_failures_on_one_node_escalate_to_structural() -> None:
    """§9.7: when the configuration space is exhausted the node, not the binding, is wrong."""

    guard = RecoveryGuard()
    first = digest("configuration-1")
    second = digest("configuration-2")
    candidates = [first, second, digest("configuration-3")]

    escalated = guard.decide(
        failure=configuration_failure(first, second),
        budget=budget(5),
        attempt=2,
        candidate_hashes=candidates,
        attempted=[first, second],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert escalated.action == "REPLAN"
    assert escalated.owner is FailureOwner.STRUCTURAL
    assert escalated.authority_scope == "PLANNER_REPLAN"
    assert escalated.reason_code == "CONFIGURATION_SPACE_EXHAUSTED"

    single = guard.decide(
        failure=configuration_failure(first),
        budget=budget(5),
        attempt=1,
        candidate_hashes=candidates,
        attempted=[first],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert single.action == "RESELECT"
    assert single.owner is FailureOwner.CONFIGURATION
    assert single.authority_scope == "ROUTER_RESELECT"

    # Distinct configurations, not attempts: the same configuration failing twice is bad
    # luck, and escalating on it would replan a graph that was never the problem.
    repeated = guard.decide(
        failure=configuration_failure(first),
        budget=budget(5),
        attempt=2,
        candidate_hashes=candidates,
        attempted=[first, first],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert repeated.action == "RESELECT"
    assert repeated.owner is FailureOwner.CONFIGURATION


@pytest.mark.acceptance("AC4-M3-031")
def test_hard_cap_and_evi_threshold_each_stop_recovery() -> None:
    """§9.7: automatic recovery continues only while caps remain *and* LCB[EVI] > epsilon."""

    guard = RecoveryGuard()
    tried = digest("configuration-1")
    candidates = [tried, *(digest(f"fresh-{index}") for index in range(5))]
    failure = configuration_failure(tried)

    # One below the cap the same inputs still reselect, so the cap is what stopped it.
    below_cap = guard.decide(
        failure=failure,
        budget=budget(3),
        attempt=2,
        candidate_hashes=candidates,
        attempted=[tried],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert below_cap.action == "RESELECT"
    assert below_cap.evi_lcb is not None and below_cap.evi_lcb > DEFAULT_EPSILON

    # The third attempt of a three-attempt budget failing is the end of the budget: `>=`.
    at_cap = guard.decide(
        failure=failure,
        budget=budget(3),
        attempt=3,
        candidate_hashes=candidates,
        attempted=[tried],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert at_cap.action == "STOP"
    assert at_cap.reason_code == "ATTEMPT_CAP_REACHED"
    assert at_cap.evi_lcb is None
    assert at_cap.authority_scope == "ROUTER_RESELECT"

    # The EVI gate on its own, with the cap far away. Nineteen of twenty candidates are
    # spent: the point estimate is 0.05 and clears the 0.02 threshold, and the lower bound
    # does not. A guard that gated on the point estimate would keep spending here.
    crowd = [digest(f"crowded-{index}") for index in range(20)]
    spent = crowd[:19]
    exhausted = guard.decide(
        failure=transient_failure(),
        budget=budget(50),
        attempt=1,
        candidate_hashes=crowd,
        attempted=spent,
        new_evidence_since={},
        prior_success=1.0,
    )
    point_estimate = 1 / 20 * 1.0
    assert point_estimate > DEFAULT_EPSILON
    assert exhausted.action == "STOP"
    assert exhausted.reason_code == "EVI_BELOW_THRESHOLD"
    assert exhausted.evi_lcb is not None
    assert exhausted.evi_lcb <= DEFAULT_EPSILON < point_estimate
    # Pinned by a number the test wrote, not by the function under test: the Wilson lower
    # bound of 1/20 at z = 1.96.
    assert exhausted.evi_lcb == pytest.approx(0.0088812, abs=1e-6)

    # Halfway through the same twenty, recovery is still worth paying for.
    healthy = guard.decide(
        failure=transient_failure(),
        budget=budget(50),
        attempt=1,
        candidate_hashes=crowd,
        attempted=crowd[:10],
        new_evidence_since={},
        prior_success=1.0,
    )
    assert healthy.action == "RESELECT"
    assert healthy.evi_lcb is not None and healthy.evi_lcb > DEFAULT_EPSILON

    # Third stop, varying only the prior. Same candidates, same spend, same epsilon: a node
    # whose configurations have almost never worked before is not worth another attempt even
    # with half its search space untried. Without the prior term in EVI this node would
    # reselect for as long as candidates remained, which is the unstopped recovery loop.
    hopeless = guard.decide(
        failure=transient_failure(),
        budget=budget(50),
        attempt=1,
        candidate_hashes=crowd,
        attempted=crowd[:10],
        new_evidence_since={},
        prior_success=0.02,
    )
    assert hopeless.action == "STOP"
    assert hopeless.reason_code == "EVI_BELOW_THRESHOLD"
    assert hopeless.evi_lcb is not None and hopeless.evi_lcb <= DEFAULT_EPSILON
    # Both sides are decisions the guard returned, so the ordering is the guard's own
    # arithmetic and not a number this test decided the prior ought to produce.
    assert hopeless.evi_lcb < healthy.evi_lcb

    # Fourth stop, varying only epsilon. §9.7 states the gate as LCB[EVI] > epsilon, so an
    # operator who tightens the threshold must be obeyed: the inputs that reselect under the
    # default stop under a stricter one, and nothing else about the call changed.
    strict = guard.decide(
        failure=transient_failure(),
        budget=budget(50),
        attempt=1,
        candidate_hashes=crowd,
        attempted=crowd[:10],
        new_evidence_since={},
        prior_success=1.0,
        epsilon=0.5,
    )
    assert strict.action == "STOP"
    assert strict.reason_code == "EVI_BELOW_THRESHOLD"
    assert strict.evi_lcb == healthy.evi_lcb


@pytest.mark.acceptance("AC4-M3-032")
def test_an_attempted_hash_is_never_reproposed_without_new_evidence() -> None:
    """§9.7's last rule: equivalent failed configurations do not repeat without new evidence."""

    rng = random.Random(SEED)
    guard = RecoveryGuard()
    candidates = [digest(f"candidate-{index}") for index in range(6)]
    attempted: list[str] = []
    chosen: list[str] = []

    for _ in range(len(candidates)):
        eligible = guard.eligible_candidates(
            candidate_hashes=candidates,
            attempted=attempted,
            new_evidence_since={},
        )
        assert set(eligible).isdisjoint(attempted)
        assert set(eligible) == set(candidates) - set(attempted)
        pick = rng.choice(eligible)
        assert pick not in chosen
        chosen.append(pick)
        attempted.append(pick)

    assert sorted(chosen) == sorted(candidates)

    failure = transient_failure()
    spent = guard.decide(
        failure=failure,
        budget=budget(50),
        attempt=1,
        candidate_hashes=candidates,
        attempted=attempted,
        new_evidence_since={},
        prior_success=1.0,
    )
    assert guard.eligible_candidates(
        candidate_hashes=candidates, attempted=attempted, new_evidence_since={}
    ) == ()
    assert spent.action == "STOP"
    assert spent.reason_code == "EVI_BELOW_THRESHOLD"
    assert spent.evi_lcb == 0.0

    # New evidence about one spent configuration — and only that one — puts it back.
    revived = guard.decide(
        failure=failure,
        budget=budget(50),
        attempt=1,
        candidate_hashes=candidates,
        attempted=attempted,
        new_evidence_since={chosen[3]: 2, chosen[4]: 0},
        prior_success=1.0,
    )
    assert guard.eligible_candidates(
        candidate_hashes=candidates,
        attempted=attempted,
        new_evidence_since={chosen[3]: 2, chosen[4]: 0},
    ) == (chosen[3],)
    assert revived.action == "RESELECT"
    assert revived.evi_lcb is not None and revived.evi_lcb > DEFAULT_EPSILON


def test_duplicate_candidates_and_the_two_unreached_stop_paths() -> None:
    """The dedup branch and both named STOP outcomes are reached, not merely documented."""

    guard = RecoveryGuard()
    candidates = [digest(f"dup-{index}") for index in range(3)]
    assert guard.eligible_candidates(
        candidate_hashes=[candidates[0], candidates[0], candidates[1]],
        attempted=[],
        new_evidence_since={},
    ) == (candidates[0], candidates[1])
    duplicated = guard.decide(
        failure=transient_failure(),
        budget=budget(50),
        attempt=1,
        candidate_hashes=[candidates[0], candidates[0], candidates[1], candidates[2]],
        attempted=[],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert duplicated.evi_lcb is not None and 0.0 <= duplicated.evi_lcb <= 1.0

    nothing_left = guard.decide(
        failure=transient_failure(),
        budget=budget(50),
        attempt=1,
        candidate_hashes=[],
        attempted=[],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert nothing_left.action == "STOP"
    assert nothing_left.reason_code == "NO_CANDIDATE_CONFIGURATIONS"
    assert nothing_left.evi_lcb == 0.0

    # A router-owned failure a rule marks non-retryable stops before any EVI arithmetic.
    no_retry = FailureClassifier(
        created_by=principal(),
        rules=(
            FailureRule(
                rule_id="CONFIGURATION_NO_RETRY",
                matches=lambda signals: True,
                failure_type=FailureType.CONFIGURATION,
                owner=FailureOwner.CONFIGURATION,
                retryable=False,
                confidence=0.9,
            ),
        ),
    ).classify(
        signals=FailureSignals(error_code="RUNTIME_VERSION_DRIFT", error_message="pinned"),
        execution_instance_id=EXECUTION_INSTANCE_ID,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=lambda: FIXED_TIME,
    )
    stopped = guard.decide(
        failure=no_retry,
        budget=budget(50),
        attempt=1,
        candidate_hashes=candidates,
        attempted=[],
        new_evidence_since={},
        prior_success=0.9,
    )
    assert stopped.action == "STOP"
    assert stopped.reason_code == "FAILURE_NOT_RETRYABLE"
