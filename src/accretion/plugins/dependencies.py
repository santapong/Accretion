from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from accretion.contracts import MetaPluginManifest, PluginConnectorResolution
from accretion.plugins.errors import PluginDependencyError

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_CONSTRAINT = re.compile(r"^(==|>=|<=|!=|>|<|\^|~)?\s*(\d+\.\d+\.\d+)$")

Version = tuple[int, int, int]


def parse_version(value: str) -> Version:
    """Parse a strict ``MAJOR.MINOR.PATCH`` version into a comparable tuple."""

    match = _VERSION.match(value.strip())
    if match is None:
        raise PluginDependencyError(f"{value!r} is not a MAJOR.MINOR.PATCH version")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def satisfies_constraint(version: str, constraint: str) -> bool:
    """Evaluate one constraint, or a comma-joined conjunction of them.

    Deliberately a tuple comparator and not a package manager: exact pins, the five
    ordering operators, ``^`` (compatible-major) and ``~`` (compatible-minor).
    """

    candidate = parse_version(version)
    clauses = [item.strip() for item in constraint.split(",") if item.strip()]
    if not clauses:
        raise PluginDependencyError("empty version constraint")
    return all(_satisfies_clause(candidate, clause) for clause in clauses)


def _satisfies_clause(candidate: Version, clause: str) -> bool:
    match = _CONSTRAINT.match(clause)
    if match is None:
        raise PluginDependencyError(f"{clause!r} is not a supported version constraint")
    operator = match.group(1) or "=="
    target = parse_version(match.group(2))
    if operator == "==":
        return candidate == target
    if operator == "!=":
        return candidate != target
    if operator == ">=":
        return candidate >= target
    if operator == ">":
        return candidate > target
    if operator == "<=":
        return candidate <= target
    if operator == "<":
        return candidate < target
    if operator == "^":
        upper = (target[0] + 1, 0, 0)
        return target <= candidate < upper
    upper = (target[0], target[1] + 1, 0)
    return target <= candidate < upper


def check_constraints(
    requirements: Mapping[str, str],
    available: Mapping[str, str],
) -> None:
    """Raise unless every ``{plugin_id: constraint}`` is met by ``{plugin_id: version}``."""

    for plugin_id, constraint in sorted(requirements.items()):
        version = available.get(plugin_id)
        if version is None:
            raise PluginDependencyError(
                f"required plugin {plugin_id} ({constraint}) is not installed"
            )
        if not satisfies_constraint(version, constraint):
            raise PluginDependencyError(
                f"plugin {plugin_id}@{version} does not satisfy {constraint}"
            )


def validate_acyclic(edges: Mapping[str, Sequence[str]]) -> list[str]:
    """Return a deterministic topological order, or raise naming the cycle.

    Iterative depth-first search so a deep dependency chain cannot exhaust the stack.
    """

    order: list[str] = []
    state: dict[str, int] = {}
    nodes = sorted(set(edges) | {child for children in edges.values() for child in children})

    for root in nodes:
        if state.get(root, 0) == 2:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                if state.get(node, 0) == 2:
                    continue
                if state.get(node, 0) == 1:
                    cycle = path[path.index(node) :] + [node]
                    raise PluginDependencyError(
                        f"plugin dependency cycle: {' -> '.join(cycle)}"
                    )
                state[node] = 1
                path.append(node)
            children = sorted(edges.get(node, ()))
            if index < len(children):
                stack.append((node, index + 1))
                stack.append((children[index], 0))
                continue
            state[node] = 2
            path.pop()
            order.append(node)
    return order


def resolve_connector_requirements(
    manifest: MetaPluginManifest,
    *,
    connections: Mapping[str, str],
    granted_scopes: Mapping[str, Sequence[str]] | None = None,
) -> list[PluginConnectorResolution]:
    """Map declared connectors onto what the workspace actually has.

    A missing *required* connector is a setup gap, not a failure: the manager parks
    the installation in ``SETUP_REQUIRED`` rather than ``FAILED``.
    """

    scopes = {key: frozenset(value) for key, value in (granted_scopes or {}).items()}
    resolutions: list[PluginConnectorResolution] = []
    for requirement, required in (
        *((item, True) for item in manifest.required_connectors),
        *((item, False) for item in manifest.optional_connectors),
    ):
        connection_id = connections.get(requirement.connector_id)
        available = scopes.get(requirement.connector_id, frozenset())
        missing = sorted(set(requirement.scopes) - available)
        resolutions.append(
            PluginConnectorResolution(
                connector_id=requirement.connector_id,
                required=required,
                satisfied=connection_id is not None and not missing,
                connection_id=connection_id,
                missing_scopes=missing,
            )
        )
    return sorted(resolutions, key=lambda item: (not item.required, item.connector_id))


def unsatisfied_required_connectors(
    resolutions: Sequence[PluginConnectorResolution],
) -> list[str]:
    return sorted(item.connector_id for item in resolutions if item.required and not item.satisfied)
