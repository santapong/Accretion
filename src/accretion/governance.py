from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jsonschema import ValidationError, validate

from accretion.contracts import (
    RISK_RANK,
    ApprovalRecord,
    ApprovalStatus,
    AuthorizationOutcome,
    Capability,
    CapabilityAuthorization,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
    CapabilityKind,
    CapabilityPolicy,
    CapabilityRequest,
    CapabilityResolutionOutcome,
    Connection,
    ConnectionRef,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    CredentialReference,
    ErrorSummary,
    EventType,
    EvidenceCandidate,
    EvidenceRecord,
    EvidenceTrust,
    IdempotencyMode,
    MetaPlugin,
    MetaSkill,
    PrincipalStatus,
    Provider,
    RiskLevel,
    Task,
)
from accretion.contracts.canonical import canonical_json
from accretion.digests import legacy_json_digest
from accretion.ids import new_id
from accretion.persistence.side_effects import SideEffectLedger, SideEffectStatus
from accretion.persistence.store import StateStore
from accretion.redaction import redact, redact_text, scrub_values
from accretion.research.transforms import (
    CANDIDATES_KEY,
    TransformContext,
    TransformRegistry,
    request_query,
)
from accretion.resolver import CapabilityResolver
from accretion.runtimes.common import make_event
from accretion.token_broker import TokenBroker, TokenBrokerError

if TYPE_CHECKING:
    from accretion.mcp.manager import RemoteMcpManager

CapabilityHandler = Callable[[dict[str, Any], Mapping[str, str]], Awaitable[dict[str, Any]]]
EventNotifier = Callable[[str], Awaitable[None]]


class CapabilityInputError(ValueError):
    pass


class CredentialUnavailableError(RuntimeError):
    pass


class CredentialBroker:
    """Resolve secret references only at the executor boundary."""

    def __init__(
        self,
        source_env_by_ref: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.source_env_by_ref = dict(source_env_by_ref or {})
        self._environment = environment if environment is not None else os.environ

    def availability(self, credential_ref: str) -> CredentialReference:
        source = self.source_env_by_ref.get(credential_ref)
        return CredentialReference(
            credential_ref=credential_ref,
            available=bool(source and self._environment.get(source)),
        )

    def resolve(self, credential_refs: list[str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for credential_ref in credential_refs:
            source = self.source_env_by_ref.get(credential_ref)
            value = self._environment.get(source, "") if source else ""
            if not value:
                raise CredentialUnavailableError(
                    f"credential reference {credential_ref!r} is unavailable"
                )
            resolved[credential_ref] = value
        return resolved


class CapabilityPolicyEngine:
    def __init__(self, granted_permissions: set[str] | None = None) -> None:
        self.granted_permissions = granted_permissions or set()

    def authorize(
        self,
        *,
        task: Task,
        capability: Capability,
        request: CapabilityRequest,
        policy: CapabilityPolicy,
        approval: ApprovalRecord | None = None,
    ) -> CapabilityAuthorization:
        def decision(outcome: AuthorizationOutcome, reason: str) -> CapabilityAuthorization:
            return CapabilityAuthorization(
                outcome=outcome,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                reason=reason,
                approval_id=approval.approval_id if approval else None,
            )

        if not capability.enabled:
            return decision(AuthorizationOutcome.DENY, "capability is disabled")
        if capability.capability_id in policy.explicitly_denied:
            return decision(AuthorizationOutcome.DENY, "capability is denied by policy")
        if capability.capability_id in task.envelope.denied_capabilities:
            return decision(AuthorizationOutcome.DENY, "capability is denied by the task")
        if capability.capability_id not in task.envelope.allowed_capabilities:
            return decision(AuthorizationOutcome.DENY, "capability is not allowed by the task")
        missing = sorted(set(capability.required_permissions) - self.granted_permissions)
        if missing:
            return decision(
                AuthorizationOutcome.DENY,
                f"missing operator permissions: {', '.join(missing)}",
            )
        if capability.side_effects and capability.idempotency is IdempotencyMode.NONE:
            return decision(
                AuthorizationOutcome.DENY,
                "side-effecting capabilities must declare idempotency",
            )
        if capability.side_effects and not request.idempotency_key:
            return decision(
                AuthorizationOutcome.DENY,
                "side-effecting capability request requires an idempotency key",
            )
        protected = bool(capability.side_effects) or (
            RISK_RANK[capability.risk] >= RISK_RANK[policy.require_approval_at_risk]
        ) or RISK_RANK[task.envelope.risk_level] >= RISK_RANK[policy.require_approval_at_risk]
        if not protected:
            return decision(AuthorizationOutcome.ALLOW, "explicitly allowed low-risk capability")
        if approval is None or approval.status is ApprovalStatus.PENDING:
            return decision(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                "protected capability requires a content-bound operator approval",
            )
        if approval.status is not ApprovalStatus.APPROVED:
            return decision(AuthorizationOutcome.DENY, "operator did not approve the request")
        return decision(AuthorizationOutcome.ALLOW, "content-bound operator approval is recorded")


class CapabilityExecutor:
    def __init__(self, handlers: Mapping[str, CapabilityHandler] | None = None) -> None:
        self.handlers = dict(handlers or {})

    async def execute(
        self,
        capability: Capability,
        arguments: dict[str, Any],
        credentials: Mapping[str, str],
    ) -> dict[str, Any]:
        if capability.backend is CapabilityBackend.PYTHON:
            handler = self.handlers.get(capability.capability_id)
            if handler is None:
                raise RuntimeError(
                    f"no allowlisted Python handler for {capability.capability_id}"
                )
            return await handler(arguments, credentials)
        if capability.backend is CapabilityBackend.CLI:
            return await self._execute_cli(capability, arguments, credentials)
        raise RuntimeError(
            f"backend {capability.backend.value} is not executable by the v0.1 gateway"
        )

    @staticmethod
    async def _execute_cli(
        capability: Capability,
        arguments: dict[str, Any],
        credentials: Mapping[str, str],
    ) -> dict[str, Any]:
        execution = capability.provider_projections.get("accretion", {})
        command = execution.get("command") if isinstance(execution, dict) else None
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise RuntimeError("CLI capability does not declare a fixed argv command")
        credential_env = execution.get("credential_env", {})
        if not isinstance(credential_env, dict):
            raise RuntimeError("CLI capability credential_env must be an object")
        child_env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TMPDIR"}
        }
        for credential_ref, value in credentials.items():
            target = credential_env.get(credential_ref)
            if not isinstance(target, str) or not target:
                raise RuntimeError(f"no child environment target for {credential_ref}")
            child_env[target] = value
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
        )
        stdout, stderr = await process.communicate(
            json.dumps(arguments, separators=(",", ":")).encode()
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"capability process failed with code {process.returncode}: "
                f"{redact_text(stderr.decode(errors='replace')[-2000:])}"
            )
        decoded = json.loads(stdout.decode() or "{}")
        if not isinstance(decoded, dict):
            raise RuntimeError("capability process output must be a JSON object")
        return decoded


SOURCE_IDS_KEY = "source_ids"
"""Canonical key a normalized capability output uses to name the sources it cited."""


def _output_source_ids(output: dict[str, Any] | None) -> list[str]:
    """Read the source identifiers a capability's normalized output declares.

    Capability output is untrusted, so only a list of non-empty strings under the
    canonical key is accepted and anything else yields nothing. Capabilities that
    declare no sources — every capability that predates v0.3 M5 — yield an empty
    list, which is why the field could be added without touching a call site.
    """

    if not isinstance(output, dict):
        return []
    declared = output.get(SOURCE_IDS_KEY)
    if not isinstance(declared, list):
        return []
    return [item for item in declared if isinstance(item, str) and item]


def approval_binding(request: CapabilityRequest) -> str:
    bound = {
        "run_id": request.run_id,
        "node_id": request.node_id,
        "capability_id": request.capability_id,
        "capability_version": request.capability_version,
        "arguments": request.arguments,
        "idempotency_key": request.idempotency_key,
    }
    # Byte-frozen, not converged (M8). ``arguments`` is arbitrary caller-supplied JSON,
    # and this digest is persisted as an approval's ``native_request_id``: hashing a
    # non-ASCII argument as itself rather than as ``\uXXXX`` would orphan every approval
    # an earlier release recorded. See :mod:`accretion.digests`.
    return f"capability:{legacy_json_digest(bound)}"


class CapabilityGateway:
    adapter_version = "accretion-mcp-gateway-v1"

    def __init__(
        self,
        *,
        store: StateStore,
        side_effects: SideEffectLedger,
        broker: CredentialBroker,
        executor: CapabilityExecutor,
        policy_engine: CapabilityPolicyEngine,
        policy_id: str = "local-capability-policy",
        notify: EventNotifier | None = None,
        token_broker: TokenBroker | None = None,
        remote_mcp: RemoteMcpManager | None = None,
        transforms: TransformRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.side_effects = side_effects
        self.broker = broker
        self.executor = executor
        self.policy_engine = policy_engine
        self.policy_id = policy_id
        self.notify = notify
        # The Token Broker is the sole credential authority for connector-backed
        # capabilities (ADR3-004). The env-var CredentialBroker above still serves
        # v0.1-style `credential_refs` and is deliberately left in place.
        self.token_broker = token_broker
        self.remote_mcp = remote_mcp
        # SDD 7.6's per-binding transform seam. ``None`` means no binding may name
        # a transform, which is exactly the pre-v0.3-M5 behaviour.
        self.transforms = transforms
        # Injectable so evidence timestamps come from one place and a test can
        # bracket ``retrieved_at`` without patching the clock globally.
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _connector_needs_credential(self, connection: ConnectionRef) -> bool:
        """Whether the resolved connection's connector declares a credential at all.

        Some connectors are credential-free by definition --- a local MCP adapter, for
        instance --- and have no token handle to mint from. Demanding one would make
        them unexecutable, so the question is asked of the *connector definition*,
        which is exactly the test ``RemoteMcpManager.execute`` already applies before
        deciding whether a missing token is an authorization failure.

        Fails closed in both directions that matter: an unknown connector is treated
        as needing a credential, and a connector declaring any auth type other than
        ``NONE`` still goes through the Token Broker.
        """

        connector = await self.store.get_connector_definition(connection.connector_id)
        return connector is None or connector.auth_type is not ConnectorAuthType.NONE

    async def _connection_credentials(
        self, connection: ConnectionRef, capability: Capability
    ) -> dict[str, str]:
        """Mint short-lived access material for a connector-backed capability.

        The plaintext exists only in the returned mapping, which reaches the executor
        and nothing else: it is never persisted, never redacted into an event, and
        never returned to the agent (INV3-003).
        """

        if self.token_broker is None:
            raise CredentialUnavailableError(
                f"capability {capability.capability_id} needs connection "
                f"{connection.connection_id} but no token broker is configured"
            )
        stored = await self.store.get_connection(connection.connection_id)
        if stored is None or stored.token_handle_ref is None:
            raise CredentialUnavailableError(
                f"connection {connection.connection_id} holds no credential"
            )
        handle = await self.store.get_token_handle(stored.token_handle_ref)
        if handle is None:
            raise CredentialUnavailableError(
                f"connection {connection.connection_id} references a missing token handle"
            )
        connector = await self.store.get_connector_definition(stored.connector_id)
        try:
            material = await self.token_broker.get_access_material(
                handle,
                audience=[connector.resource_server]
                if connector and connector.resource_server
                else [],
                scopes=list(stored.granted_scopes),
                expected_issuer=connector.authorization_server if connector else None,
            )
        except TokenBrokerError as exc:
            # Fail closed: an unusable credential must not fall through to an
            # unauthenticated call.
            raise CredentialUnavailableError(str(exc)) from exc
        return {f"connection:{stored.connector_id}": material.reveal()}

    async def execute(
        self,
        request: CapabilityRequest,
        connection: ConnectionRef | None = None,
        binding: CapabilityBinding | None = None,
        *,
        executing_provider: Provider | None = None,
    ) -> CapabilityExecutionResult:
        # Who gets named in the authorization terminals and the audit events. The run
        # carries the *requested* provider; the provider that actually executed the
        # node is a property of that node's session, which only the scheduler knows.
        # Callers that hold no session -- the MCP gateway, the API, search, experience
        # -- pass nothing and keep naming ``run.provider``, which is what every one of
        # them named before this parameter existed.
        # The resolved connection and binding say which backend actually served the
        # call. They were previously read for credentials and then dropped, which
        # left the stored result unable to name its own connector.
        connector_id = (
            connection.connector_id
            if connection is not None
            else (binding.connector_id if binding is not None else None)
        )
        binding_id = binding.binding_id if binding is not None else None
        connection_id = connection.connection_id if connection is not None else None
        run = await self.store.get_run(request.run_id)
        if run is None:
            raise KeyError(request.run_id)
        provider = executing_provider if executing_provider is not None else run.provider
        task = await self.store.get_task(run.task_id)
        if task is None:
            raise KeyError(run.task_id)
        # AC3-ID-05. The HTTP boundary gates route access, but capability invocation
        # happens in the gateway subprocess, which authenticates nobody. An in-flight
        # run must stop spending authority the moment its owner is disabled.
        if run.principal_id:
            owner = await self.store.get_principal(run.principal_id)
            if owner is None or owner.status is PrincipalStatus.DISABLED:
                raise PermissionError(
                    f"principal {run.principal_id} may not invoke capabilities"
                )
        capability = await self.store.get_capability(
            request.capability_id, request.capability_version
        )
        policy = await self.store.get_capability_policy(self.policy_id)
        if policy is None:
            raise RuntimeError(f"capability policy {self.policy_id!r} is unavailable")
        if capability is None:
            authorization = CapabilityAuthorization(
                outcome=AuthorizationOutcome.DENY,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                reason="unknown or unversioned capability",
            )
            return await self._terminal(
                provider,
                request,
                authorization,
                CapabilityExecutionStatus.DENIED,
                ErrorSummary(code="CAPABILITY_UNKNOWN", message=authorization.reason),
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
            )
        try:
            validate(instance=request.arguments, schema=capability.input_schema)
        except ValidationError as exc:
            raise CapabilityInputError(redact_text(exc.message)) from exc

        approval = await self._approval_for(request)
        authorization = self.policy_engine.authorize(
            task=task,
            capability=capability,
            request=request,
            policy=policy,
            approval=approval,
        )
        await self._event(
            provider,
            request,
            EventType.TOOL_REQUESTED,
            "accretion/capability-requested",
            {"authorization": authorization.model_dump(mode="json")},
        )
        if authorization.outcome is AuthorizationOutcome.DENY:
            return await self._terminal(
                provider,
                request,
                authorization,
                CapabilityExecutionStatus.DENIED,
                ErrorSummary(code="CAPABILITY_DENIED", message=authorization.reason),
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
            )
        if authorization.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
            approval = await self._ensure_approval(request)
            authorization = authorization.model_copy(update={"approval_id": approval.approval_id})
            result = CapabilityExecutionResult(
                request=request,
                authorization=authorization,
                status=CapabilityExecutionStatus.REQUIRES_APPROVAL,
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
            )
            await self.store.save_capability_result(result)
            if not await self._approval_event_exists(request.run_id, approval.approval_id):
                await self._event(
                    provider,
                    request,
                    EventType.APPROVAL_REQUIRED,
                    "accretion/capability-approval-required",
                    {
                        "approval_id": approval.approval_id,
                        "capability_id": request.capability_id,
                    },
                )
            return result

        credentials = self.broker.resolve(capability.credential_refs)
        if connection is not None and await self._connector_needs_credential(connection):
            # AC3-SEC-03: connector-backed capabilities take their credential from the
            # broker, never from the resolver, the request, or the agent.
            try:
                connection_credentials = await self._connection_credentials(
                    connection, capability
                )
            except CredentialUnavailableError:
                if (
                    capability.backend is CapabilityBackend.MCP
                    and binding is not None
                    and self.remote_mcp is not None
                ):
                    await self.remote_mcp.mark_auth_required(
                        binding, connection, correlation_id=request.request_id
                    )
                raise
            credentials = {**credentials, **connection_credentials}
        operation_id: str | None = None
        if capability.side_effects:
            assert request.idempotency_key is not None
            operation, created = await self.side_effects.record_intent(
                run_id=request.run_id,
                idempotency_key=request.idempotency_key,
                capability_id=request.capability_id,
                payload=redact(request.arguments),
            )
            operation_id = operation.operation_id
            if not created:
                return await self._duplicate_result(
                    provider,
                    request,
                    authorization,
                    operation,
                    connector_id=connector_id,
                    binding_id=binding_id,
                    connection_id=connection_id,
                )
        executing = CapabilityExecutionResult(
            request=request,
            authorization=authorization,
            status=CapabilityExecutionStatus.EXECUTING,
            side_effect_operation_id=operation_id,
            connector_id=connector_id,
            binding_id=binding_id,
            connection_id=connection_id,
        )
        await self.store.save_capability_result(executing)
        await self._event(
            provider,
            request,
            EventType.TOOL_STARTED,
            "accretion/capability-started",
            {"side_effect_operation_id": operation_id},
        )
        try:
            # SDD 7.6: the canonical arguments have already been validated against the
            # capability's own input_schema above, so the canonical schema stays
            # authoritative and the transform maps -- it cannot widen or bypass.
            arguments = self._transform_input(binding, request)
            # Scrub by value as well as by key: a capability can return its own
            # injected credential under a harmless key, which key-name redaction
            # cannot see (AC3-SEC-02).
            if capability.backend is CapabilityBackend.MCP:
                if binding is None or self.remote_mcp is None:
                    raise RuntimeError("remote MCP execution is not configured")
                raw_output = await self.remote_mcp.execute(
                    binding,
                    connection,
                    arguments,
                    credentials,
                    correlation_id=request.request_id,
                )
            else:
                raw_output = await self.executor.execute(
                    capability, arguments, credentials
                )
            # The output transform runs *before* the scrub / redact / validate chain,
            # and that chain's order is unchanged: the transform only decides what the
            # chain is handed. Normalized output is still redacted, still scrubbed of
            # injected credential values, and still validated against the capability's
            # declared output_schema -- a backend cannot buy leniency with a transform.
            normalized = self._transform_output(
                binding,
                request,
                raw_output,
                connector_id=connector_id,
                connection_id=connection_id,
            )
            output = scrub_values(redact(normalized), credentials.values())
            validate(instance=output, schema=capability.output_schema)
            await self._record_evidence(request, output)
            if request.idempotency_key:
                await self.side_effects.finish(
                    request.idempotency_key, succeeded=True, result=output
                )
            return await self._terminal(
                provider,
                request,
                authorization,
                CapabilityExecutionStatus.SUCCEEDED,
                output=output,
                operation_id=operation_id,
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
                source_ids=_output_source_ids(output),
            )
        except Exception as exc:
            error = ErrorSummary(
                code="CAPABILITY_EXECUTION_FAILED",
                message=redact_text(str(exc)),
                retryable=False,
            )
            if request.idempotency_key:
                await self.side_effects.finish(
                    request.idempotency_key,
                    succeeded=False,
                    result={"error": error.model_dump(mode="json")},
                )
            return await self._terminal(
                provider,
                request,
                authorization,
                CapabilityExecutionStatus.FAILED,
                error,
                operation_id=operation_id,
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
            )

    def _transform_input(
        self, binding: CapabilityBinding | None, request: CapabilityRequest
    ) -> dict[str, Any]:
        """Map canonical arguments onto the resolved backend's wire arguments.

        A binding that names no transform -- every binding that predates v0.3 M5 --
        returns the canonical arguments unchanged, so this is a no-op for them.
        """

        if self.transforms is None or binding is None:
            return request.arguments
        return self.transforms.apply_input(binding.input_transform_ref, request.arguments)

    def _transform_output(
        self,
        binding: CapabilityBinding | None,
        request: CapabilityRequest,
        raw_output: Any,
        *,
        connector_id: str | None,
        connection_id: str | None,
    ) -> dict[str, Any]:
        """Map the backend's wire result onto the canonical capability output.

        Every value in the provenance context comes from the real execution path: the
        resolved binding and connection, the canonical request, and the gateway clock.
        Nothing is read back from connector output, so a connector cannot author the
        record that says where it came from.
        """

        if self.transforms is None or binding is None:
            if not isinstance(raw_output, dict):
                raise RuntimeError("capability output must be an object")
            return raw_output
        return self.transforms.apply_output(
            binding.output_transform_ref,
            raw_output,
            TransformContext(
                capability_id=request.capability_id,
                connector_id=connector_id or binding.connector_id,
                query=request_query(request.arguments),
                retrieved_at=self.clock(),
                binding_id=binding.binding_id,
                connection_id=connection_id,
            ),
        )

    async def _record_evidence(
        self, request: CapabilityRequest, output: dict[str, Any] | None
    ) -> None:
        """Persist normalized candidates as evidence, from the real execution path.

        Written after validation, so the Evidence Store only ever holds output the
        capability's own schema accepted. The trust label is a literal assigned here
        and is never read from connector-supplied data: ``EvidenceCandidate`` carries
        no trust field, so a payload claiming ``"trust": "VERIFIED"`` has nowhere to
        land. Verifiers relabel the record later (AC3-RES-04); until then it is
        UNVERIFIED and, by ``EvidenceRecord``'s own validator, unrankable.

        Records are content-addressed: a candidate whose digest is already stored for
        this run is not written twice, so the same paper reached through two backends
        collapses to one piece of evidence with the provenance of the first.

        Capabilities that return no ``candidates`` array -- every capability outside
        the research package -- fall straight through.
        """

        if not isinstance(output, dict):
            return
        candidates = output.get(CANDIDATES_KEY)
        if not isinstance(candidates, list):
            return
        for item in candidates:
            if not isinstance(item, dict):
                continue
            candidate = EvidenceCandidate.model_validate(item)
            existing = await self.store.get_research_evidence_by_digest(
                request.run_id, candidate.content_digest
            )
            if existing is not None:
                continue
            await self.store.save_research_evidence(
                EvidenceRecord(
                    evidence_id=new_id("evidence"),
                    run_id=request.run_id,
                    node_id=request.node_id,
                    evidence_class=candidate.evidence_class,
                    candidate=candidate,
                    trust=EvidenceTrust.UNVERIFIED,
                    trust_score=None,
                )
            )

    async def _approval_for(self, request: CapabilityRequest) -> ApprovalRecord | None:
        native_request_id = approval_binding(request)
        records = await self.store.list_approvals(request.run_id)
        return next(
            (item for item in records if item.native_request_id == native_request_id), None
        )

    async def _ensure_approval(self, request: CapabilityRequest) -> ApprovalRecord:
        return await self.store.save_approval(
            ApprovalRecord(
                approval_id=new_id("approval"),
                run_id=request.run_id,
                node_id=request.node_id,
                native_request_id=approval_binding(request),
                method="capability/execute",
                summary=f"Allow {request.capability_id}@{request.capability_version}",
                payload=redact(
                    {
                        "capability_id": request.capability_id,
                        "capability_version": request.capability_version,
                        "arguments": request.arguments,
                        "declared_reason": request.declared_reason,
                        "idempotency_key": request.idempotency_key,
                    }
                ),
            )
        )

    async def _approval_event_exists(self, run_id: str, approval_id: str) -> bool:
        return any(
            event.normalized_type is EventType.APPROVAL_REQUIRED
            and event.payload.get("approval_id") == approval_id
            for event in await self.store.list_events(run_id)
        )

    async def _duplicate_result(
        self,
        provider: Any,
        request: CapabilityRequest,
        authorization: CapabilityAuthorization,
        operation: Any,
        *,
        connector_id: str | None = None,
        binding_id: str | None = None,
        connection_id: str | None = None,
    ) -> CapabilityExecutionResult:
        if operation.status is SideEffectStatus.SUCCEEDED:
            return await self._terminal(
                provider,
                request,
                authorization,
                CapabilityExecutionStatus.SUCCEEDED,
                output=redact(operation.result_payload or {}),
                operation_id=operation.operation_id,
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
                source_ids=_output_source_ids(operation.result_payload),
            )
        if operation.status is SideEffectStatus.FAILED:
            return await self._terminal(
                provider,
                request,
                authorization,
                CapabilityExecutionStatus.FAILED,
                ErrorSummary(code="SIDE_EFFECT_PREVIOUSLY_FAILED", message="prior call failed"),
                operation_id=operation.operation_id,
                connector_id=connector_id,
                binding_id=binding_id,
                connection_id=connection_id,
            )
        return await self._terminal(
            provider,
            request,
            authorization,
            CapabilityExecutionStatus.UNKNOWN,
            ErrorSummary(
                code="SIDE_EFFECT_OUTCOME_UNKNOWN",
                message="existing side-effect intent has no safe terminal result",
            ),
            operation_id=operation.operation_id,
            connector_id=connector_id,
            binding_id=binding_id,
            connection_id=connection_id,
        )

    async def _terminal(
        self,
        provider: Any,
        request: CapabilityRequest,
        authorization: CapabilityAuthorization,
        status: CapabilityExecutionStatus,
        error: ErrorSummary | None = None,
        *,
        output: dict[str, Any] | None = None,
        operation_id: str | None = None,
        connector_id: str | None = None,
        binding_id: str | None = None,
        connection_id: str | None = None,
        source_ids: list[str] | None = None,
    ) -> CapabilityExecutionResult:
        result = CapabilityExecutionResult(
            request=request,
            authorization=authorization,
            status=status,
            output=redact(output) if output is not None else None,
            error=error,
            side_effect_operation_id=operation_id,
            completed_at=datetime.now(UTC),
            connector_id=connector_id,
            binding_id=binding_id,
            connection_id=connection_id,
            source_ids=list(source_ids) if source_ids else [],
        )
        await self.store.save_capability_result(result)
        event_type = (
            EventType.TOOL_COMPLETED
            if status is CapabilityExecutionStatus.SUCCEEDED
            else EventType.TOOL_FAILED
        )
        await self._event(
            provider,
            request,
            event_type,
            "accretion/capability-terminal",
            {
                "status": status.value,
                "output": result.output,
                "error": error.model_dump(mode="json") if error else None,
                "side_effect_operation_id": operation_id,
            },
        )
        return result

    async def _event(
        self,
        provider: Any,
        request: CapabilityRequest,
        event_type: EventType,
        native_type: str,
        payload: dict[str, Any],
    ) -> None:
        run = await self.store.get_run(request.run_id)
        session_id = run.session_id if run and run.session_id else f"gateway:{request.run_id}"
        await self.store.append_event(
            make_event(
                run_id=request.run_id,
                session_id=session_id,
                provider=provider,
                native_type=native_type,
                normalized_type=event_type,
                payload={
                    "tool_call_id": request.request_id,
                    "capability_id": request.capability_id,
                    **redact(payload),
                },
                adapter_version=self.adapter_version,
            )
        )
        if self.notify is not None:
            await self.notify(request.run_id)


@dataclass(slots=True)
class GatewayCapabilityInvoker:
    """Resolve a canonical capability id and execute it through the gateway.

    This is the executor-side half of the section 27 exit criterion. A workflow node
    names *what* it needs --- a capability id and nothing else --- and this object
    turns that into a governed execution: the resolver picks the connection and the
    binding, so the call site never learns which connector served it, and swapping
    backends stays a binding change (AC3-RES-02).

    Evidence is *not* normalized or persisted here. ``CapabilityGateway`` already does
    both on its own execution path, after output-schema validation, which is the only
    place a trust label is ever assigned. Duplicating it here would create a second
    writer of the Evidence Store and a second opinion about trust.

    Failures are returned, never raised: a research lookup that cannot resolve must
    leave the node's existing outcome untouched rather than abort a run that was not
    asking for research in the first place.
    """

    resolver: CapabilityResolver
    gateway: CapabilityGateway
    principal_id: str | None = None
    workspace_id: str | None = None
    declared_reason: str = "workflow capability reference"

    async def __call__(
        self,
        *,
        run_id: str,
        node_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        executing_provider: Provider | None = None,
    ) -> CapabilityExecutionResult | None:
        resolved = await self.resolver.resolve(
            capability_id,
            principal_id=self.principal_id,
            workspace_id=self.workspace_id,
        )
        if resolved is None:
            return None
        if resolved.outcome not in {
            CapabilityResolutionOutcome.OK,
            CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED,
        }:
            return None
        return await self.gateway.execute(
            CapabilityRequest(
                request_id=new_id("capability_request"),
                run_id=run_id,
                node_id=node_id,
                capability_id=capability_id,
                capability_version=resolved.capability.version,
                arguments=arguments,
                declared_reason=self.declared_reason,
            ),
            resolved.connection,
            resolved.binding,
            executing_provider=executing_provider,
        )


async def _echo_handler(
    arguments: dict[str, Any], credentials: Mapping[str, str]
) -> dict[str, Any]:
    del credentials
    return {"message": arguments["message"]}


async def _protected_handler(
    arguments: dict[str, Any], credentials: Mapping[str, str]
) -> dict[str, Any]:
    del credentials
    return {"recorded": True, "value": arguments["value"]}


def default_capability_handlers() -> dict[str, CapabilityHandler]:
    return {
        "accretion.echo": _echo_handler,
        "accretion.protected-write": _protected_handler,
    }


async def seed_governance(store: StateStore) -> None:
    created_at = datetime(2026, 8, 22, tzinfo=UTC)
    capabilities = [
        Capability(
            capability_id="accretion.echo",
            kind=CapabilityKind.TOOL,
            version="1.0.0",
            description="Return a structured message through the governed gateway.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 2000}},
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            risk=RiskLevel.LOW,
            idempotency=IdempotencyMode.NONE,
            backend=CapabilityBackend.PYTHON,
            created_at=created_at,
        ),
        Capability(
            capability_id="accretion.protected-write",
            kind=CapabilityKind.TOOL,
            version="1.0.0",
            description="Exercise approval and durable side-effect accounting safely.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string", "maxLength": 2000}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "recorded": {"type": "boolean"},
                    "value": {"type": "string"},
                },
                "required": ["recorded", "value"],
                "additionalProperties": False,
            },
            risk=RiskLevel.HIGH,
            side_effects=["durable test record"],
            idempotency=IdempotencyMode.KEYED,
            backend=CapabilityBackend.PYTHON,
            created_at=created_at,
        ),
    ]
    for capability in capabilities:
        await store.upsert_capability(capability)
    skill = MetaSkill(
        skill_id="governed-capability-smoke",
        version="1.0.0",
        description="Exercise low-risk and approval-bound gateway calls.",
        activation_criteria={"task_type": ["EXPERIMENT"]},
        instructions="Use only explicitly allowed Accretion gateway capabilities.",
        required_capabilities=[item.capability_id for item in capabilities],
        created_at=created_at,
    )
    await store.upsert_skill(skill)
    plugin_payload = {
        "plugin_id": "accretion-core-governance",
        "version": "1.0.0",
        "capabilities": [item.capability_id for item in capabilities],
        "skills": [skill.skill_id],
    }
    # Converged onto ADR-056 canonical JSON in M8. The payload is four code literals, so
    # its domain is closed and entirely ASCII and the digest is the same constant either
    # way --- which had to be *proved* rather than assumed, because ``upsert_plugin``
    # refuses any drift for an existing ``(plugin_id, version)`` and a moved checksum
    # would fail every deployment that already seeded this row. ``canonical_json`` and not
    # ``content_hash``: the latter drops a top-level ``content_hash`` key, and this digest
    # must commit to every key it is given.
    checksum = hashlib.sha256(canonical_json(plugin_payload)).hexdigest()
    await store.upsert_plugin(
        MetaPlugin(
            plugin_id="accretion-core-governance",
            version="1.0.0",
            description="Built-in, locally allowlisted v0.1 governance plugin.",
            capability_refs=[item.capability_id for item in capabilities],
            skill_refs=[skill.skill_id],
            policy_refs=["local-capability-policy"],
            checksum=checksum,
            allowlisted=True,
            created_at=created_at,
        )
    )
    await store.upsert_capability_policy(
        CapabilityPolicy(
            policy_id="local-capability-policy",
            version="1.0.0",
            description="Deny by default; approve protected external effects.",
            require_approval_at_risk=RiskLevel.HIGH,
            created_at=created_at,
        )
    )
    # M0 demo connector: exercises the full connection-aware resolution path
    # for accretion.echo with NONE auth. Built-ins stay binding-free so the
    # NO_CONNECTOR_REQUIRED fast path keeps v0.1/v0.2 behavior unchanged.
    await store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id="conndef_local_echo",
            name="Local echo connector",
            kind=ConnectorKind.LOCAL,
            auth_type=ConnectorAuthType.NONE,
            connection_scope=ConnectionScope.WORKSPACE,
            created_at=created_at,
        )
    )
    await store.upsert_connection(
        Connection(
            connection_id="conn_local_echo",
            connector_id="conndef_local_echo",
            workspace_id="workspace_local",
            scope=ConnectionScope.WORKSPACE,
            status=ConnectionStatus.ACTIVE,
            workspace_shareable=True,
            created_at=created_at,
        )
    )
    await store.upsert_capability_binding(
        CapabilityBinding(
            binding_id="capbind_local_echo",
            capability_id="accretion.echo",
            connector_id="conndef_local_echo",
            backend=CapabilityBindingBackend(type=CapabilityBackend.PYTHON),
            created_at=created_at,
        )
    )
