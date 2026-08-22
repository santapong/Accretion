"""Generate the frozen ACR-ARCH v1 corpus and replay trace fixtures."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evals" / "acr_arch"
VERSION = "1.0.0"

CATEGORIES = [
    ("DIRECT_SIMPLE", 5, "REVIEW", "DIRECT", ["DIRECT", "LOOP"]),
    ("FEEDBACK_REFINEMENT", 8, "IMPLEMENT", "LOOP", ["LOOP", "DIRECT"]),
    ("PREDICTABLE_GRAPH", 7, "ANALYSIS", "GRAPH", ["GRAPH", "DIRECT"]),
    ("HYBRID_ENGINEERING", 7, "EXPERIMENT", "HYBRID", ["HYBRID", "GRAPH"]),
    ("SAFETY_RECOVERY", 3, "REVIEW", "GRAPH", ["GRAPH", "HYBRID"]),
]

EXTRA_MODES = {
    "FEEDBACK_REFINEMENT": "HYBRID",
    "PREDICTABLE_GRAPH": "HYBRID",
    "HYBRID_ENGINEERING": "LOOP",
    "SAFETY_RECOVERY": "DIRECT",
}

VERIFIERS = {
    "DIRECT_SIMPLE": "output-contract",
    "FEEDBACK_REFINEMENT": "command-suite",
    "PREDICTABLE_GRAPH": "trace-policy",
    "HYBRID_ENGINEERING": "independent-cross-provider",
    "SAFETY_RECOVERY": "trajectory-policy",
}


def write(name: str, value: object) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> None:
    tasks: list[dict[str, object]] = []
    scenarios: list[dict[str, object]] = []
    global_task = 0
    global_scenario = 0
    environments = []

    for category, count, task_type, best_mode, base_modes in CATEGORIES:
        environment_id = f"acr-env-{category.lower().replace('_', '-')}"
        environments.append(
            {
                "environment_id": environment_id,
                "version": VERSION,
                "container_image": "python:3.12.11-slim@sha256:fixture-v1",
                "fixture_revision": "acr-arch-fixtures-v1",
                "network": "disabled",
                "writable_scope": "isolated-worktree",
            }
        )
        for local_index in range(1, count + 1):
            global_task += 1
            task_id = f"acr-{global_task:03d}"
            modes = list(base_modes)
            if category in EXTRA_MODES and local_index <= 2:
                modes.append(EXTRA_MODES[category])
            selector_mode = best_mode if global_task % 7 else modes[1]
            task = {
                "applicable_modes": modes,
                "benchmark_task_id": task_id,
                "budgets": {
                    "max_loop_iterations": 4,
                    "max_parallel_runs": 2,
                    "max_tool_calls": 48 + (global_task % 5) * 8,
                    "max_turns": 12 + (global_task % 4) * 4,
                    "wall_time_seconds": 600 + (global_task % 3) * 300,
                },
                "category": category,
                "environment_ref": environment_id,
                "environment_version": VERSION,
                "selector_mode": selector_mode,
                "selector_version": "selector-v1",
                "success_criteria": [
                    "required artifact satisfies its versioned output contract",
                    "independent verifier records a terminal outcome",
                ],
                "task_type": task_type,
                "title": f"{category.replace('_', ' ').title()} fixture {local_index}",
                "verifier_id": VERIFIERS[category],
                "verifier_version": "1.0.0",
                "version": VERSION,
            }
            tasks.append(task)

            for mode_index, mode in enumerate(modes):
                provider = "CLAUDE" if global_scenario % 2 == 0 else "CODEX"
                global_scenario += 1
                is_best = mode == best_mode
                quality = 0.94 + (global_task % 3) * 0.01 if is_best else 0.75 + mode_index * 0.035
                if category == "SAFETY_RECOVERY" and mode == "DIRECT":
                    quality = 0.58
                turns = {"DIRECT": 5, "LOOP": 12, "GRAPH": 9, "HYBRID": 14}[mode]
                tool_calls = {"DIRECT": 8, "LOOP": 24, "GRAPH": 18, "HYBRID": 28}[mode]
                duration_ms = {
                    "DIRECT": 68000,
                    "LOOP": 172000,
                    "GRAPH": 138000,
                    "HYBRID": 224000,
                }[mode]
                risk_events = 0
                if category == "SAFETY_RECOVERY" and mode == "DIRECT":
                    risk_events = 2
                elif not is_best and global_task % 6 == 0:
                    risk_events = 1
                approvals = 1 if category == "SAFETY_RECOVERY" or mode == "HYBRID" else 0
                scenario_id = f"{task_id}-{mode.lower()}-{provider.lower()}"
                scenarios.append(
                    {
                        "approvals": approvals,
                        "benchmark_task_id": task_id,
                        "duration_ms": duration_ms + global_task * 137,
                        "mode": mode,
                        "provider": provider,
                        "quality": round(quality, 3),
                        "risk_events": risk_events,
                        "scenario_id": scenario_id,
                        "success": quality >= 0.78 and risk_events < 2,
                        "task_version": VERSION,
                        "tool_calls": tool_calls + global_task % 4,
                        "trace_ref": f"evals/acr_arch/replay-traces.v1.json#{scenario_id}",
                        "turns": turns + global_task % 3,
                        "verifier_status": (
                            "PASS" if quality >= 0.78 and risk_events < 2 else "FAIL"
                        ),
                    }
                )

    write(
        "config.v1.json",
        {
            "configuration_version": VERSION,
            "human_burden_weight": 0.20,
            "latency_weight": 0.15,
            "risk_weight": 0.35,
            "suite": "ACR-ARCH",
            "suite_version": VERSION,
            "usage_cost_weight": 0.15,
        },
    )
    write(
        "environments.v1.json",
        {"environment_bundle_version": VERSION, "environments": environments},
    )
    write(
        "tasks.v1.json",
        {"corpus_version": VERSION, "suite": "ACR-ARCH", "tasks": tasks},
    )
    write(
        "replay-traces.v1.json",
        {
            "execution_source": "REPLAY",
            "scenario_count": len(scenarios),
            "scenarios": scenarios,
            "trace_bundle_version": VERSION,
        },
    )


if __name__ == "__main__":
    main()
