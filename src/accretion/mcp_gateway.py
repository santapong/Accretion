from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from accretion.config import get_settings
from accretion.contracts import (
    CapabilityExecutionStatus,
    CapabilityRequest,
    CapabilityResolutionOutcome,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    default_capability_handlers,
    seed_governance,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.side_effects import PostgresSideEffectLedger
from accretion.persistence.store import PostgresStore, StateStore
from accretion.redaction import redact_text
from accretion.resolver import CapabilityResolver
from accretion.secrets_store import EnvelopeSecretStore
from accretion.token_broker import EncryptedTokenBroker

MCP_PROTOCOL_VERSION = "2025-06-18"


def _response(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": redact_text(message)},
    }


class StdioMcpGateway:
    def __init__(
        self,
        gateway: CapabilityGateway,
        store: StateStore,
        run_id: str,
        principal_id: str | None = None,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.run_id = run_id
        # Resolution is per principal: a USER-scoped connection is invisible without
        # one (INV3-008), so a gateway that resolves anonymously can never spend a
        # connector credential.
        self.principal_id = principal_id
        self.resolver = CapabilityResolver(store)

    async def dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None:
            return None
        if method == "initialize":
            return _response(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "accretion-governed-gateway", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _response(request_id, {})
        if method == "tools/list":
            run = await self.store.get_run(self.run_id)
            if run is None:
                return _error(request_id, -32001, "gateway run was not found")
            task = await self.store.get_task(run.task_id)
            if task is None:
                return _error(request_id, -32001, "gateway task was not found")
            allowed = set(task.envelope.allowed_capabilities)
            denied = set(task.envelope.denied_capabilities)
            tools = [
                {
                    "name": resolved.capability.capability_id,
                    "description": resolved.capability.description,
                    "inputSchema": resolved.capability.input_schema,
                    "annotations": {
                        "readOnlyHint": not bool(resolved.capability.side_effects),
                        "destructiveHint": bool(resolved.capability.side_effects),
                        "idempotentHint": resolved.capability.idempotency.value != "NONE",
                    },
                }
                for resolved in await self.resolver.list_resolved()
                if resolved.outcome
                in {
                    CapabilityResolutionOutcome.OK,
                    CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED,
                }
                and resolved.capability.capability_id in allowed
                and resolved.capability.capability_id not in denied
            ]
            return _response(request_id, {"tools": tools})
        if method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict):
                return _error(request_id, -32602, "tools/call params must be an object")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _error(request_id, -32602, "tool name and arguments are required")
            declared_reason = arguments.pop(
                "_declared_reason", f"Provider requested {name} through Accretion"
            )
            idempotency_key = arguments.pop("_idempotency_key", None)
            resolved = await self.resolver.resolve(name, principal_id=self.principal_id)
            if resolved is not None and resolved.outcome not in {
                CapabilityResolutionOutcome.OK,
                CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED,
            }:
                # Fail closed on unusable connections without leaking credentials.
                return _error(
                    request_id,
                    -32002,
                    f"capability {name} is not resolvable: "
                    f"{resolved.outcome.value} ({resolved.reason})",
                )
            capability = resolved.capability if resolved else None
            version = capability.version if capability else "unknown"
            request = CapabilityRequest(
                request_id=new_id("capability_request"),
                run_id=self.run_id,
                node_id=os.getenv("ACCRETION_GATEWAY_NODE_ID", "gateway"),
                capability_id=name,
                capability_version=version,
                arguments=arguments,
                declared_reason=str(declared_reason),
                idempotency_key=str(idempotency_key) if idempotency_key else None,
            )
            try:
                # The resolver already chose the connection; handing it to the gateway
                # is what lets a connector-backed capability spend a token at all.
                result = await self.gateway.execute(
                    request, resolved.connection if resolved else None
                )
            except Exception as exc:
                return _error(request_id, -32000, str(exc))
            serialized = result.model_dump(mode="json")
            return _response(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(serialized, separators=(",", ":")),
                        }
                    ],
                    "structuredContent": serialized,
                    "isError": result.status
                    not in {
                        CapabilityExecutionStatus.SUCCEEDED,
                        CapabilityExecutionStatus.REQUIRES_APPROVAL,
                    },
                },
            )
        return _error(request_id, -32601, f"unsupported MCP method {method!r}")


async def _serve() -> None:
    run_id = os.getenv("ACCRETION_GATEWAY_RUN_ID", "")
    if not run_id:
        raise RuntimeError("ACCRETION_GATEWAY_RUN_ID is required")
    settings = get_settings()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    await seed_governance(store)
    gateway = CapabilityGateway(
        store=store,
        side_effects=PostgresSideEffectLedger(sessions),
        broker=CredentialBroker(settings.credential_env_map),
        executor=CapabilityExecutor(default_capability_handlers()),
        policy_engine=CapabilityPolicyEngine(set(settings.granted_permissions)),
        policy_id=settings.capability_policy_id,
        token_broker=EncryptedTokenBroker(store, EnvelopeSecretStore())
        if settings.token_encryption_key
        else None,
    )
    server = StdioMcpGateway(
        gateway, store, run_id, os.getenv("ACCRETION_GATEWAY_PRINCIPAL_ID") or None
    )
    try:
        while raw := await asyncio.to_thread(sys.stdin.buffer.readline):
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                response = await server.dispatch(message)
            except Exception as exc:
                response = _error(None, -32700, str(exc))
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
