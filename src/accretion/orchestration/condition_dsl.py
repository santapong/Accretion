from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from accretion.orchestration.models import ConditionOperator, TypedCondition

ALLOWED_ROOTS = frozenset(
    {
        "approval",
        "budget",
        "capability",
        "node",
        "runtime",
        "search",
        "verifier",
    }
)
MAX_CONDITION_DEPTH = 8


class ConditionEvaluationError(ValueError):
    pass


def validate_condition(condition: TypedCondition, *, depth: int = 1) -> list[str]:
    problems: list[str] = []
    if depth > MAX_CONDITION_DEPTH:
        return [f"condition nesting exceeds {MAX_CONDITION_DEPTH}"]
    if condition.path is not None:
        root = condition.path.split(".", 1)[0]
        if root not in ALLOWED_ROOTS:
            problems.append(f"condition path root {root!r} is not allowed")
    for operand in condition.operands:
        problems.extend(validate_condition(operand, depth=depth + 1))
    return problems


def evaluate_condition(condition: TypedCondition, state: Mapping[str, Any]) -> bool:
    problems = validate_condition(condition)
    if problems:
        raise ConditionEvaluationError("; ".join(problems))
    operator = condition.operator
    if operator is ConditionOperator.ALL:
        return all(evaluate_condition(item, state) for item in condition.operands)
    if operator is ConditionOperator.ANY:
        return any(evaluate_condition(item, state) for item in condition.operands)
    if operator is ConditionOperator.NOT:
        return not evaluate_condition(condition.operands[0], state)
    actual = _resolve(state, condition.path or "")
    expected = condition.value
    try:
        if operator is ConditionOperator.EQ:
            return bool(actual == expected)
        if operator is ConditionOperator.NE:
            return bool(actual != expected)
        if operator is ConditionOperator.LT:
            return bool(actual < expected)
        if operator is ConditionOperator.LTE:
            return bool(actual <= expected)
        if operator is ConditionOperator.GT:
            return bool(actual > expected)
        if operator is ConditionOperator.GTE:
            return bool(actual >= expected)
        if operator is ConditionOperator.IN:
            return bool(actual in expected)
    except (TypeError, KeyError) as exc:
        raise ConditionEvaluationError(
            f"condition {operator.value} could not compare {actual!r} and {expected!r}"
        ) from exc
    raise ConditionEvaluationError(f"unsupported condition operator {operator.value}")


def _resolve(state: Mapping[str, Any], path: str) -> Any:
    current: Any = state
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ConditionEvaluationError(f"condition path {path!r} is unavailable")
        current = current[part]
    return current
