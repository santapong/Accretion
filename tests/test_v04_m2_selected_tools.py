"""Receipt-pinned tools use the real resolver and governed research gateway."""

from __future__ import annotations

import pytest
from test_v03_m5_research import (
    PRINCIPAL,
    QUERY,
    SEARCH,
    WORKSPACE,
    make_run,
    research_stack,
)

from accretion.contracts import CapabilityExecutionStatus, Provider
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import ToolBinding
from accretion.governance import GatewayCapabilityInvoker


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "binding_id", "digest", "version", "tool_id"])
async def test_selected_tool_executes_exact_binding_or_refuses_before_gateway(drift):
    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        resolved = await stack.resolver.resolve(
            SEARCH, principal_id=PRINCIPAL, workspace_id=WORKSPACE
        )
        assert resolved is not None and resolved.binding is not None
        selected = ToolBinding(
            capability={"capability_id": SEARCH, "capability_version": resolved.capability.version},
            tool={
                "tool_id": resolved.binding.backend.tool_name,
                "implementation_digest": content_hash(
                    {"capability": resolved.capability, "binding": resolved.binding},
                    exclude=(),
                ),
            },
            binding_id=resolved.binding.binding_id,
            binding_version=resolved.binding.schema_version,
        )
        if drift == "binding_id":
            selected = selected.model_copy(update={"binding_id": "another-binding"})
        elif drift == "version":
            selected = selected.model_copy(update={"binding_version": "999"})
        elif drift in {"digest", "tool_id"}:
            selected = selected.model_copy(
                update={
                    "tool": selected.tool.model_copy(
                        update={
                            "implementation_digest" if drift == "digest" else "tool_id": "0" * 64
                            if drift == "digest"
                            else "another-tool"
                        }
                    )
                }
            )
        invoker = GatewayCapabilityInvoker(
            resolver=stack.resolver,
            gateway=stack.gateway,
            principal_id=PRINCIPAL,
            workspace_id=WORKSPACE,
        )
        before = await stack.store.list_events(run.run_id)
        if drift:
            with pytest.raises(RuntimeError, match="ROUTED_TOOL_BINDING_DRIFT"):
                await invoker.invoke_selected(
                    run_id=run.run_id,
                    node_id="gather",
                    selected=selected,
                    workspace_id=WORKSPACE,
                    arguments={"query": QUERY},
                    executing_provider=Provider.FAKE,
                )
            assert await stack.store.list_events(run.run_id) == before
        else:
            result = await invoker.invoke_selected(
                run_id=run.run_id,
                node_id="gather",
                selected=selected,
                workspace_id=WORKSPACE,
                arguments={"query": QUERY},
                executing_provider=Provider.FAKE,
            )
            assert result.status is CapabilityExecutionStatus.SUCCEEDED
            assert result.binding_id == selected.binding_id
            assert result.request.capability_version == selected.capability.capability_version
