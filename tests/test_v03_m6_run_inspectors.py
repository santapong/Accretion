"""Run-inspector evidence for v0.3 M6 (PR6).

Three surfaces of the run page are proved here, and the fixtures vitest renders are
generated from this run rather than written by hand:

* the React Flow capability badges (`AC3-UI-05`) project persisted
  `CapabilityExecutionResult` rows, so this module executes a *real* governed
  capability through `CapabilityGateway` --- connector, connection and binding
  resolved, credential minted by the real broker --- and commits what the store kept;
* the graph diff (`V02-UI-003`) must name identities, so the fixture is what
  `DynamicWorkflowService.graph_diff` returns over two really-activated revisions;
* the router inspector (`V02-UI-006`) must show the fallback order and the observed
  features, so the fixture is what `PerformanceAwareRuntimeRouter` really decided.

The badge module is also checked structurally: a badge that could reach the API client
would stop being a read-only projection of the audit, which is the property
`AC3-UI-05` actually asserts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AuthMode,
    Capability,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    CapabilityExecutionStatus,
    CapabilityRequest,
    Connection,
    ConnectionRef,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    EventType,
    GraphEdgeKind,
    MetaPluginManifest,
    PluginCapabilityGrant,
    PluginInstallation,
    PluginState,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunState,
    RuntimeHealth,
    RuntimeStatus,
    Task,
    TaskEnvelope,
    UsagePressure,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    seed_governance,
)
from accretion.ids import new_id
from accretion.oauth import OAuthTokenResponse
from accretion.orchestration.models import (
    ConditionOperator,
    GraphValidationStatus,
    ReplanReason,
    TypedCondition,
)
from accretion.orchestration.router import PerformanceAwareRuntimeRouter
from accretion.orchestration.service import DynamicWorkflowService
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.plugins.registration import (
    plugin_connector_id,
    project_bindings,
    project_capabilities,
    project_connector,
)
from accretion.runtimes.fake import FakeRuntime
from accretion.secrets_store import EnvelopeSecretStore
from accretion.services.run_manager import RunManager
from accretion.token_broker import EncryptedTokenBroker
from accretion.workspace import WorktreeManager

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "apps" / "ui" / "src" / "__fixtures__"
UI_SRC = REPO_ROOT / "apps" / "ui" / "src"

#: Set to regenerate the committed fixtures after an intended payload change.
REGENERATE = os.environ.get("ACCRETION_REGENERATE_UI_FIXTURES") == "1"

#: A credential value that must never reach a rendered fixture.
SENTINEL = "gho_m6_run_inspector_sentinel_value"

CONNECTOR_ID = "conndef_m6_github"
#: The installed plugin whose capability the third badged node is served by. Its
#: connector id is minted by the real registration layer, never spelled out here.
PLUGIN_ID = "m6-badges"
PLUGIN_CAPABILITY_ID = "m6-badges.summarize"
ISSUER = "https://authorization.test"
RESOURCE = "https://api.test"

_FIXED_INSTANT = "2026-08-28T00:00:00Z"
_TIME_FIELDS = frozenset({"created_at", "completed_at", "activated_at", "expires_at"})
#: A minted id: a ``new_id`` ULID or a uuid-suffixed test id. Rewritten in first-seen
#: order so cross-references survive while the committed bytes stay put.
_MINTED_ID = re.compile(r"^([a-z][a-z0-9_]{1,20})_(?:[0-9A-Z]{26}|[0-9a-f]{12})$")


class Aliases:
    """A stable rename for every id minted during one exercise."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def rename(self, value: str) -> str:
        match = _MINTED_ID.match(value)
        if match is None:
            return self._rename_embedded(value)
        if value in self.seen:
            return self.seen[value]
        prefix = match.group(1)
        index = self.counts.get(prefix, 0) + 1
        self.counts[prefix] = index
        alias = f"{prefix}_m6_{index:02d}"
        self.seen[value] = alias
        return alias

    def _rename_embedded(self, value: str) -> str:
        """Rewrite ids embedded in a compound identity such as ``run_X:act``."""

        if ":" not in value:
            return value
        head, _, tail = value.partition(":")
        renamed = self.rename(head)
        return f"{renamed}:{tail}" if renamed != head else value


def stabilize(payload: Any, aliases: Aliases) -> Any:
    if isinstance(payload, dict):
        return {
            key: _FIXED_INSTANT
            if key in _TIME_FIELDS and isinstance(value, str)
            else stabilize(value, aliases)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [stabilize(item, aliases) for item in payload]
    if isinstance(payload, str):
        return aliases.rename(payload)
    return payload


def assert_fixture(name: str, payload: Any, aliases: Aliases) -> Any:
    """Compare one generated payload against its committed file, byte for byte."""

    path = FIXTURE_DIR / name
    generated = (
        json.dumps(stabilize(payload, aliases), indent=2, sort_keys=True) + "\n"
    ).encode()
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generated)
    assert path.is_file(), (
        f"missing UI fixture {path}; regenerate with ACCRETION_REGENERATE_UI_FIXTURES=1"
    )
    assert path.read_bytes() == generated, (
        f"{path} is stale: the live code no longer produces it. Re-run with "
        "ACCRETION_REGENERATE_UI_FIXTURES=1 and review the diff before committing."
    )
    return json.loads(generated)


class StaticKey:
    key_id = "m6-1"

    def material(self) -> bytes:
        return b"M" * 32


async def _handler(arguments: dict[str, Any], credentials: Any) -> dict[str, Any]:
    del credentials
    return {"ok": True, "echo": arguments.get("message", "")}


async def governed_gateway() -> tuple[
    MemoryStore, CapabilityGateway, Run, ConnectionRef, CapabilityBinding, CapabilityBinding
]:
    """A run whose capabilities really resolve onto connectors, connections and bindings.

    Two provenances are built: an OAuth connector-backed capability, and a capability
    served by an *installed plugin*, bound through the synthetic local connector
    ``plugin_connector_id`` mints. `AC3-UI-05` names plugin metadata, so the plugin half
    has to come from the real registration projection rather than a hand-written id.
    """

    suffix = uuid.uuid4().hex[:12]
    store = MemoryStore()
    await seed_governance(store)
    project = Project(project_id=f"project_{suffix}", name="M6 run inspector", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=f"task_{suffix}",
            project_id=project.project_id,
            objective="Execute a connector-backed capability the run page badges.",
            allowed_capabilities=[
                "fixture.badged",
                "accretion.echo",
                PLUGIN_CAPABILITY_ID,
            ],
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=f"run_{suffix}",
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    await store.create_run(run)

    connector = ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="GitHub",
        kind=ConnectorKind.REST,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=ISSUER,
        resource_server=RESOURCE,
        default_scopes=["repo:read"],
    )
    await store.upsert_connector_definition(connector)
    broker = EncryptedTokenBroker(store, EnvelopeSecretStore(StaticKey()))
    handle = await broker.store_authorization(
        connector=connector,
        principal_id=f"prin_{suffix}",
        workspace_id=f"workspace_{suffix}",
        response=OAuthTokenResponse(
            access_token=SENTINEL, refresh_token="ghr_m6_refresh", granted_scopes=["repo:read"]
        ),
    )
    connection = Connection(
        connection_id=f"conn_{suffix}",
        connector_id=CONNECTOR_ID,
        workspace_id=f"workspace_{suffix}",
        principal_id=f"prin_{suffix}",
        scope=ConnectionScope.USER,
        status=ConnectionStatus.ACTIVE,
        granted_scopes=["repo:read"],
        token_handle_ref=handle.token_handle_id,
    )
    await store.upsert_connection(connection)
    await store.upsert_capability(
        Capability(
            capability_id="fixture.badged",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object"},
            risk=RiskLevel.LOW,
            backend=CapabilityBackend.PYTHON,
        )
    )
    binding = CapabilityBinding(
        binding_id=f"capbind_{suffix}",
        capability_id="fixture.badged",
        connector_id=CONNECTOR_ID,
        backend=CapabilityBindingBackend(type=CapabilityBackend.HTTP, method="GET"),
    )
    plugin_binding = await install_plugin_capability(store)
    gateway = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=CredentialBroker(),
        executor=CapabilityExecutor(
            {
                "fixture.badged": _handler,
                "accretion.echo": _handler,
                PLUGIN_CAPABILITY_ID: _handler,
            }
        ),
        policy_engine=CapabilityPolicyEngine(),
        token_broker=broker,
    )
    reference = ConnectionRef(
        connection_id=connection.connection_id,
        connector_id=CONNECTOR_ID,
        status=connection.status,
    )
    return store, gateway, run, reference, binding, plugin_binding


async def install_plugin_capability(store: MemoryStore) -> CapabilityBinding:
    """Register one granted plugin capability exactly as an installation would.

    The connector, the projected capability and the binding all come from
    ``accretion.plugins.registration``, so the connector id the badge reads the plugin
    identity out of is the one a real install writes.
    """

    manifest = MetaPluginManifest(
        id=PLUGIN_ID,
        version="1.0.0",
        name="M6 badges",
        capabilities=[
            Capability(
                capability_id=PLUGIN_CAPABILITY_ID,
                version="1.0.0",
                input_schema={"type": "object", "additionalProperties": True},
                output_schema={"type": "object"},
                risk=RiskLevel.LOW,
                backend=CapabilityBackend.PYTHON,
            )
        ],
    )
    installation = PluginInstallation(
        installation_id=f"plgi_{uuid.uuid4().hex[:12]}",
        workspace_id=f"workspace_{uuid.uuid4().hex[:12]}",
        plugin_id=PLUGIN_ID,
        version=manifest.version,
        manifest_digest="c" * 64,
        state=PluginState.ENABLED,
        requested_capability_ids=[PLUGIN_CAPABILITY_ID],
        capability_grants=[PluginCapabilityGrant(capability_id=PLUGIN_CAPABILITY_ID)],
    )
    await store.upsert_connector_definition(project_connector(manifest))
    projected = project_capabilities(manifest, installation.capability_grants, installation)
    for capability in projected:
        await store.upsert_capability(capability)
    binding = project_bindings(projected, installation)[0]
    assert binding.connector_id == plugin_connector_id(PLUGIN_ID)
    return binding


def capability_request(run: Run, node: str, capability_id: str, message: str) -> CapabilityRequest:
    return CapabilityRequest(
        request_id=new_id("capability_request"),
        run_id=run.run_id,
        node_id=f"{run.run_id}:{node}",
        capability_id=capability_id,
        capability_version="1.0.0",
        arguments={"message": message},
        declared_reason="M6 run inspector fixture",
    )


@pytest.mark.acceptance("AC3-UI-05")
async def test_the_badge_fixture_is_what_the_gateway_recorded_for_a_governed_call() -> None:
    """The badges render persisted gateway rows, not a hand-written shape.

    Two nodes are exercised: one connector-backed call, which carries a connector, a
    connection and a binding, and one unbound call, which carries none of them. The
    page must therefore render *different* provenance for two nodes of one run, which
    is what makes the badge non-vacuous.
    """

    store, gateway, run, connection, binding, plugin_binding = await governed_gateway()
    bound = await gateway.execute(
        capability_request(run, "act", "fixture.badged", "badged"),
        connection,
        binding,
    )
    unbound = await gateway.execute(
        capability_request(run, "verify", "accretion.echo", "unbound")
    )
    served = await gateway.execute(
        capability_request(run, "plan", PLUGIN_CAPABILITY_ID, "plugin-served"),
        None,
        plugin_binding,
    )
    assert bound.status is CapabilityExecutionStatus.SUCCEEDED
    assert bound.connector_id == CONNECTOR_ID
    assert bound.connection_id == connection.connection_id
    assert bound.binding_id == binding.binding_id
    assert unbound.connector_id is None
    assert unbound.connection_id is None
    assert unbound.binding_id is None
    # The plugin half of AC3-UI-05: a plugin-served call carries the plugin's synthetic
    # connector, so the badge can name the plugin without a second source of truth.
    assert served.status is CapabilityExecutionStatus.SUCCEEDED
    assert served.connector_id == plugin_connector_id(PLUGIN_ID)
    assert served.connection_id is None
    assert served.binding_id == plugin_binding.binding_id

    results = await store.list_capability_results(run.run_id)
    assert len(results) == 3
    payload = {
        "schema_version": "1.0",
        "capability_results": [item.model_dump(mode="json") for item in results],
    }
    # No credential may travel to the browser inside the audit the badges project.
    assert SENTINEL not in json.dumps(payload)
    written = assert_fixture("run-audit.json", payload, Aliases())
    nodes = {
        item["request"]["node_id"]: item for item in written["capability_results"]
    }
    assert len(nodes) == 3, "the badge fixture must cover three differently-bound nodes"
    badged = next(
        item
        for item in written["capability_results"]
        if item["connector_id"] == CONNECTOR_ID
    )
    assert badged["connection_id"] and badged["binding_id"]
    plugin_served = next(
        item
        for item in written["capability_results"]
        if item["connector_id"] == plugin_connector_id(PLUGIN_ID)
    )
    assert plugin_served["connection_id"] is None
    assert plugin_served["binding_id"]


@pytest.mark.acceptance("AC3-UI-05")
def test_the_badge_projection_cannot_reach_the_api_client() -> None:
    """Structural proof that a badge is a projection, not a control.

    A badge that could import the API client, hold state, or call ``fetch`` would be a
    second, unaudited path to the same authority. jsdom cannot prove the *absence* of
    such a path across every future edit; the module boundary can.
    """

    source = (UI_SRC / "runBadges.ts").read_text()
    imports = re.findall(r'^\s*import\b[^;]*?from\s+"([^"]+)"', source, re.MULTILINE)
    assert imports == ["./types"], f"runBadges.ts must import only ./types, got {imports}"
    assert "api" not in {item.rsplit("/", 1)[-1] for item in imports}
    for forbidden in ("fetch(", "useState", "useQuery", "XMLHttpRequest", "EventSource"):
        assert forbidden not in source, f"runBadges.ts must not contain {forbidden}"
    # And the type-only import cannot carry a runtime value into the module.
    assert re.search(r'^import type \{[^}]*\} from "\./types";', source, re.MULTILINE)


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Accretion Test"], check=True)
    (path / "README.md").write_text("M6 fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


class TopologyEditingPlanner:
    """The real planner, with the replan proposal edited before the service sees it.

    The deterministic fragment planner reproduces the same graph for the same task, so a
    replan diff over its unedited output is six empty lists --- which cannot prove the
    page renders identities. Editing the *proposal* keeps every downstream step real:
    the service still validates it, still refuses to rewrite a completed node, still
    installs it, and still computes the diff.
    """

    def __init__(self, inner: Any, edits: list[Any]) -> None:
        self.inner = inner
        self.edits = edits

    def __getattr__(self, item: str) -> Any:
        return getattr(self.inner, item)

    def propose(self, *args: Any, **kwargs: Any) -> Any:
        proposal = self.inner.propose(*args, **kwargs)
        if kwargs.get("based_on_graph_revision") is None or not self.edits:
            return proposal
        return self.edits.pop(0)(proposal)

    def repair(self, proposal: Any) -> Any:
        return self.inner.repair(proposal)


def insert_review_stage(proposal: Any) -> Any:
    """Add a review node between `act` and `verify`, and reword `act`."""

    nodes = {node.local_id: node for node in proposal.nodes}
    review = nodes["act"].model_copy(
        update={"local_id": "review", "objective": "Review the bounded candidate."}
    )
    act = nodes["act"].model_copy(
        update={"objective": "Produce the bounded candidate for review."}
    )
    edges = [edge for edge in proposal.edges if edge.local_id != "act-verify"]
    act_review = next(
        edge for edge in proposal.edges if edge.local_id == "act-verify"
    ).model_copy(update={"local_id": "act-review", "source": "act", "target": "review"})
    review_verify = act_review.model_copy(
        update={"local_id": "review-verify", "source": "review", "target": "verify"}
    )
    return proposal.model_copy(
        update={
            "nodes": [nodes["start"], act, review, nodes["verify"], nodes["complete"]],
            "edges": [*edges[:1], act_review, review_verify, *edges[1:]],
        }
    )


def retire_review_stage(proposal: Any) -> Any:
    """Drop the review stage again, harden `verify`, and condition its exit edge."""

    nodes = {node.local_id: node for node in proposal.nodes}
    verify = nodes["verify"].model_copy(update={"max_attempts": 2})
    edges = {edge.local_id: edge for edge in proposal.edges}
    exit_edge = edges["verify-complete"].model_copy(
        update={
            "kind": GraphEdgeKind.CONDITION,
            "condition": TypedCondition(
                operator=ConditionOperator.EQ, path="node.outcome", value="SUCCESS"
            )
        }
    )
    return proposal.model_copy(
        update={
            "nodes": [nodes["start"], nodes["act"], verify, nodes["complete"]],
            "edges": [edges["start-act"], edges["act-verify"], exit_edge],
        }
    )


async def replanned_run(
    tmp_path: Path, *, edits: list[Any] | None = None, step_delay: float = 0.1
) -> tuple[RunManager, DynamicWorkflowService, str]:
    """A run with a really-activated graph revision, paused and ready to replan."""

    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime(step_delay=step_delay)},
        limiter=ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
    )
    service = DynamicWorkflowService(manager, globally_enabled=True, operator_identity="m6-test")
    if edits:
        service.planner = TopologyEditingPlanner(service.planner, list(edits))
    project = await manager.create_project("M6", repository)
    current = await service.get_project_features(project.project_id)
    await service.update_project_features(
        project.project_id, dynamic_workflows=True, expected_revision=current.revision
    )
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Review the existing M6 fixture through a validated dynamic graph.",
        task_patch={
            "task_type": "REVIEW",
            "required_outputs": [{"path": "README.md", "kind": "file"}],
        },
    )
    proposal = await service.propose(task.envelope.task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    run_id = proposal.run_id
    outcome = await service.validate(run_id, proposal.proposal_id)
    assert outcome.validation.status is GraphValidationStatus.ACCEPT
    await service.activate(run_id, proposal.proposal_id)
    for _ in range(200):
        events = await manager.store.list_events(run_id)
        if any(
            event.normalized_type is EventType.NODE_EXITED
            and event.node_id == f"{run_id}:start"
            for event in events
        ):
            break
        await asyncio.sleep(0.01)
    await manager.pause(run_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    return manager, service, run_id


@pytest.mark.acceptance("V02-UI-001")
@pytest.mark.acceptance("V02-UI-002")
@pytest.mark.acceptance("V02-UI-003")
@pytest.mark.acceptance("V02-UI-004")
@pytest.mark.acceptance("V02-UI-005")
async def test_the_diff_fixture_names_identities_across_two_real_revisions(
    tmp_path: Path,
) -> None:
    """`graph_diff` over two really-activated revisions, committed for the run page.

    The same exercise carries the inherited planner-inspector criteria: the proposal
    the page renders really has assumptions, required capabilities and a validation
    verdict (`V02-UI-001`); the earlier revision survives the replan byte-for-byte and
    is still listed (`V02-UI-002`); a proposal that has not been activated is not the
    active revision (`V02-UI-004`); and the replan keeps the pre-replan trace
    (`V02-UI-005`).
    """

    manager, service, run_id = await replanned_run(
        tmp_path, edits=[insert_review_stage, retire_review_stage], step_delay=0.5
    )
    proposals = await manager.store.list_workflow_proposals(run_id=run_id)
    first = proposals[0]
    assert first.assumptions, "the planner inspector renders the proposal's assumptions"
    assert first.fragment_refs and first.rationale_summary
    assert 0 < first.confidence <= 1
    validations = await manager.store.list_graph_validations(first.proposal_id)
    assert validations, "the planner inspector renders a validation verdict"

    before = await manager.store.get_graph_revision(run_id, 1)
    assert before is not None
    events_before = await manager.store.list_events(run_id)
    trace_before = await manager.get_trace(run_id)

    # V02-UI-004: the proposal exists before any revision does, so a pending proposal
    # is never mistaken for the executable graph.
    revisions_before = await manager.store.list_graph_revisions(run_id)
    assert [item.revision for item in revisions_before] == [1]
    assert len(proposals) >= len(revisions_before)

    replan = await service.replan(
        run_id, reason=ReplanReason.HUMAN_REQUEST, evidence_refs=["operator:m6-fixture"]
    )
    assert replan.revision is not None and replan.revision.revision == 2
    # V02-UI-002: the prior revision is untouched and still reachable.
    assert await manager.store.get_graph_revision(run_id, 1) == before
    assert [item.revision for item in await manager.store.list_graph_revisions(run_id)] == [1, 2]
    # V02-UI-005: the replan appended to the history rather than replacing it.
    events_after = await manager.store.list_events(run_id)
    assert [item.event_id for item in events_after][: len(events_before)] == [
        item.event_id for item in events_before
    ]
    trace_after = await manager.get_trace(run_id)
    assert len(trace_after.traversals) >= len(trace_before.traversals)

    forward = await service.graph_diff(run_id, 1, 2)

    rollback = await service.replan(
        run_id, reason=ReplanReason.HUMAN_REQUEST, evidence_refs=["operator:m6-rollback"]
    )
    assert rollback.revision is not None and rollback.revision.revision == 3
    backward = await service.graph_diff(run_id, 2, 3)

    aliases = Aliases()
    written_forward = assert_fixture("graph-diff.json", forward.model_dump(mode="json"), aliases)
    written_backward = assert_fixture(
        "graph-diff-rollback.json", backward.model_dump(mode="json"), aliases
    )

    sections = (
        "added_nodes",
        "removed_nodes",
        "changed_nodes",
        "added_edges",
        "removed_edges",
        "changed_edges",
    )
    # Every one of the six lists the page must render carries an identity in at least
    # one of the two committed diffs, so no list can be dropped from the page and still
    # pass its vitest differential.
    for key in sections:
        for written in (written_forward, written_backward):
            assert isinstance(written[key], list)
            assert all(isinstance(item, str) and item for item in written[key]), key
        assert written_forward[key] or written_backward[key], (
            f"neither committed diff exercises {key}"
        )
    assert written_forward["added_nodes"] == ["review"]
    assert written_backward["removed_nodes"] == ["review"]
    assert written_forward["removed_edges"] == ["act-verify"]
    assert written_backward["changed_edges"] == ["verify-complete"]

    resumed = manager.background.get(run_id)
    if resumed is not None:
        await resumed


@pytest.mark.acceptance("V02-UI-006")
async def test_the_router_fixture_carries_a_fallback_order_and_observed_features() -> None:
    """The decision the page renders is what the production router really returned."""

    router = PerformanceAwareRuntimeRouter()
    health = [
        RuntimeHealth(
            runtime_id=f"runtime_{provider.value.lower()}",
            provider=provider,
            runtime_version=version,
            status=status,
            auth_mode=AuthMode.API,
            observed_usage_pressure=pressure,
        )
        for provider, version, status, pressure in (
            (Provider.CLAUDE, "1.4.0", RuntimeStatus.READY, UsagePressure.LOW),
            (Provider.CODEX, "0.9.0", RuntimeStatus.BUSY, UsagePressure.MEDIUM),
            (Provider.FAKE, "1.0.0", RuntimeStatus.READY, UsagePressure.HIGH),
        )
    ]
    decision = router.decide(
        run_id="run_m6_router",
        node_id="run_m6_router:act",
        health=health,
        historical_quality={
            (Provider.CLAUDE, "1.4.0"): 0.91,
            (Provider.CODEX, "0.9.0"): 0.74,
            (Provider.FAKE, "1.0.0"): 0.5,
        },
        expected_latency={Provider.CLAUDE: 0.2, Provider.CODEX: 0.4, Provider.FAKE: 0.1},
        specialization_fit={Provider.CLAUDE: 0.8, Provider.CODEX: 0.6, Provider.FAKE: 0.3},
        risk_penalty={Provider.CODEX: 0.2},
    )
    assert decision.selected_runtime is not None
    assert len(decision.fallback_order) >= 2, "the page renders an ordered fallback chain"
    assert decision.observed_features, "the page renders every observed feature"
    payload = [decision.model_dump(mode="json")]
    serialized = json.dumps(payload)
    assert SENTINEL not in serialized
    written = assert_fixture("runtime-decisions.json", payload, Aliases())
    assert written[0]["fallback_order"] == [item.value for item in decision.fallback_order]
    assert written[0]["observed_features"] == decision.observed_features
