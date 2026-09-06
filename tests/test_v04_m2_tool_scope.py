"""Workspace-isolation regressions for receipt-pinned tool execution."""

from __future__ import annotations

from typing import cast

import pytest
from test_v03_m5_research import (
    PRINCIPAL,
    QUERY,
    SEARCH,
    WORKSPACE,
    make_run,
    research_stack,
)

from accretion.contracts import Connection, WorkspaceEntity
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import ToolBinding
from accretion.governance import GatewayCapabilityInvoker
from accretion.resolver import CapabilityResolver

FOREIGN_WORKSPACE = "wks_000_foreign"
FOREIGN_CONNECTION = "con_000_foreign_sorts_first"


async def _selected_binding(resolver: CapabilityResolver) -> tuple[ToolBinding, str]:
    resolved = await resolver.resolve(
        SEARCH,
        principal_id=PRINCIPAL,
        workspace_id=WORKSPACE,
    )
    assert resolved is not None
    assert resolved.binding is not None
    assert resolved.connection is not None
    return (
        ToolBinding(
            capability={
                "capability_id": SEARCH,
                "capability_version": resolved.capability.version,
            },
            tool={
                "tool_id": resolved.binding.backend.tool_name,
                "implementation_digest": content_hash(
                    {
                        "capability": resolved.capability,
                        "binding": resolved.binding,
                    },
                    exclude=(),
                ),
            },
            binding_id=resolved.binding.binding_id,
            binding_version=resolved.binding.schema_version,
        ),
        resolved.connection.connection_id,
    )


@pytest.mark.asyncio
async def test_production_constructor_uses_run_owner_and_requested_workspace() -> None:
    """An earlier-sorting foreign connection must never serve the selected tool."""

    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        selected, local_connection_id = await _selected_binding(stack.resolver)
        local_connection = await stack.store.get_connection(local_connection_id)
        assert local_connection is not None
        await stack.store.upsert_workspace(
            WorkspaceEntity(workspace_id=FOREIGN_WORKSPACE, name="Foreign workspace")
        )
        await stack.store.upsert_connection(
            Connection.model_validate(
                {
                    **local_connection.model_dump(mode="python"),
                    "connection_id": FOREIGN_CONNECTION,
                    "workspace_id": FOREIGN_WORKSPACE,
                }
            )
        )

        invoker = GatewayCapabilityInvoker(
            resolver=stack.resolver,
            gateway=stack.gateway,
        )
        assert invoker.principal_id is None
        assert invoker.workspace_id is None

        result = await invoker.invoke_selected(
            run_id=run.run_id,
            node_id="gather",
            selected=selected,
            workspace_id=WORKSPACE,
            arguments={"query": QUERY},
        )

        assert result.connection_id == local_connection_id
        assert result.connection_id != FOREIGN_CONNECTION
        assert result.binding_id == selected.binding_id


class ResolverMustNotRun:
    async def resolve(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("workspace authorization must precede capability resolution")


@pytest.mark.asyncio
async def test_foreign_requested_workspace_is_rejected_before_resolution() -> None:
    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        selected, _ = await _selected_binding(stack.resolver)
        await stack.store.upsert_workspace(
            WorkspaceEntity(workspace_id=FOREIGN_WORKSPACE, name="Foreign workspace")
        )
        invoker = GatewayCapabilityInvoker(
            resolver=cast(CapabilityResolver, ResolverMustNotRun()),
            gateway=stack.gateway,
        )

        with pytest.raises(PermissionError, match="ROUTED_TOOL_UNAUTHORIZED"):
            await invoker.invoke_selected(
                run_id=run.run_id,
                node_id="gather",
                selected=selected,
                workspace_id=FOREIGN_WORKSPACE,
                arguments={"query": QUERY},
            )

        assert await stack.store.list_events(run.run_id) == []
