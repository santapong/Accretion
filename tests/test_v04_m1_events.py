"""The twelve SDD v0.4 section-12 event types, declared once (plan finding 4).

``EventType`` is a generated surface: it appears verbatim in ``openapi.json`` and in
``apps/ui/src/api/schema.d.ts``, and CI fails on a diff in the latter. Adding members one
milestone at a time would mean regenerating those artefacts in nine separate PRs across two
lanes, each of which would then conflict with the other lane's regeneration. So all twelve
routing, feedback and router events land here, in M1's first PR, before either lane branches.

Two things are asserted, and the second matters more than the first. That the twelve names
*exist* is what the later milestones need. That the forty-nine members which came before are
unchanged is what everything already persisted needs: an ``events.normalized_type`` column
holds these strings, so renaming or reordering a member is not a refactor, it is a silent
rewrite of history. The golden list is written out here rather than derived from the enum,
because a test that read the table it is checking would pass no matter what the table said.
"""

from __future__ import annotations

import re

from accretion.api.main import SSE_TERMINAL_EVENTS
from accretion.contracts import EventType

EVENT_TYPES_BEFORE_V04: tuple[str, ...] = (
    "RUN_CREATED",
    "WORKFLOW_PROPOSAL_CREATED",
    "WORKFLOW_PROPOSAL_REPAIRED",
    "GRAPH_VALIDATION_STARTED",
    "GRAPH_VALIDATION_RESULT",
    "GRAPH_REVISION_ACTIVATED",
    "REPLAN_REQUESTED",
    "REPLAN_STARTED",
    "REPLAN_COMPLETED",
    "RUNTIME_DECISION",
    "SEARCH_STARTED",
    "SEARCH_CANDIDATE_STARTED",
    "SEARCH_CANDIDATE_COMPLETED",
    "SEARCH_CANDIDATE_PRUNED",
    "SEARCH_SELECTION",
    "SEARCH_PROMOTION_STARTED",
    "SEARCH_PROMOTION_COMPLETED",
    "SEARCH_STOPPED",
    "EXPERIENCE_QUERY",
    "EXPERIENCE_RETRIEVED",
    "TRAJECTORY_REPLAY_STARTED",
    "TRAJECTORY_REPLAY_REJECTED",
    "RUN_STARTED",
    "RUN_PROGRESS",
    "NODE_ENTERED",
    "NODE_EXITED",
    "TOOL_REQUESTED",
    "TOOL_STARTED",
    "TOOL_COMPLETED",
    "TOOL_FAILED",
    "FILE_CHANGED",
    "DIFF_AVAILABLE",
    "APPROVAL_REQUIRED",
    "APPROVAL_RESOLVED",
    "ARTIFACT_CREATED",
    "CHECKPOINT_SAVED",
    "RUNTIME_CALL_STARTED",
    "RUNTIME_CALL_COMPLETED",
    "RUNTIME_CALL_FAILED",
    "RUNTIME_CALL_CANCELLED",
    "LOOP_ITERATION_STARTED",
    "LOOP_ITERATION_COMPLETED",
    "VERIFICATION_STARTED",
    "VERIFICATION_RESULT",
    "RUN_PAUSED",
    "RUN_RESUMED",
    "RUN_COMPLETED",
    "RUN_FAILED",
    "RUN_CANCELLED",
)
"""Every ``EventType`` member as it stood on ``develop`` before this PR, in declaration order."""

SECTION_12_EVENT_TYPES: tuple[str, ...] = (
    "ROUTING_REQUESTED",
    "ROUTING_CANDIDATES_BUILT",
    "ROUTING_DECISION_CREATED",
    "ROUTING_OVERRIDE_RECORDED",
    "ROUTING_FALLBACK_SELECTED",
    "ROUTING_HUMAN_REVIEW_REQUIRED",
    "VERIFICATION_RESULT_RECORDED",
    "EXPERIENCE_CREATED",
    "ROUTER_CANDIDATE_TRAINED",
    "ROUTER_PROMOTION_EVALUATED",
    "ROUTER_VERSION_PROMOTED",
    "ROUTER_VERSION_ROLLED_BACK",
)
"""The twelve SDD v0.4 section-12 events, in the order the plan fixes."""


def test_the_twelve_section_12_event_types_exist() -> None:
    for name in SECTION_12_EVENT_TYPES:
        assert hasattr(EventType, name), f"SDD section 12 names {name}, which EventType lacks"


def test_no_event_type_that_existed_before_v04_changed_its_name_or_value() -> None:
    """Persisted rows hold these strings; a rename is a rewrite of history, not a refactor."""

    members = [member.name for member in EventType]
    assert members[: len(EVENT_TYPES_BEFORE_V04)] == list(EVENT_TYPES_BEFORE_V04)
    for name in EVENT_TYPES_BEFORE_V04:
        assert EventType[name].value == name


def test_every_event_type_value_equals_its_name_in_screaming_snake() -> None:
    for member in EventType:
        assert member.value == member.name
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", member.value)


def test_the_new_members_are_appended_after_run_cancelled_and_nothing_else_is() -> None:
    """Appended, not interleaved: declaration order is what the generated enum serialises."""

    members = [member.name for member in EventType]
    assert members[len(EVENT_TYPES_BEFORE_V04) :] == list(SECTION_12_EVENT_TYPES)


def test_declaring_the_new_events_did_not_widen_the_sse_terminal_set() -> None:
    """A stream that ended on ``ROUTING_REQUESTED`` would truncate every v0.4 run."""

    assert SSE_TERMINAL_EVENTS == {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
    }
