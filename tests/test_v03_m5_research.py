"""v0.3 M5 PR2 --- the research MCP adapter, its transforms, and the bundled plugin.

Claims AC3-RES-01, AC3-RES-02 and AC3-RES-03.

Nothing here skips, and nothing at the seam under test is stubbed. The two research
backends are served by :mod:`tests.fake_research_api` --- the test's own corpus, in
the test's own encoding of the two wire shapes --- and everything between that fake
and the assertion is the real thing: the real SDK MCP client over ``ASGITransport``,
M3's real :class:`RemoteMcpManager` behind its real endpoint policy, the real
``PluginManager`` installing the bundled package from disk, and the real
``CapabilityGateway``. What is faked is the one thing M5 deliberately fakes, the
upstream literature service, and the acceptance baseline records that. No test in
this file touches the network.

The evidence author's rule for this file: a criterion is proven by making the system
*do* the thing, never by reading back a declaration that says it does. So AC3-RES-01
executes every capability instead of counting manifest strings, AC3-RES-02 drives two
runs from one byte-identical workflow spec instead of comparing two literals, and
AC3-RES-03 reads its record back out of the store rather than out of the value the
call returned.

This repository has no ``conftest.py``: the builder below is module-local and returns
a tuple, and ``asyncio_mode`` is ``auto``, so tests are bare ``async def``.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fake_research_api import (
    MISMATCHED_CLAIM,
    MISMATCHED_PAPER,
    SHARED_DOI,
    FakeResearchApi,
)
from httpx import ASGITransport, AsyncClient
from jsonschema import validate
from pydantic import ValidationError

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    CapabilityBinding,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
    CapabilityRequest,
    CapabilityResolutionOutcome,
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceTrust,
    ExecutionMode,
    GraphNodeKind,
    MetaPluginManifest,
    Principal,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunNode,
    RunState,
    Task,
    TaskEnvelope,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
    WorkflowNodeSpec,
    WorkflowTemplate,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    GatewayCapabilityInvoker,
    default_capability_handlers,
    seed_governance,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.manager import RemoteMcpManager
from accretion.mcp.remote_client import SdkRemoteMcpClient
from accretion.orchestration.materialize import materialize_workflow_template
from accretion.orchestration.models import (
    DynamicLoopSpec,
    DynamicWorkflowEdgeSpec,
    DynamicWorkflowNodeSpec,
    WorkflowProposal,
)
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.plugins.manager import DirectoryPluginSource, PluginManager
from accretion.plugins.manifest import canonical_manifest_digest, parse_manifest
from accretion.plugins.trust import PluginTrustVerifier
from accretion.research import (
    BACKENDS,
    CROSSREF_CONNECTOR,
    OPENALEX_CONNECTOR,
    RESEARCH_CAPABILITY_IDS,
    RESEARCH_PLUGIN_ID,
    bind_research_backend,
    default_transform_registry,
    seed_research_connectors,
)
from accretion.research.server import CROSSREF_HOST, OPENALEX_HOST
from accretion.research.trust import TRUST_ORDER, assign_trust, rank_evidence
from accretion.resolver import CapabilityResolver
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.verifiers.git_diff import GitDiffVerifier
from accretion.verifiers.output_contract import OutputContractVerifier
from accretion.verifiers.registry import VerifierRegistry, VerifierUnavailableError
from accretion.verifiers.research import (
    CITATION_VERIFIER_ID,
    PROVENANCE_VERIFIER_ID,
    RESEARCH_VERIFIER_IDS,
    CitationVerifier,
    research_verifiers,
)
from accretion.verifiers.trajectory import TrajectoryPolicyVerifier
from accretion.workspace import WorktreeManager

WORKSPACE = "wks_m5"
PRINCIPAL = "usr_m5_admin"
PERMISSIONS = frozenset({"research.read", "github.read"})

SEARCH = "research.literature.search"
FETCH = "research.paper.fetch"
METADATA = "research.metadata.resolve"
CITATION = "research.citation.verify"
REPOSITORY = "github.search"

QUERY = "evidence provenance retrieval"
OTHER_QUERY = "citation verification trust"
#: Reaches F-1007, which no other assertion in this file retrieves — so the record it
#: produces is genuinely a second piece of evidence rather than a dedup collision.
UNVERIFIED_QUERY = "unverified unrankable quarantine"

# The clock is injected, so AC3-RES-03's timestamp assertion is a bracket the test
# owns rather than a re-read of ``datetime.now`` dressed up as an assertion.
CLOCK_BEFORE = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 26, 11, 30, tzinfo=UTC)
CLOCK_AFTER = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

#: Canonical arguments per capability, in the *canonical* names the input schemas
#: declare. Backends receive quite different arguments; that is the transform's job,
#: and asserting on ``FakeResearchApi.calls`` is how this file checks it happened.
CANONICAL_ARGUMENTS: dict[str, dict[str, Any]] = {
    SEARCH: {"query": QUERY, "max_results": 5},
    FETCH: {"paper_id": "F-1001"},
    METADATA: {"identifier": "F-1001"},
    CITATION: {"paper_id": "F-1005", "citation": MISMATCHED_CLAIM},
    REPOSITORY: {"query": "provenance trust", "max_results": 5},
}

#: Backend B identifies a record by DOI, not by opaque work id, so the two
#: identifier-shaped capabilities take a different *value* --- never a different
#: argument *name*, and never a different capability id.
CROSSREF_ARGUMENT_OVERRIDES: dict[str, dict[str, Any]] = {
    FETCH: {"paper_id": "10.5555/gateway-provenance"},
    METADATA: {"identifier": "10.5555/gateway-provenance"},
    CITATION: {"paper_id": MISMATCHED_CLAIM, "citation": MISMATCHED_PAPER.title},
}


async def research_dns(host: str, port: int) -> list[str]:
    """Resolve only the two research hosts, and only to a globally routable address.

    Deliberately not a blanket allow: the endpoint policy's non-global-address and
    HTTPS checks stay live for everything else this file touches.
    """

    del port
    if host in {OPENALEX_HOST, CROSSREF_HOST}:
        return ["93.184.216.34"]
    raise OSError(f"unexpected host {host!r}")


@dataclass(slots=True)
class ResearchStack:
    """Everything a research test needs, all of it wired to one store."""

    store: MemoryStore
    upstream: FakeResearchApi
    plugins: PluginManager
    remote_mcp: RemoteMcpManager
    gateway: CapabilityGateway
    resolver: CapabilityResolver
    manifest: MetaPluginManifest
    servers: dict[str, str]


@contextlib.asynccontextmanager
async def research_stack(
    *,
    upstream: FakeResearchApi | None = None,
    granted_permissions: frozenset[str] = PERMISSIONS,
    retrieved_at: datetime = RETRIEVED_AT,
    enabled_connector: str = OPENALEX_CONNECTOR,
) -> AsyncIterator[ResearchStack]:
    """Module-local async builder; this repository has no ``conftest.py``.

    An async context manager rather than a plain builder because a streamable-HTTP
    MCP app must have its lifespan entered before it will serve, and both research
    backends really are served here.
    """

    api = upstream if upstream is not None else FakeResearchApi()
    router, apps = api.transport()

    def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=router),
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    store = MemoryStore()
    await seed_governance(store)
    await store.upsert_principal(
        Principal(principal_id=PRINCIPAL, issuer="test", subject=PRINCIPAL)
    )
    await store.upsert_workspace(WorkspaceEntity(workspace_id=WORKSPACE, name=WORKSPACE))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            role=WorkspaceRole.OWNER,
        )
    )
    await seed_research_connectors(store)

    remote_mcp = RemoteMcpManager(
        store=store,
        client=SdkRemoteMcpClient(http_client_factory=client_factory),
        endpoint_policy=McpEndpointPolicy(resolver=research_dns),
    )
    policy_engine = CapabilityPolicyEngine(set(granted_permissions))
    plugins = PluginManager(
        store=store,
        trust_verifier=PluginTrustVerifier(builtin_ids=(RESEARCH_PLUGIN_ID,)),
        policy_engine=policy_engine,
        source=DirectoryPluginSource(),
        remote_mcp=remote_mcp,
    )
    gateway = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=CredentialBroker(),
        executor=CapabilityExecutor(default_capability_handlers()),
        policy_engine=policy_engine,
        remote_mcp=remote_mcp,
        transforms=default_transform_registry(),
        clock=lambda: retrieved_at,
    )

    manifest = parse_manifest(await plugins.source.read_manifest(RESEARCH_PLUGIN_ID))
    async with contextlib.AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.router.lifespan_context(app))
        installation = await plugins.install(
            RESEARCH_PLUGIN_ID,
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            consent_digest=canonical_manifest_digest(manifest),
            consent_capability_ids=[item.capability_id for item in manifest.capabilities],
        )
        servers: dict[str, str] = {}
        for mcp_server_id in installation.registered_mcp_server_ids:
            server = await store.get_mcp_server(mcp_server_id)
            assert server is not None
            # Enabling runs M3's real discovery, schema validation, and endpoint
            # policy against the fake upstream. A fake whose tool names had drifted
            # from the canonical ones would fail here rather than pass silently.
            await remote_mcp.refresh_discovery(
                mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
            )
            await remote_mcp.enable(
                mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
            )
            servers[server.connector_id] = mcp_server_id
        for backend in BACKENDS:
            await bind_research_backend(
                store,
                connector_id=backend.connector_id,
                mcp_server_id=servers[backend.connector_id],
                enabled=backend.connector_id == enabled_connector,
            )
        yield ResearchStack(
            store=store,
            upstream=api,
            plugins=plugins,
            remote_mcp=remote_mcp,
            gateway=gateway,
            resolver=CapabilityResolver(store),
            manifest=manifest,
            servers=servers,
        )


async def make_run(store: MemoryStore, allowed: Sequence[str]) -> Run:
    """A real project/task/run triple, so gateway calls produce real provenance."""

    project = Project(project_id=new_id("project"), name="M5", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Gather external research evidence",
            allowed_capabilities=list(allowed),
            risk_level=RiskLevel.LOW,
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=PRINCIPAL,
    )
    await store.create_run(run)
    return run


async def call(
    stack: ResearchStack,
    run: Run,
    capability_id: str,
    arguments: dict[str, Any],
    *,
    node_id: str = "node_m5_research",
) -> CapabilityExecutionResult:
    """Resolve the capability the way a runtime would, then execute it.

    The resolver picks the connection and the binding; the test never names a
    backend here. That is what makes the backend swap in AC3-RES-02 a property of
    the system rather than of the call site.
    """

    resolved = await stack.resolver.resolve(
        capability_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert resolved is not None, capability_id
    assert resolved.outcome is CapabilityResolutionOutcome.OK, resolved.reason
    return await stack.gateway.execute(
        CapabilityRequest(
            request_id=new_id("capability_request"),
            run_id=run.run_id,
            node_id=node_id,
            capability_id=capability_id,
            capability_version=resolved.capability.version,
            arguments=arguments,
            declared_reason="acceptance evidence",
        ),
        resolved.connection,
        resolved.binding,
    )


def declared_capability(manifest: MetaPluginManifest, capability_id: str) -> Any:
    return next(
        item for item in manifest.capabilities if item.capability_id == capability_id
    )


async def research_bindings(
    store: MemoryStore, connector_id: str, capability_id: str | None = None
) -> list[CapabilityBinding]:
    return [
        binding
        for binding in await store.list_capability_bindings(
            capability_id=capability_id, enabled_only=False
        )
        if binding.connector_id == connector_id
    ]


async def swap_backend(store: MemoryStore, *, to: str) -> set[str]:
    """AC3-RES-02's swap: flip ``enabled`` on binding rows and change nothing else.

    Returns the set of model field names that actually differed across the whole
    binding set, so a test can assert that ``enabled`` really was the only one --- no
    code edited, no workflow edited, no capability id, schema, or transform moved.
    """

    changed: set[str] = set()
    for backend in BACKENDS:
        for binding in await research_bindings(store, backend.connector_id):
            target = backend.connector_id == to
            if binding.enabled == target:
                continue
            updated = binding.model_copy(update={"enabled": target})
            before = binding.model_dump(mode="json")
            after = updated.model_dump(mode="json")
            changed |= {key for key in after if before[key] != after[key]}
            await store.upsert_capability_binding(updated)
    return changed


# --------------------------------------------------------------------------------------
# AC3-RES-01
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-RES-01")
async def test_every_research_capability_executes_through_the_gateway() -> None:
    """The plugin *exposes* literature search, retrieval, metadata and verification.

    Not three strings in a manifest. The plugin is installed through the real
    ``PluginManager``, every declared capability is resolved and executed through
    ``CapabilityGateway.execute``, each must reach ``SUCCEEDED``, and each output is
    validated against the ``output_schema`` the manifest itself declares --- so a
    capability that is declared but unexecutable, or executable but off-contract,
    fails here.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, RESEARCH_CAPABILITY_IDS)

        # The four SDD 10 research capabilities the criterion names, plus the
        # repository search kept from the superset (ADR3-M5-002).
        assert SEARCH in RESEARCH_CAPABILITY_IDS
        assert FETCH in RESEARCH_CAPABILITY_IDS
        assert METADATA in RESEARCH_CAPABILITY_IDS
        assert CITATION in RESEARCH_CAPABILITY_IDS

        executed: dict[str, CapabilityExecutionResult] = {}
        for capability_id in RESEARCH_CAPABILITY_IDS:
            result = await call(stack, run, capability_id, CANONICAL_ARGUMENTS[capability_id])
            assert result.status is CapabilityExecutionStatus.SUCCEEDED, (
                capability_id,
                result.error,
            )
            assert result.output is not None
            # The declared contract, read from the manifest on disk, not from the
            # capability row the installer happened to write.
            declared = declared_capability(stack.manifest, capability_id)
            validate(instance=result.output, schema=declared.output_schema)
            assert result.output["candidates"], capability_id
            executed[capability_id] = result

        assert set(executed) == set(RESEARCH_CAPABILITY_IDS)

        # Each capability really did something different: the fake upstream saw a
        # distinct tool per capability, so five SUCCEEDEDs are not one call five times.
        assert len({tool_call.tool_name for tool_call in stack.upstream.calls}) == len(
            RESEARCH_CAPABILITY_IDS
        )

        # Citation *verification*, not citation resolution: the claimed identifier
        # does not survive the register, and the capability says so.
        citation = executed[CITATION].output
        assert citation is not None
        candidate = citation["candidates"][0]
        assert candidate["payload"]["resolves"] is False
        assert candidate["payload"]["claimed_doi"] == MISMATCHED_CLAIM
        assert candidate["identifiers"]["doi"] == MISMATCHED_PAPER.registered_doi
        assert MISMATCHED_PAPER.identifier_matches is False

        # ADR3-M5-002 asserted rather than merely written down: no research criterion
        # needs code execution, so the capability is not declared, not registered, and
        # not resolvable.
        assert "python.execute" not in RESEARCH_CAPABILITY_IDS
        declared_ids = {item.capability_id for item in stack.manifest.capabilities}
        assert "python.execute" not in declared_ids
        assert await stack.store.get_capability("python.execute") is None
        assert (
            await stack.resolver.resolve(
                "python.execute", principal_id=PRINCIPAL, workspace_id=WORKSPACE
            )
        ) is None


async def test_a_capability_whose_upstream_fails_is_not_reported_as_success() -> None:
    """The SUCCEEDED assertions above are load-bearing, not free.

    Without this, ``SUCCEEDED`` could be a status the gateway hands back regardless.
    """

    upstream = FakeResearchApi(failing_tool="openalex_works_search")
    async with research_stack(upstream=upstream) as stack:
        run = await make_run(stack.store, RESEARCH_CAPABILITY_IDS)
        result = await call(stack, run, SEARCH, CANONICAL_ARGUMENTS[SEARCH])
        assert result.status is CapabilityExecutionStatus.FAILED
        assert await stack.store.list_research_evidence(run.run_id) == []


# --------------------------------------------------------------------------------------
# AC3-RES-02
# --------------------------------------------------------------------------------------


def research_workflow(capability_id: str) -> WorkflowTemplate:
    """One canonical workflow, named in terms of the capability and nothing else.

    It carries no connector, no endpoint, no tool name and no wire shape --- exactly
    the "without provider-specific logic" the section 27 exit criterion asks for. Both
    runs below are driven from this same object.
    """

    return WorkflowTemplate(
        template_record_id="tpl_m5_research",
        template_id="m5-research",
        version="1.0.0",
        mode=ExecutionMode.GRAPH,
        nodes=[
            WorkflowNodeSpec(
                key="gather",
                kind=GraphNodeKind.TOOL,
                label="Gather external research evidence",
                instruction=capability_id,
            )
        ],
        checksum="m5-research-checksum",
    )


@pytest.mark.acceptance("AC3-RES-02")
async def test_connector_backend_swaps_without_changing_capability_ids() -> None:
    """The backend swaps; the canonical workflow does not move a byte.

    Three separate claims, because the criterion makes three:

    (a) the ``WorkflowNodeSpec`` driving the call is ``==`` across the two runs and
        serializes to identical bytes, as does the template around it;
    (b) ``provenance.connector_id`` differs between the runs while ``capability_id``
        is identical --- proof the backend really changed, not just the label;
    (c) the swap is only ``enabled`` toggling on two binding rows. No code is edited,
        no workflow is edited, and no other field of any binding differs.
    """

    async with research_stack() as stack:
        workflow = research_workflow(SEARCH)
        node = workflow.nodes[0]
        capability_id = node.instruction
        assert capability_id is not None
        before_node_bytes = node.model_dump_json()
        before_template_bytes = workflow.model_dump_json()

        # --- run one, on backend A -------------------------------------------------
        first_run = await make_run(stack.store, [capability_id])
        first = await call(stack, first_run, capability_id, {"query": QUERY, "max_results": 5})
        assert first.status is CapabilityExecutionStatus.SUCCEEDED, first.error
        assert first.connector_id == OPENALEX_CONNECTOR

        # --- the swap, and only the swap -------------------------------------------
        changed_fields = await swap_backend(stack.store, to=CROSSREF_CONNECTOR)
        assert changed_fields == {"enabled"}, changed_fields
        enabled = [
            binding
            for binding in await stack.store.list_capability_bindings(enabled_only=True)
            if binding.connector_id in {OPENALEX_CONNECTOR, CROSSREF_CONNECTOR}
        ]
        assert {binding.connector_id for binding in enabled} == {CROSSREF_CONNECTOR}

        # --- run two, from the *same* workflow object -------------------------------
        second_run = await make_run(stack.store, [capability_id])
        second = await call(
            stack,
            second_run,
            workflow.nodes[0].instruction or "",
            {"query": QUERY, "max_results": 5},
        )
        assert second.status is CapabilityExecutionStatus.SUCCEEDED, second.error
        assert second.connector_id == CROSSREF_CONNECTOR

        # (a) the workflow is untouched, by equality and byte for byte
        assert workflow.nodes[0] == node
        assert workflow.nodes[0] == WorkflowNodeSpec.model_validate_json(before_node_bytes)
        assert workflow.nodes[0].model_dump_json() == before_node_bytes
        assert workflow.model_dump_json() == before_template_bytes

        # (b) the connector differs, the capability id does not
        first_records = await stack.store.list_research_evidence(first_run.run_id)
        second_records = await stack.store.list_research_evidence(second_run.run_id)
        assert first_records and second_records
        first_connectors = {r.candidate.provenance.connector_id for r in first_records}
        second_connectors = {r.candidate.provenance.connector_id for r in second_records}
        assert first_connectors == {OPENALEX_CONNECTOR}
        assert second_connectors == {CROSSREF_CONNECTOR}
        assert first_connectors != second_connectors
        capability_ids = {
            record.candidate.provenance.capability_id
            for record in (*first_records, *second_records)
        }
        assert capability_ids == {capability_id}

        # (c) the two backends genuinely spoke different wire dialects, so the
        # identical canonical output is normalization rather than coincidence.
        openalex_calls = stack.upstream.calls_to(OPENALEX_HOST)
        crossref_calls = stack.upstream.calls_to(CROSSREF_HOST)
        assert [c.arguments for c in openalex_calls] == [{"q": QUERY, "per_page": 5}]
        assert [c.arguments for c in crossref_calls] == [
            {"query_bibliographic": QUERY, "rows": 5}
        ]
        assert openalex_calls[0].tool_name != crossref_calls[0].tool_name

        # And the canonical output shape is the same on both sides of the swap.
        assert first.output is not None and second.output is not None
        assert sorted(first.output) == sorted(second.output) == ["candidates", "source_ids"]
        assert first.output["source_ids"] and second.output["source_ids"]


async def test_the_same_paper_through_either_backend_is_one_piece_of_evidence() -> None:
    """Content-addressing, which is what makes the swap safe rather than merely quiet.

    Also the deduplication half of the overlapping-DOI fixture: two records sharing a
    DOI are *not* one record, because identifier overlap is not content identity.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        first = await call(stack, run, FETCH, {"paper_id": "F-1001"})
        assert first.status is CapabilityExecutionStatus.SUCCEEDED, first.error
        await swap_backend(stack.store, to=CROSSREF_CONNECTOR)
        second = await call(stack, run, FETCH, {"paper_id": "10.5555/gateway-provenance"})
        assert second.status is CapabilityExecutionStatus.SUCCEEDED, second.error

        assert first.output is not None and second.output is not None
        first_digest = first.output["candidates"][0]["content_digest"]
        second_digest = second.output["candidates"][0]["content_digest"]
        assert first_digest == second_digest
        # One paper, reached twice through two connectors, stored once.
        assert len(await stack.store.list_research_evidence(run.run_id)) == 1

        # The shared DOI, on the other hand, is two distinct works.
        shared = await call(stack, run, FETCH, {"paper_id": SHARED_DOI})
        assert shared.status is CapabilityExecutionStatus.SUCCEEDED, shared.error
        assert shared.output is not None
        candidates = shared.output["candidates"]
        assert len(candidates) == 2
        assert {c["provenance"]["source_id"] for c in candidates} == {SHARED_DOI}
        assert len({c["content_digest"] for c in candidates}) == 2
        assert len(await stack.store.list_research_evidence(run.run_id)) == 3


async def test_a_backend_pointed_at_the_other_backends_transform_fails_closed() -> None:
    """The transform pair is the whole of the backend difference, and it is strict.

    If backend B's binding named backend A's output transform, the expected array key
    would be absent and the call must fail rather than quietly normalize zero results
    and report an empty but successful search.
    """

    async with research_stack(enabled_connector=CROSSREF_CONNECTOR) as stack:
        run = await make_run(stack.store, [SEARCH])
        for binding in await research_bindings(stack.store, CROSSREF_CONNECTOR, SEARCH):
            await stack.store.upsert_capability_binding(
                binding.model_copy(
                    update={"output_transform_ref": "research.output.openalex.search.v1"}
                )
            )
        result = await call(stack, run, SEARCH, {"query": QUERY, "max_results": 5})
        assert result.status is CapabilityExecutionStatus.FAILED
        assert result.error is not None
        assert await stack.store.list_research_evidence(run.run_id) == []


# --------------------------------------------------------------------------------------
# AC3-RES-03
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-RES-03")
async def test_evidence_provenance_is_read_back_out_of_the_store() -> None:
    """Connector, capability, query, timestamp and source identifier --- all five.

    Read back **from the store**, out of records this test never wrote. The test's
    only action is a gateway execution; the evidence write is entirely the gateway's,
    so what is asserted is what the system persisted rather than what the call
    happened to return.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        assert await stack.store.list_research_evidence(run.run_id) == []

        first = await call(stack, run, SEARCH, {"query": QUERY, "max_results": 5})
        assert first.status is CapabilityExecutionStatus.SUCCEEDED, first.error

        records = await stack.store.list_research_evidence(run.run_id)
        assert len(records) >= 2, records
        assert all(isinstance(record, EvidenceRecord) for record in records)

        for record in records:
            provenance = record.candidate.provenance
            # 1. connector
            assert provenance.connector_id == OPENALEX_CONNECTOR
            # 2. capability
            assert provenance.capability_id == SEARCH
            # 3. query
            assert provenance.query == QUERY
            # 4. timestamp, inside a bracket the injected clock defines
            assert CLOCK_BEFORE < provenance.retrieved_at < CLOCK_AFTER
            assert provenance.retrieved_at == RETRIEVED_AT
            # 5. source identifier
            assert provenance.source_id

        # Two results, two distinct source identifiers: the field identifies a
        # source rather than repeating one constant per call.
        source_ids = [record.candidate.provenance.source_id for record in records]
        assert len(set(source_ids)) == len(source_ids) >= 2

        # A second execution asking a different question stores a different query,
        # so the recorded query is this call's question and not a fixed string.
        second = await call(stack, run, SEARCH, {"query": OTHER_QUERY, "max_results": 5})
        assert second.status is CapabilityExecutionStatus.SUCCEEDED, second.error
        queries = {
            record.candidate.provenance.query
            for record in await stack.store.list_research_evidence(run.run_id)
        }
        assert QUERY in queries
        assert OTHER_QUERY in queries
        assert len(queries) == 2

        # And the type enforces it: provenance that cannot name its source cannot be
        # constructed, so the criterion survives a future author who forgets it.
        with pytest.raises(ValidationError) as missing_source:
            EvidenceProvenance(  # type: ignore[call-arg]
                connector_id=OPENALEX_CONNECTOR,
                capability_id=SEARCH,
                query=QUERY,
                retrieved_at=RETRIEVED_AT,
            )
        assert "source_id" in str(missing_source.value)


async def test_provenance_is_stamped_by_the_gateway_not_by_the_connector() -> None:
    """Every provenance field comes from the execution path, not the upstream payload.

    The fake upstream never emits a connector id, a capability id, a query or a
    timestamp, so a provenance that read itself out of connector output would have
    nothing to read.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        result = await call(stack, run, SEARCH, {"query": QUERY, "max_results": 3})
        assert result.status is CapabilityExecutionStatus.SUCCEEDED, result.error
        wire = json.dumps(
            [tool_call.arguments for tool_call in stack.upstream.calls]
        )
        assert OPENALEX_CONNECTOR not in wire
        record = (await stack.store.list_research_evidence(run.run_id))[0]
        provenance = record.candidate.provenance
        assert provenance.binding_id is not None
        assert provenance.connection_id is not None
        binding = next(
            item
            for item in await stack.store.list_capability_bindings(
                capability_id=SEARCH, enabled_only=True
            )
            if item.connector_id == OPENALEX_CONNECTOR
        )
        assert provenance.binding_id == binding.binding_id


async def test_evidence_is_filtered_by_capability_when_the_store_is_asked() -> None:
    """``list_research_evidence`` is the read path AC3-RES-03 is asserted through.

    A read path that ignored its filter would let the assertions above pass by
    accident, so the filter is exercised on its own.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        search = await call(stack, run, SEARCH, CANONICAL_ARGUMENTS[SEARCH])
        assert search.status is CapabilityExecutionStatus.SUCCEEDED, search.error
        repository = await call(stack, run, REPOSITORY, CANONICAL_ARGUMENTS[REPOSITORY])
        assert repository.status is CapabilityExecutionStatus.SUCCEEDED, repository.error

        everything = await stack.store.list_research_evidence(run.run_id)
        only_search = await stack.store.list_research_evidence(run.run_id, SEARCH)
        only_repository = await stack.store.list_research_evidence(run.run_id, REPOSITORY)
        assert only_search and only_repository
        assert len(only_search) + len(only_repository) == len(everything)
        assert {r.candidate.provenance.capability_id for r in only_search} == {SEARCH}
        assert {r.candidate.provenance.capability_id for r in only_repository} == {REPOSITORY}


# --------------------------------------------------------------------------------------
# AC3-RES-04 --- verifiers and the trust model (PR3)
# --------------------------------------------------------------------------------------
#
# Every record judged below was gathered by a real gateway execution earlier in the
# same test, through the real transforms, and read back out of the store. Neither
# operand of the criterion's comparison is a literal: the "deterministic verifier
# evidence" side is a record the real CitationVerifier passed, and the "unverified
# external text" side is a record the same run retrieved and no verifier accepted.


def evidence_target(run: Run, *evidence_ids: str) -> VerificationTarget:
    """A target scoped to specific records.

    Per-record scoping is what makes trust a property of the record rather than of
    the batch: with a run-scoped target one bad citation would quarantine every
    record the run gathered.
    """

    return VerificationTarget(
        target_ref=evidence_ids[0] if evidence_ids else run.run_id,
        kind=VerificationTargetKind.EXTERNAL_EVIDENCE,
        run_id=run.run_id,
        evidence_refs=list(evidence_ids),
    )


def evidence_context(run: Run) -> VerificationContext:
    return VerificationContext(
        task_id=run.task_id, project_id=run.project_id, workspace=Path(".")
    )


async def judge(
    stack: ResearchStack, run: Run, record: EvidenceRecord
) -> tuple[EvidenceRecord, list[VerificationResult]]:
    """Run all three real verifiers over one record, then apply the trust model."""

    target = evidence_target(run, record.evidence_id)
    context = evidence_context(run)
    results = [
        await verifier.verify(target, context)
        for verifier in research_verifiers(stack.store)
    ]
    return assign_trust(record, results), results


async def evidence_for(
    stack: ResearchStack, run: Run, capability_id: str, arguments: dict[str, Any]
) -> EvidenceRecord:
    """Execute a capability for real and return the single record it stored."""

    before = {record.evidence_id for record in await stack.store.list_research_evidence(run.run_id)}
    result = await call(stack, run, capability_id, arguments)
    assert result.status is CapabilityExecutionStatus.SUCCEEDED, result.error
    fresh = [
        record
        for record in await stack.store.list_research_evidence(run.run_id)
        if record.evidence_id not in before
    ]
    assert fresh, f"{capability_id} stored no evidence"
    return fresh[0]


@pytest.mark.acceptance("AC3-RES-04")
async def test_unverified_external_text_cannot_outrank_verified_evidence() -> None:
    """The criterion, and then the clincher that a lower *score* would not survive.

    Both records come from real gateway executions in this run. One is a citation the
    real :class:`CitationVerifier` resolved against the retrieved record; the other is
    plain retrieved text that no verifier accepted. The verified record is then given
    a relevance signal of ``0.01`` and the unverified one ``1.0`` --- and the verified
    record must *still* lead, because ``rank_evidence`` has no axis on which relevance
    could overtake trust. Under a merely-lower-score trust model this assertion fails.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))

        # Deterministic verifier evidence: a citation whose claimed identifier the
        # retrieved record actually registers.
        resolved = await evidence_for(
            stack,
            run,
            CITATION,
            {"paper_id": "F-1001", "citation": "10.5555/gateway-provenance"},
        )
        # Unverified external text: retrieved, provenanced, and judged by nobody. A
        # query that reaches a different paper, because the Evidence Store collapses
        # two retrievals of the same content into one record and the two sides of
        # this comparison have to be two records.
        external = await evidence_for(
            stack, run, SEARCH, {"query": UNVERIFIED_QUERY, "max_results": 1}
        )
        assert external.evidence_id != resolved.evidence_id

        verified, results = await judge(stack, run, resolved)
        assert {result.verifier_id for result in results} == set(RESEARCH_VERIFIER_IDS)
        assert verified.trust is EvidenceTrust.VERIFIED
        assert verified.trust_score is not None

        # The unverified record is not "assigned UNVERIFIED"; it is simply never
        # judged, which is the state real external text arrives in.
        assert external.trust is EvidenceTrust.UNVERIFIED
        assert external.trust_score is None

        # Marked lower trust — the criterion, read straight off the ladder.
        assert TRUST_ORDER[verified.trust] > TRUST_ORDER[external.trust]

        # And structurally unrankable, not merely low-scored: the model refuses to
        # let an unverified record carry a score at all.
        with pytest.raises(ValidationError) as scored_unverified:
            external.model_copy(update={"trust_score": 0.99}).model_validate(
                external.model_copy(update={"trust_score": 0.99}).model_dump()
            )
        assert "trust score" in str(scored_unverified.value)

        # The clincher. Relevance inverted, ranking unmoved.
        loud = external.model_copy(
            update={
                "candidate": external.candidate.model_copy(
                    update={"payload": {**external.candidate.payload, "similarity": 1.0}}
                )
            }
        )
        quiet = verified.model_copy(
            update={
                "candidate": verified.candidate.model_copy(
                    update={"payload": {**verified.candidate.payload, "similarity": 0.01}}
                )
            }
        )
        assert rank_evidence([loud, quiet]) == [quiet, loud]
        assert rank_evidence([quiet, loud]) == [quiet, loud]


async def test_a_connector_claiming_verified_is_still_stored_unverified() -> None:
    """The security half of AC3-RES-04: trust is *assigned*, never *received*.

    The ordering assertion above is only worth what the trust label is worth, and a
    label a connector can set is worth nothing. So this drives the whole real path ---
    fake upstream, real MCP transport, real transforms, real gateway, real store ---
    with an upstream that lies as convincingly as it can. Every row it serves claims
    ``trust: "VERIFIED"``, a ``trust_score`` of 0.99, ``verified: true``, and a
    ``verifier_id`` forging the id of a verifier that genuinely exists in this process
    --- so the forgery is not rejected merely for naming something unknown.

    None of it may land. The stored record must be ``UNVERIFIED`` with no score, and
    the forged keys must be absent from the payload rather than present-and-ignored:
    a key that survives normalization is one a later reader could still believe.
    """

    upstream = FakeResearchApi(
        claimed_trust="VERIFIED", claimed_verifier_id=CITATION_VERIFIER_ID
    )
    async with research_stack(upstream=upstream) as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))

        # Retrieved first, so it is a genuinely distinct record: the Evidence Store
        # collapses two retrievals of the same content, and the ranking check below
        # needs two records rather than one record twice.
        honest = await evidence_for(
            stack,
            run,
            CITATION,
            {"paper_id": "F-1001", "citation": "10.5555/gateway-provenance"},
        )

        result = await call(stack, run, SEARCH, {"query": QUERY, "max_results": 3})
        # The lie does not break retrieval — it is served, accepted, and defanged.
        assert result.status is CapabilityExecutionStatus.SUCCEEDED, result.error
        records = [
            record
            for record in await stack.store.list_research_evidence(run.run_id)
            if record.evidence_id != honest.evidence_id
        ]
        assert records

        for record in records:
            assert record.trust is EvidenceTrust.UNVERIFIED
            assert record.trust_score is None
            # Dropped at the normalizer, not merely disregarded downstream.
            for forged in ("trust", "trust_score", "verified", "verification", "verifier_id"):
                assert forged not in record.candidate.payload
            # Nowhere else on the candidate either — not in the snippet, the title,
            # the identifiers, or any nested structure the allowlist did not police.
            serialized = json.dumps(record.candidate.model_dump(mode="json"))
            assert "VERIFIED" not in serialized
            assert CITATION_VERIFIER_ID not in serialized

        # And the forgery buys nothing at ranking time: these records still sort
        # behind evidence a verifier actually accepted. Both operands remain real.
        verified, _ = await judge(stack, run, honest)
        assert verified.trust is EvidenceTrust.VERIFIED
        assert rank_evidence([records[0], verified])[0] == verified


async def test_every_unscored_record_sorts_after_every_scored_one() -> None:
    """Not "usually after". The first sort key is scored-ness, before anything else."""

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        await call(stack, run, SEARCH, {"query": QUERY, "max_results": 5})
        records = await stack.store.list_research_evidence(run.run_id)
        assert len(records) >= 3

        scored: list[EvidenceRecord] = []
        unscored: list[EvidenceRecord] = []
        for index, record in enumerate(records):
            if index % 2:
                unscored.append(record)
            else:
                # A weak but real score: the worst scored record still leads the best
                # unscored one.
                scored.append(
                    record.model_copy(
                        update={"trust": EvidenceTrust.CORROBORATED, "trust_score": 0.3}
                    )
                )
        assert scored and unscored
        ranked = rank_evidence([*unscored, *scored])
        boundary = len(scored)
        assert all(record.trust_score is not None for record in ranked[:boundary])
        assert all(record.trust_score is None for record in ranked[boundary:])
        # Total and stable: the same multiset in any input order lands identically.
        assert rank_evidence([*scored, *unscored]) == ranked


async def test_a_citation_the_retrieved_record_contradicts_is_quarantined() -> None:
    """A FAIL is a positive finding, so the record drops *below* unverified."""

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        record = await evidence_for(stack, run, CITATION, CANONICAL_ARGUMENTS[CITATION])
        judged, results = await judge(stack, run, record)

        citation = next(
            result for result in results if result.verifier_id == CITATION_VERIFIER_ID
        )
        assert citation.status is VerificationStatus.FAIL
        assert any(
            item.code == "CITATION_IDENTIFIER_MISMATCH" for item in citation.findings
        )
        assert judged.trust is EvidenceTrust.QUARANTINED
        assert judged.trust_score is None
        assert TRUST_ORDER[judged.trust] < TRUST_ORDER[EvidenceTrust.UNVERIFIED]


async def test_provenance_alone_corroborates_but_does_not_verify() -> None:
    """The middle rung, and the band that keeps it below every verified record.

    A record that both verifiers accept is VERIFIED. Withdraw *only* the citation
    verdict from the same record and it lands on CORROBORATED: still attributable ---
    provenance says where it came from --- but no longer verified, because nothing
    resolved its citation. That is genuinely more than nothing and genuinely less
    than verified, and the two score bands are disjoint so the rungs cannot cross.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        record = await evidence_for(stack, run, SEARCH, {"query": QUERY, "max_results": 1})
        judged, results = await judge(stack, run, record)

        citation = next(
            result for result in results if result.verifier_id == CITATION_VERIFIER_ID
        )
        provenance = next(
            result for result in results if result.verifier_id == PROVENANCE_VERIFIER_ID
        )
        assert citation.status is VerificationStatus.PASS
        assert provenance.status is VerificationStatus.PASS
        assert judged.trust is EvidenceTrust.VERIFIED

        # Drop the citation verdict and the same record falls exactly one rung, and
        # into a score band that cannot reach the one it just left.
        corroborated = assign_trust(record, [provenance])
        assert corroborated.trust is EvidenceTrust.CORROBORATED
        assert corroborated.trust_score is not None
        assert judged.trust_score is not None
        assert corroborated.trust_score < judged.trust_score
        assert corroborated.trust_score <= 0.65 < 0.7 <= judged.trust_score
        assert rank_evidence([corroborated, judged]) == [judged, corroborated]


async def test_inconclusive_never_raises_trust() -> None:
    """An unresolvable citation is not a soft pass.

    A record with no identifier to resolve against yields INCONCLUSIVE. The precise
    claim is that the INCONCLUSIVE contributes *nothing*: the record lands exactly
    where it would have landed had the citation verifier never run at all, one rung
    below the same record with a resolving citation. Rewarding unresolvability is how
    unverifiable text acquires a verified record's standing.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        record = await evidence_for(stack, run, SEARCH, {"query": QUERY, "max_results": 1})
        stripped = record.model_copy(
            update={"candidate": record.candidate.model_copy(update={"identifiers": {}})}
        )
        await stack.store.save_research_evidence(stripped)

        judged, results = await judge(stack, run, stripped)
        citation = next(
            result for result in results if result.verifier_id == CITATION_VERIFIER_ID
        )
        assert citation.status is VerificationStatus.INCONCLUSIVE
        assert citation.score is None

        # Did not raise: still short of VERIFIED, which only a real PASS reaches.
        assert judged.trust is EvidenceTrust.CORROBORATED
        assert judged.trust is not EvidenceTrust.VERIFIED
        assert TRUST_ORDER[judged.trust] < TRUST_ORDER[EvidenceTrust.VERIFIED]

        # Contributed nothing at all: identical to dropping the result entirely.
        without = assign_trust(
            stripped,
            [
                result
                for result in results
                if result.verifier_id != CITATION_VERIFIER_ID
            ],
        )
        assert (judged.trust, judged.trust_score) == (without.trust, without.trust_score)

        # And did not lower it either: INCONCLUSIVE is not FAIL.
        assert judged.trust is not EvidenceTrust.QUARANTINED
        assert TRUST_ORDER[judged.trust] > TRUST_ORDER[EvidenceTrust.UNVERIFIED]


async def test_the_citation_verifier_reads_the_record_not_the_network() -> None:
    """Determinism, asserted as an absence of upstream traffic and a stable verdict.

    Verification consults the record the gateway already retrieved. So the upstream
    call count does not move while verifying, and verifying the same stored record
    ten times produces ten identical verdicts.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        record = await evidence_for(stack, run, CITATION, CANONICAL_ARGUMENTS[CITATION])
        before = len(stack.upstream.calls)

        verifier = CitationVerifier(stack.store)
        target = evidence_target(run, record.evidence_id)
        context = evidence_context(run)
        verdicts = [(await verifier.verify(target, context)) for _ in range(10)]

        assert len(stack.upstream.calls) == before
        assert {verdict.status for verdict in verdicts} == {VerificationStatus.FAIL}
        assert len({verdict.score for verdict in verdicts}) == 1
        assert len({tuple(verdict.evidence_refs) for verdict in verdicts}) == 1
        assert len({
            tuple(sorted(item.fingerprint or "" for item in verdict.findings))
            for verdict in verdicts
        }) == 1


async def test_a_connector_supplied_verdict_is_never_read_as_authority() -> None:
    """Backend A volunteers ``resolves``; the verifier recomputes it anyway.

    The mismatched fixture's upstream row carries ``resolves: false`` alongside the
    identifiers. Flipping that self-graded key to ``true`` in the stored payload must
    not change the verdict --- and, because a connector-authored verdict has no
    business surviving normalization, the provenance verifier quarantines the record
    outright for carrying one.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        record = await evidence_for(stack, run, CITATION, CANONICAL_ARGUMENTS[CITATION])
        assert record.candidate.payload.get("resolves") is False

        poisoned = record.model_copy(
            update={
                "candidate": record.candidate.model_copy(
                    update={
                        "payload": {
                            **record.candidate.payload,
                            "resolves": True,
                            "trust": "VERIFIED",
                        }
                    }
                )
            }
        )
        await stack.store.save_research_evidence(poisoned)
        judged, results = await judge(stack, run, poisoned)

        citation = next(
            result for result in results if result.verifier_id == CITATION_VERIFIER_ID
        )
        provenance = next(
            result for result in results if result.verifier_id == PROVENANCE_VERIFIER_ID
        )
        assert citation.status is VerificationStatus.FAIL
        assert provenance.status is VerificationStatus.FAIL
        assert any(
            item.code == "CONNECTOR_SUPPLIED_TRUST" for item in provenance.findings
        )
        assert judged.trust is EvidenceTrust.QUARANTINED


async def test_assign_trust_ignores_the_label_the_record_arrives_with() -> None:
    """Recomputed, never inherited --- so the function is idempotent and unspoofable."""

    async with research_stack() as stack:
        run = await make_run(stack.store, list(RESEARCH_CAPABILITY_IDS))
        record = await evidence_for(stack, run, SEARCH, {"query": QUERY, "max_results": 1})
        verified, results = await judge(stack, run, record)
        assert verified.trust is EvidenceTrust.VERIFIED

        # A record that arrives already claiming VERIFIED, with nothing to back it.
        assert assign_trust(verified, []).trust is EvidenceTrust.UNVERIFIED
        assert assign_trust(verified, []).trust_score is None
        # And re-judging a judged record does not drift.
        assert assign_trust(verified, results).trust_score == verified.trust_score


async def test_research_verifiers_reject_a_target_of_the_wrong_kind() -> None:
    """The kind guard every verifier in this repository carries."""

    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        await call(stack, run, SEARCH, {"query": QUERY, "max_results": 1})
        wrong = VerificationTarget(
            target_ref=run.run_id,
            kind=VerificationTargetKind.COMMAND_SUITE,
            run_id=run.run_id,
        )
        for verifier in research_verifiers(stack.store):
            result = await verifier.verify(wrong, evidence_context(run))
            assert result.status is VerificationStatus.INCONCLUSIVE
            assert [item.code for item in result.findings] == ["TARGET_KIND_MISMATCH"]


async def test_a_research_verifier_id_is_given_an_external_evidence_target(
    tmp_path: Path,
) -> None:
    """The run manager's target-kind branch, which the kind guard above depends on.

    Without the branch a research verifier id falls through to COMMAND_SUITE and
    every research verifier answers INCONCLUSIVE about a target it should have
    judged --- a silent no-op wearing a configuration error's clothes.
    """

    store = MemoryStore()
    manager = RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
        verifier_registry=VerifierRegistry(
            [OutputContractVerifier(), *research_verifiers(store)]
        ),
    )
    project = Project(project_id=new_id("project"), name="M5", repository_path=".")
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Gather external research evidence",
            risk_level=RiskLevel.LOW,
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=PRINCIPAL,
    )

    for verifier_id in sorted(RESEARCH_VERIFIER_IDS):
        target = manager._verification_target(
            verifier_id=verifier_id,
            run=run,
            task=task,
            iteration_id=None,
            artifact_ref=None,
            diff_sha256=None,
        )
        assert target.kind is VerificationTargetKind.EXTERNAL_EVIDENCE
        assert target.command_suite_refs == []
        assert target.evidence_refs == []
    # The pre-M5 mapping is untouched.
    assert (
        manager._verification_target(
            verifier_id="output-contract",
            run=run,
            task=task,
            iteration_id=None,
            artifact_ref=None,
            diff_sha256=None,
        ).kind
        is VerificationTargetKind.OUTPUT_CONTRACT
    )


async def test_the_api_process_registers_the_research_verifiers(tmp_path: Path) -> None:
    """The standing gap PR3 closes.

    ``RunManager``'s fallback registry is the hardcoded three, so before M5 the API
    process resolved every research verifier id to ``VerifierUnavailableError`` in
    production while the same ids resolved happily in tests that supplied their own
    registry. Asserted here on the same construction the API performs.
    """

    store = MemoryStore()
    default = RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )
    for verifier_id in sorted(RESEARCH_VERIFIER_IDS):
        with pytest.raises(VerifierUnavailableError):
            default.verifiers.get(verifier_id)

    wired = VerifierRegistry(
        [
            GitDiffVerifier(),
            OutputContractVerifier(),
            TrajectoryPolicyVerifier(),
            *research_verifiers(store),
        ]
    )
    assert set(RESEARCH_VERIFIER_IDS).issubset(wired.list_ids())
    assert wired.list_ids() == sorted(
        {
            "git-diff",
            "output-contract",
            "trajectory-policy",
            *RESEARCH_VERIFIER_IDS,
        }
    )
    # And every id the run manager maps to EXTERNAL_EVIDENCE is actually resolvable,
    # so the branch and the registry cannot drift apart.
    assert len(wired.resolve(sorted(RESEARCH_VERIFIER_IDS))) == 3


# --------------------------------------------------------------------------------------
# The section 27 exit criterion --- "the v0.2 dynamic workflow can use the research
# plugin without provider-specific logic"
# --------------------------------------------------------------------------------------


def _proposal(nodes: list[DynamicWorkflowNodeSpec]) -> WorkflowProposal:
    """A minimal proposal that materialization accepts: one entry, one reachable terminal.

    Chained in the given order so the graph has a single entry node and every node
    reaches the terminal; the fail-closed fallback edges are added by materialization
    itself and are not this test's subject.
    """

    keys = ["start", *(node.local_id for node in nodes), "done"]
    return WorkflowProposal(
        proposal_id=new_id("workflow_proposal"),
        task_id=new_id("task"),
        objective="Gather external research evidence",
        nodes=[
            DynamicWorkflowNodeSpec(
                local_id="start", kind=GraphNodeKind.TASK, objective="start"
            ),
            *nodes,
            DynamicWorkflowNodeSpec(
                local_id="done", kind=GraphNodeKind.TERMINAL, objective="done"
            ),
        ],
        edges=[
            DynamicWorkflowEdgeSpec(
                local_id=f"{source}-to-{target}", source=source, target=target
            )
            for source, target in zip(keys, keys[1:], strict=False)
        ],
        rationale_summary="exit criterion",
        confidence=0.5,
    )


def test_materialization_carries_capability_refs_into_the_executor_graph() -> None:
    """The seam the exit criterion runs through, at the point it used to be severed.

    Before M5 ``_materialize_node`` constructed ``WorkflowNodeSpec`` four times and
    named ``capability_refs`` in none of them, so a capability reference that the
    planner proposed and the validator authorized was silently dropped on the way to
    the executor. Both halves are asserted: a plain node keeps its references, and a
    LOOP puts them on the *act* child rather than on the parent, because the parent is
    a region marker that executes nothing and would otherwise name authority no step
    ever spends.
    """

    template = materialize_workflow_template(
        _proposal(
            [
                DynamicWorkflowNodeSpec(
                    local_id="gather",
                    kind=GraphNodeKind.TOOL,
                    objective="Gather external research evidence",
                    capability_refs=[SEARCH, METADATA],
                ),
                DynamicWorkflowNodeSpec(
                    local_id="refine",
                    kind=GraphNodeKind.LOOP,
                    objective="Refine the search",
                    capability_refs=[SEARCH],
                    loop_spec=DynamicLoopSpec(max_iterations=2),
                ),
            ]
        ),
        normalized_graph_hash="a" * 64,
    )
    by_key = {node.key: node for node in template.nodes}

    # Order is the proposal's, not a set's: two runs of one template must spend the
    # same capabilities in the same sequence.
    assert by_key["gather"].capability_refs == [SEARCH, METADATA]
    assert by_key["refine"].capability_refs == []
    assert by_key["refine-act"].capability_refs == [SEARCH]
    assert by_key["refine-observe"].capability_refs == []
    # Nothing provider-specific crossed the seam --- no connector, endpoint, tool name
    # or wire shape appears anywhere in the materialized template.
    serialized = template.model_dump_json()
    for provider in (OPENALEX_CONNECTOR, CROSSREF_CONNECTOR, OPENALEX_HOST, CROSSREF_HOST):
        assert provider not in serialized


def test_node_spec_without_capability_refs_still_deserializes() -> None:
    """Every workflow template persisted before M5 must still load.

    ``WorkflowNodeSpec`` is a ``StrictModel`` with ``extra="forbid"``, so a required
    field here would reject every stored template at once. The bytes below are a
    pre-M5 node exactly as the database holds it.
    """

    node = WorkflowNodeSpec.model_validate_json(
        '{"schema_version":"1.0","key":"gather","kind":"TOOL","label":"Gather"}'
    )
    assert node.capability_refs == []


async def test_tool_node_capability_ref_drives_a_governed_research_call(
    tmp_path: Path,
) -> None:
    """The exit criterion end to end, through the real scheduler seam.

    The workflow names one canonical capability id and nothing else. The invoker
    resolves it, the gateway executes it against the real MCP path, and the evidence
    lands in the store with provenance naming a connector the workflow never mentioned
    --- which is precisely "uses the research plugin without provider-specific logic".
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        manager = RunManager(
            store=stack.store,
            worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
            runtimes={Provider.FAKE: FakeRuntime()},
            limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
            live_providers_enabled=False,
        )
        manager.capability_invoker = GatewayCapabilityInvoker(
            resolver=stack.resolver,
            gateway=stack.gateway,
            principal_id=PRINCIPAL,
            workspace_id=WORKSPACE,
        )
        spec = WorkflowNodeSpec(
            key="gather",
            kind=GraphNodeKind.TOOL,
            label="Gather external research evidence",
            instruction=QUERY,
            capability_refs=[SEARCH],
        )
        node = RunNode(node_id=spec.key, key=spec.key, kind=spec.kind, label=spec.label)

        assert await stack.store.list_research_evidence(run.run_id) == []
        await manager._invoke_node_capabilities(run, node, spec)

        stored = await stack.store.list_research_evidence(run.run_id)
        assert stored, "a capability-bearing TOOL node produced no evidence"
        for record in stored:
            assert record.node_id == spec.key
            assert record.candidate.provenance.capability_id == SEARCH
            assert record.candidate.provenance.query == QUERY
            # The connector is discovered, never declared: the node named no backend.
            assert record.candidate.provenance.connector_id == OPENALEX_CONNECTOR
            assert record.trust is EvidenceTrust.UNVERIFIED
            assert record.trust_score is None


async def test_empty_capability_refs_invoke_nothing(tmp_path: Path) -> None:
    """The compatibility half of the seam, asserted rather than assumed.

    Every workflow that existed before M5 has empty ``capability_refs``, and the
    invoker must not be reached for any of them --- not reached and returning early,
    but not reached at all, which is what makes the pre-M5 TOOL path byte-identical.
    """

    calls: list[str] = []

    async def recording(
        *, run_id: str, node_id: str, capability_id: str, arguments: dict[str, object]
    ) -> None:
        calls.append(capability_id)
        return None

    async with research_stack() as stack:
        run = await make_run(stack.store, [])
        manager = RunManager(
            store=stack.store,
            worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
            runtimes={Provider.FAKE: FakeRuntime()},
            limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
            live_providers_enabled=False,
        )
        assert manager.capability_invoker is None
        manager.capability_invoker = recording
        spec = WorkflowNodeSpec(key="gather", kind=GraphNodeKind.TOOL, label="Gather")
        node = RunNode(node_id=spec.key, key=spec.key, kind=spec.kind, label=spec.label)

        await manager._invoke_node_capabilities(run, node, spec)

        assert calls == []
        assert await stack.store.list_research_evidence(run.run_id) == []


async def test_research_evidence_route_returns_the_stores_deterministic_order(
    tmp_path: Path,
) -> None:
    """The read-only projection, gated and ordered.

    Three claims: a non-member is refused, the response is the store's own
    ``(created_at, evidence_id)`` order rather than whatever the ORM returned, and the
    route is read-only --- there is no HTTP verb here that can write or relabel a
    record, because trust is assigned on the gateway's execution path and nowhere else.
    """

    async with research_stack() as stack:
        run = await make_run(stack.store, [SEARCH])
        manager = RunManager(
            store=stack.store,
            worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
            runtimes={Provider.FAKE: FakeRuntime()},
            limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
            live_providers_enabled=False,
        )
        manager.capability_invoker = GatewayCapabilityInvoker(
            resolver=stack.resolver,
            gateway=stack.gateway,
            principal_id=PRINCIPAL,
            workspace_id=WORKSPACE,
        )
        await manager._invoke_node_capabilities(
            run,
            RunNode(node_id="gather", key="gather", kind=GraphNodeKind.TOOL, label="Gather"),
            WorkflowNodeSpec(
                key="gather",
                kind=GraphNodeKind.TOOL,
                label="Gather",
                instruction=QUERY,
                capability_refs=[SEARCH],
            ),
        )
        expected = await stack.store.list_research_evidence(run.run_id)
        assert expected

        principal = await stack.store.get_principal(PRINCIPAL)
        assert principal is not None
        app.state.manager = manager
        app.state.auth = AuthRuntime(
            mode="LOCAL_PRINCIPAL",
            identity=IdentityService(stack.store),
            cookie_name="accretion_session",
            cookie_secure=False,
            session_ttl_seconds=3600,
            local_principal_cache=principal,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/runs/{run.run_id}/research-evidence",
                params={"workspace_id": WORKSPACE},
            )
            assert response.status_code == 200, response.text
            assert [item["evidence_id"] for item in response.json()] == [
                record.evidence_id for record in expected
            ]

            # The gate is real: a workspace this principal does not belong to is
            # refused before the run is even looked up.
            denied = await client.get(
                f"/api/v1/runs/{run.run_id}/research-evidence",
                params={"workspace_id": "workspace_not_mine"},
            )
            assert denied.status_code == 403, denied.text

            # And the capability filter is the store's, not a re-sort in the route.
            filtered = await client.get(
                f"/api/v1/runs/{run.run_id}/research-evidence",
                params={"workspace_id": WORKSPACE, "capability_id": METADATA},
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json() == []
