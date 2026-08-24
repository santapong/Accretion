from __future__ import annotations

from accretion.contracts import Provider, RuntimeHealth, RuntimeStatus, UsagePressure
from accretion.ids import new_id
from accretion.orchestration.models import RuntimeCandidate, RuntimeDecision

_PRESSURE = {
    UsagePressure.UNKNOWN: 0.5,
    UsagePressure.LOW: 0.0,
    UsagePressure.MEDIUM: 0.5,
    UsagePressure.HIGH: 0.8,
    UsagePressure.EXHAUSTED: 1.0,
}


class PerformanceAwareRuntimeRouter:
    version = "performance-router-v2"

    def decide(
        self,
        *,
        run_id: str,
        node_id: str,
        health: list[RuntimeHealth],
        historical_quality: dict[tuple[Provider, str], float],
        expected_latency: dict[Provider, float] | None = None,
        specialization_fit: dict[Provider, float] | None = None,
        risk_penalty: dict[Provider, float] | None = None,
    ) -> RuntimeDecision:
        latency = expected_latency or {}
        specialization = specialization_fit or {}
        risk = risk_penalty or {}
        candidates: list[RuntimeCandidate] = []
        for item in health:
            available = item.status in {RuntimeStatus.READY, RuntimeStatus.BUSY}
            quality = historical_quality.get((item.provider, item.runtime_version), 0.5)
            pressure = _PRESSURE[item.observed_usage_pressure]
            latency_value = latency.get(item.provider, 0.5)
            risk_value = risk.get(item.provider, 0.0)
            specialization_value = specialization.get(item.provider, 0.5)
            score = (
                0.35 * quality
                + 0.25 * float(available)
                + 0.15 * specialization_value
                - 0.10 * pressure
                - 0.10 * latency_value
                - 0.05 * risk_value
            )
            candidates.append(
                RuntimeCandidate(
                    provider=item.provider,
                    runtime_version=item.runtime_version,
                    available=available,
                    historical_quality=quality,
                    usage_pressure=item.observed_usage_pressure,
                    expected_latency=latency_value,
                    risk_penalty=risk_value,
                    specialization_fit=specialization_value,
                    score=round(score, 6),
                    exclusion_reason=None
                    if available
                    else f"runtime status is {item.status.value}",
                )
            )
        ranked = sorted(
            (candidate for candidate in candidates if candidate.available),
            key=lambda candidate: (-candidate.score, candidate.provider.value),
        )
        selected = ranked[0].provider if ranked else None
        reason = (
            f"selected {selected.value} using version-keyed observable evidence"
            if selected is not None
            else "no compatible runtime is available"
        )
        return RuntimeDecision(
            decision_id=new_id("runtime_decision"),
            run_id=run_id,
            node_id=node_id,
            candidates=candidates,
            selected_runtime=selected,
            selected_reason=reason,
            fallback_order=[candidate.provider for candidate in ranked[1:]],
            observed_features={"candidate_count": len(candidates)},
        )
