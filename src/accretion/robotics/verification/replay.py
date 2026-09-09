"""Compare supplied captures; never launch or attest an independent replay."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from fractions import Fraction

from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics import CanonicalWriterEnvelope, TrustedSafetyKey
from accretion.contracts.robotics.values import ReplayClass
from accretion.robotics.errors import RoboticsError
from accretion.robotics.observations import ArtifactReader

from .checks import (
    completeness,
    quaternion_angle,
    recovery_findings,
    safety_findings,
    task_findings,
)
from .reader import FORMATS, EvidenceProblem, LoadedEpisode, load_episode, require
from .types import FieldToleranceV1, RoleFinding, TrustedVerificationBinding


def _within(first: float, second: float, tolerance: FieldToleranceV1) -> bool:
    require(math.isfinite(first) and math.isfinite(second), "REPLAY_NONFINITE")
    # Exact arithmetic prevents overflow or rounded boundary widening.
    return abs(Fraction(first) - Fraction(second)) <= Fraction(tolerance.absolute) + (
        Fraction(tolerance.relative) * abs(Fraction(first))
    )


def compare_loaded(source: LoadedEpisode, replay: LoadedEpisode) -> None:
    """Internal pure comparison after both captures pass construction checks."""
    require(
        source.record.episode_id != replay.record.episode_id
        and source.record.run_id != replay.record.run_id,
        "REPLAY_REUSED_EPISODE",
    )
    require(source.provenance.pins.lease != replay.provenance.pins.lease, "REPLAY_REUSED_LEASE")
    require(
        source.record.seed == replay.record.seed
        and source.provenance.pins.randomization_sample_hash
        == replay.provenance.pins.randomization_sample_hash,
        "REPLAY_SEED_RANDOMIZATION",
    )
    require(
        source.provenance.dependencies == replay.provenance.dependencies
        and source.record.experiment_contract_hash == replay.record.experiment_contract_hash
        and source.descriptor.content_hash == replay.descriptor.content_hash
        and source.configuration == replay.configuration,
        "REPLAY_CLOSURE",
    )
    mode = source.record.replay_class
    require(mode == replay.record.replay_class, "REPLAY_CLASS_SUBSTITUTION")
    require(mode is not ReplayClass.STATISTICAL, "REGISTERED_STATISTICAL_PROTOCOL_REQUIRED")
    require(len(source.frames) == len(replay.frames), "REPLAY_OBSERVATION_COUNT")
    require(len(source.actions) == len(replay.actions), "REPLAY_ACTION_COUNT")
    for source_action, replay_action in zip(source.actions, replay.actions, strict=True):
        require(
            source_action.prepared.command_payload == replay_action.prepared.command_payload
            and source_action.intent.semantic_action == replay_action.intent.semantic_action
            and source_action.intent.target == replay_action.intent.target
            and source_action.intent.constraints == replay_action.intent.constraints
            and [r.status for r in source_action.receipts]
            == [r.status for r in replay_action.receipts],
            "REPLAY_COMMAND_TRACE",
        )
    if mode is ReplayClass.EXACT:
        require(source.geometry == replay.geometry, "REPLAY_EXACT_GEOMETRY")
        require(len(source.safety) == len(replay.safety), "REPLAY_EXACT_SAFETY_COVERAGE")
        for source_row, replay_row in zip(source.safety, replay.safety, strict=True):
            require(
                (
                    source_row.index,
                    source_row.action_sequence,
                    source_row.phase,
                    source_row.interval,
                )
                == (
                    replay_row.index,
                    replay_row.action_sequence,
                    replay_row.phase,
                    replay_row.interval,
                ),
                "REPLAY_EXACT_SAFETY_TRACE",
            )
    profile = source.replay_tolerances
    require(profile == replay.replay_tolerances, "REPLAY_TOLERANCE_SUBSTITUTION")
    tolerances = {value.field: value for value in profile.fields}
    for first, second in zip(source.frames, replay.frames, strict=True):
        require(first.channels.keys() == second.channels.keys(), "REPLAY_CHANNEL_INVENTORY")
        require(first.index.phase == second.index.phase, "REPLAY_PHASE")
        if mode is ReplayClass.TOLERANT:
            require(first.channels.keys() == tolerances.keys(), "REPLAY_TOLERANCE_COVERAGE")
        difference = abs(first.batch.sim_time_ns - second.batch.sim_time_ns)
        require(
            difference <= (0 if mode is ReplayClass.EXACT else profile.time_tolerance_ns),
            "REPLAY_TIME",
        )
        for name, channel in first.channels.items():
            other = second.channels[name]
            require(channel.field == other.field, "REPLAY_FIELD_SCHEMA")
            require(
                abs(channel.sim_time_ns - other.sim_time_ns)
                <= (0 if mode is ReplayClass.EXACT else profile.time_tolerance_ns),
                "REPLAY_SAMPLE_TIME",
            )
            if mode is ReplayClass.EXACT:
                require(channel.raw == other.raw, "REPLAY_EXACT_BYTES")
                continue
            tolerance = tolerances[name]
            if channel.field.modality == "POSE" and channel.field.shape == [4]:
                require(tolerance.mode == "QUATERNION_ANGLE", "REPLAY_QUATERNION_MODE")
                require(
                    quaternion_angle(channel.numbers(), other.numbers()) <= tolerance.absolute,
                    "REPLAY_ORIENTATION_TOLERANCE",
                )
                continue
            require(
                tolerance.mode == "COMPONENT" and tolerance.unit == channel.field.unit,
                "REPLAY_TOLERANCE_UNIT",
            )
            require(len(channel.raw) == len(other.raw), "REPLAY_TENSOR_LENGTH")
            fmt = "<" + FORMATS[channel.field.dtype]
            for (a,), (b,) in zip(
                struct.iter_unpack(fmt, channel.raw),
                struct.iter_unpack(fmt, other.raw),
                strict=True,
            ):
                require(_within(float(a), float(b), tolerance), "REPLAY_FIELD_TOLERANCE")
    first_metrics, second_metrics = task_findings(source), task_findings(replay)
    metric_tolerances = {value.field: value for value in profile.metric_tolerances}
    if mode is ReplayClass.EXACT:
        require(
            canonical_json(first_metrics) == canonical_json(second_metrics), "REPLAY_EXACT_METRICS"
        )
    else:
        require(
            {value.name for value in first_metrics} == metric_tolerances.keys(),
            "REPLAY_METRIC_TOLERANCE_COVERAGE",
        )
        for first_metric, second_metric in zip(first_metrics, second_metrics, strict=True):
            tolerance = metric_tolerances[first_metric.name]
            require(
                first_metric.name == second_metric.name
                and first_metric.unit == second_metric.unit == tolerance.unit
                and tolerance.mode == "COMPONENT",
                "REPLAY_METRIC_SCHEMA",
            )
            require(
                _within(first_metric.value, second_metric.value, tolerance),
                "REPLAY_METRIC_TOLERANCE",
            )


def compare_replay(
    source_original: CanonicalWriterEnvelope,
    source_binding: TrustedVerificationBinding,
    source_artifacts: ArtifactReader,
    replay_original: CanonicalWriterEnvelope,
    replay_binding: TrustedVerificationBinding,
    replay_artifacts: ArtifactReader,
    *,
    trusted_safety_keys: Mapping[str, TrustedSafetyKey],
) -> RoleFinding:
    """A SATISFIED finding means comparison only, not fresh-process execution proof."""
    try:
        source_binding, replay_binding = source_binding.snapshot(), replay_binding.snapshot()
        trusted_safety_keys = dict(trusted_safety_keys)
        source = load_episode(source_original, source_binding, source_artifacts)
        replay = load_episode(replay_original, replay_binding, replay_artifacts)
        for capture, binding in ((source, source_binding), (replay, replay_binding)):
            completeness(capture, trusted_safety_keys)
            safety_findings(capture)
            if (
                capture.configuration.recovery is not None
                or capture.configuration.requires_fresh_episode_recovery
            ):
                recovery_findings(capture, binding)
        compare_loaded(source, replay)
    except (EvidenceProblem, RoboticsError, ValueError, TypeError) as error:
        reason = (
            error.reason
            if isinstance(error, EvidenceProblem)
            else (
                error.code.value if isinstance(error, RoboticsError) else "INVALID_REPLAY_EVIDENCE"
            )
        )
        return RoleFinding(
            role="REPLAY",
            status="INCONCLUSIVE"
            if reason
            in {
                "REGISTERED_STATISTICAL_PROTOCOL_REQUIRED",
                "ARTIFACT_UNAVAILABLE",
                "RESOURCE_CAP_EXHAUSTED",
                "UNSUPPORTED_REQUIRED_VERIFIER",
                "FRESH_RECOVERY_LINK_REQUIRED",
                "TASK_CHANNEL_MISSING",
                "ACTUAL_SAFETY_COVERAGE_MISSING",
            }
            else "VIOLATED",
            reasons=(reason,),
        )
    return RoleFinding(
        role="REPLAY", status="SATISFIED", reasons=("SUPPLIED_CAPTURE_COMPARISON_ONLY",)
    )
