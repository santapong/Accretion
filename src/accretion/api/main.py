from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, cast
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from accretion import __version__
from accretion.api.auth import (
    auth_runtime,
    authenticate_request,
    build_auth_runtime,
    is_exempt,
)
from accretion.api.auth import principal as current_principal
from accretion.api.schemas import (
    ApprovalDecisionCreate,
    AuthorizationStart,
    AuthProviderInfo,
    BenchmarkRunCreate,
    CapabilityResolveRequest,
    ConnectCreate,
    ConnectionSummary,
    ErrorEnvelope,
    ExperienceMaterializeCreate,
    ExperienceQueryCreate,
    ExperienceRetractCreate,
    ExperienceSelectionCreate,
    McpServerCreate,
    MeResponse,
    PluginInstallRequest,
    PluginWorkspaceRequest,
    ProjectCreate,
    ProjectFeatureUpdate,
    ReplanCreate,
    RunCreate,
    SearchCreate,
    StrategyOverrideCreate,
    TaskCreate,
    WorkflowProposeCreate,
    WorkflowTemplateSummary,
)
from accretion.benchmark import (
    AcrArchRunner,
    acr_arch_summary,
    acr_arch_task_detail,
    seed_acr_arch,
)
from accretion.concurrency import ConcurrencyLimiter
from accretion.config import get_settings
from accretion.connections import ConnectionError as ConnectionServiceError
from accretion.connections import ConnectionService
from accretion.connectors import GITHUB_CONNECTOR_ID, github_connector, github_endpoints
from accretion.contracts import (
    TERMINAL_RUN_STATES,
    AcrArchSummary,
    AgentRuntime,
    ApprovalRecord,
    ApprovalStatus,
    ArtifactRef,
    BenchmarkExecutionSource,
    BenchmarkRun,
    BenchmarkTaskDetail,
    Capability,
    Connection,
    ConnectionScope,
    ConnectorDefinition,
    EventType,
    EvidenceRecord,
    ExecutionMode,
    ExecutionTrace,
    GraphProjection,
    LoopExecution,
    McpDiscoverySnapshot,
    McpServerDefinition,
    MetaPlugin,
    MetaSkill,
    PluginAuditEvent,
    PluginInstallation,
    Project,
    Provider,
    ResolvedCapability,
    Run,
    RunAudit,
    RuntimeHealth,
    SessionRef,
    StrategyOverrideResult,
    Task,
    TaskPlanning,
    TaskProfile,
    TaskType,
    TemplateStatus,
    VerificationResult,
    WorkspaceEntity,
    WorkspaceRole,
)
from accretion.dynamic_benchmark import (
    DynamicWorkflowBenchmarkRunner,
    DynamicWorkflowBenchmarkSummary,
)
from accretion.enterprise_auth import build_enterprise_auth_manager
from accretion.experience.models import (
    Experience,
    ExperienceDetail,
    ExperienceMatch,
    ExperienceSelection,
    TrajectorySeed,
)
from accretion.experience.service import (
    ExperienceConflictError,
    ExperienceDisabledError,
    ExperienceService,
)
from accretion.experience_benchmark import (
    ExperienceBenchmarkRunner,
    ExperienceBenchmarkSummary,
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
from accretion.identity import AuthenticationError, AuthorizationError
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy, McpEndpointPolicyError
from accretion.mcp.manager import (
    McpManagerError,
    McpServerAuthRequired,
    RemoteMcpManager,
)
from accretion.mcp.remote_client import SdkRemoteMcpClient
from accretion.oauth import OAuthClient
from accretion.orchestration.models import (
    CandidateScore,
    CandidateTrajectory,
    GraphRevisionDiff,
    GraphValidationResult,
    ProjectFeatureSettings,
    ReplanOutcome,
    ReplanRequest,
    RunGraphRevision,
    RuntimeDecision,
    SearchRecord,
    WorkflowActivationOutcome,
    WorkflowProposal,
    WorkflowValidationOutcome,
)
from accretion.orchestration.search import (
    CandidateSearchConflictError,
    CandidateSearchDisabledError,
    SearchService,
)
from accretion.orchestration.service import (
    DynamicWorkflowConflictError,
    DynamicWorkflowDisabledError,
    DynamicWorkflowService,
)
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.side_effects import PostgresSideEffectLedger
from accretion.persistence.store import PostgresStore
from accretion.plugins.errors import (
    PluginDependencyError,
    PluginManagerError,
    PluginManifestError,
    PluginPolicyDenied,
    PluginSignatureError,
    PluginTrustError,
)
from accretion.plugins.manager import PluginManager
from accretion.plugins.registration import PluginDetail
from accretion.plugins.trust import PluginTrustVerifier, load_trusted_keys
from accretion.research.transforms import default_transform_registry
from accretion.resolver import CapabilityResolver
from accretion.runtimes import ClaudeRuntime, CodexRuntime, FakeRuntime, OpencodeRuntime
from accretion.search_benchmark import SearchBenchmarkRunner, SearchBenchmarkSummary
from accretion.secrets_store import EnvelopeSecretStore
from accretion.services.run_manager import (
    ProjectionUnavailableError,
    RunManager,
    WorkflowTemplateError,
)
from accretion.templates import seed_templates
from accretion.token_broker import EncryptedTokenBroker
from accretion.verifiers.git_diff import GitDiffVerifier
from accretion.verifiers.output_contract import OutputContractVerifier
from accretion.verifiers.registry import VerifierRegistry, VerifierUnavailableError
from accretion.verifiers.research import research_verifiers
from accretion.verifiers.trajectory import TrajectoryPolicyVerifier
from accretion.workspace import WorktreeManager

SSE_TERMINAL_EVENTS = {
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
}


class ExecutionModeMismatchError(RuntimeError):
    def __init__(self, run: Run) -> None:
        mode = run.execution_mode.value if run.execution_mode is not None else "UNKNOWN"
        template = run.workflow_template_id or "unknown-template"
        super().__init__(
            f"Run {run.run_id} uses {mode}/{template}; this endpoint requires LOOP/feedback-loop-v1"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    gateway_environment = {
        "ACCRETION_DATABASE_URL": settings.database_url,
        "ACCRETION_CAPABILITY_POLICY_ID": settings.capability_policy_id,
        "ACCRETION_GRANTED_PERMISSIONS": json.dumps(settings.granted_permissions),
        "ACCRETION_CREDENTIAL_ENV_MAP": json.dumps(settings.credential_env_map),
    }
    runtimes: dict[Provider, AgentRuntime] = {
        Provider.FAKE: FakeRuntime(),
        Provider.CODEX: CodexRuntime(settings.codex_command, gateway_environment),
        Provider.CLAUDE: ClaudeRuntime(settings.claude_command, gateway_environment),
        Provider.OPENCODE: OpencodeRuntime(
            settings.opencode_command,
            gateway_environment,
            model=settings.opencode_model,
        ),
    }
    secrets_store = EnvelopeSecretStore()
    token_broker = (
        EncryptedTokenBroker(store, secrets_store)
        if settings.token_encryption_key
        else None
    )
    # Optional, off by default, and inert without an exchange endpoint (OQ3-08).
    enterprise_auth = build_enterprise_auth_manager(
        store, secrets_store, token_broker, settings
    )
    app.state.enterprise_auth = enterprise_auth
    if token_broker is not None and settings.github_client_id:
        github = github_connector(
            authorization_server=settings.github_authorization_server
        )
        await store.upsert_connector_definition(github)
        app.state.connections = ConnectionService(
            store=store,
            broker=token_broker,
            clients={
                GITHUB_CONNECTOR_ID: OAuthClient(
                    client_id=settings.github_client_id,
                    client_secret=settings.github_client_secret,
                    redirect_url=settings.github_redirect_url,
                    endpoints=github_endpoints(settings.github_authorization_server),
                    http=httpx.AsyncClient(),
                )
            },
        )
    app.state.remote_mcp = RemoteMcpManager(
        store=store,
        client=SdkRemoteMcpClient(),
        endpoint_policy=McpEndpointPolicy(
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_ports=settings.mcp_allowed_ports,
            allow_local_http=settings.mcp_allow_local_http,
        ),
        token_broker=token_broker,
    )
    app.state.plugins = PluginManager(
        store=store,
        trust_verifier=PluginTrustVerifier(
            trusted_keys=load_trusted_keys(settings.plugin_trusted_keys),
            allow_unverified_dev=settings.plugin_allow_unverified_dev,
            builtin_ids=settings.plugin_builtin_ids,
        ),
        policy_engine=CapabilityPolicyEngine(set(settings.granted_permissions)),
        remote_mcp=app.state.remote_mcp,
        policy_id=settings.capability_policy_id,
    )
    manager = RunManager(
        store=store,
        worktrees=WorktreeManager(settings.worktree_dir, settings.artifact_dir),
        runtimes=runtimes,
        limiter=ConcurrencyLimiter(
            global_limit=settings.global_max_runs,
            provider_limit=settings.provider_max_runs,
            project_limit=settings.project_max_runs,
        ),
        live_providers_enabled=settings.enable_live_providers,
        side_effect_ledger=PostgresSideEffectLedger(sessions),
        operator_identity=settings.operator_identity,
        # Built explicitly rather than left to RunManager's default. The default is
        # the hardcoded three, so until M5 the API process resolved every research
        # verifier id to VerifierUnavailableError in production while the same ids
        # resolved fine in tests that passed their own registry — a gap visible only
        # once a policy actually required one. The registry is now assembled in one
        # place and the research verifiers read evidence from the real store.
        verifier_registry=VerifierRegistry(
            [
                GitDiffVerifier(),
                OutputContractVerifier(),
                TrajectoryPolicyVerifier(),
                *research_verifiers(store),
            ]
        ),
        auto_resume_on_reconcile=settings.auto_resume_on_reconcile,
    )
    app.state.engine = engine
    app.state.manager = manager
    # The section 27 exit seam, wired for the API process. Without this the scheduler
    # holds a `capability_invoker` of `None` in production and a real one only in
    # tests --- the same asymmetry the verifier registry above had until M5. The
    # gateway is the governed path: policy engine, token broker and side-effect ledger
    # are all the production objects, and the transform registry is supplied so the
    # research bindings' `output_transform_ref` resolves here as it does in the MCP
    # gateway process.
    manager.capability_invoker = GatewayCapabilityInvoker(
        resolver=CapabilityResolver(store),
        gateway=CapabilityGateway(
            store=store,
            side_effects=PostgresSideEffectLedger(sessions),
            broker=CredentialBroker(settings.credential_env_map),
            executor=CapabilityExecutor(default_capability_handlers()),
            policy_engine=CapabilityPolicyEngine(set(settings.granted_permissions)),
            policy_id=settings.capability_policy_id,
            token_broker=token_broker,
            remote_mcp=app.state.remote_mcp,
            transforms=default_transform_registry(),
        ),
    )
    app.state.dynamic_workflows = DynamicWorkflowService(
        manager,
        globally_enabled=settings.enable_dynamic_workflows,
        operator_identity=settings.operator_identity,
    )
    experience = ExperienceService(
        manager,
        globally_enabled=settings.enable_experience_retrieval,
        operator_identity=settings.operator_identity,
    )
    app.state.experience = experience
    search_service = SearchService(
        manager,
        globally_enabled=settings.enable_candidate_search,
        operator_identity=settings.operator_identity,
        experience_service=experience,
    )
    app.state.candidate_search = search_service
    app.state.auth = build_auth_runtime(store, settings, enterprise_auth=enterprise_auth)
    await seed_templates(store)
    await seed_governance(store)
    await seed_acr_arch(store)
    await search_service.reconcile()
    await manager.reconcile()
    yield
    for task in manager.background.values():
        if not task.done():
            task.cancel()
    for runtime in runtimes.values():
        closer = getattr(runtime, "close", None)
        if callable(closer):
            # One adapter failing to shut down must not orphan another's server process.
            with suppress(Exception):
                await closer()
    await engine.dispose()


app = FastAPI(title="Accretion API", version=__version__, lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Last-Event-ID", "X-Request-ID"],
)


@app.middleware("http")
async def session_middleware(request: Request, call_next: Any) -> Any:
    if request.method == "OPTIONS" or is_exempt(request.url.path):
        return await call_next(request)
    try:
        request.state.principal = await authenticate_request(request)
    except AuthenticationError as exc:
        return _error(401, "UNAUTHENTICATED", str(exc))
    except AuthorizationError as exc:
        return _error(403, "FORBIDDEN", str(exc))
    return await call_next(request)


def manager(request: Request) -> RunManager:
    return cast(RunManager, request.app.state.manager)


def dynamic_workflows(request: Request) -> DynamicWorkflowService:
    return cast(DynamicWorkflowService, request.app.state.dynamic_workflows)


def candidate_search(request: Request) -> SearchService:
    return cast(SearchService, request.app.state.candidate_search)


def experience_service(request: Request) -> ExperienceService:
    return cast(ExperienceService, request.app.state.experience)


def remote_mcp(request: Request) -> RemoteMcpManager:
    service = getattr(request.app.state, "remote_mcp", None)
    if service is None:
        raise ValueError("remote MCP manager is unavailable")
    return cast(RemoteMcpManager, service)


def plugins(request: Request) -> PluginManager:
    service = getattr(request.app.state, "plugins", None)
    if service is None:
        raise ValueError("plugin manager is unavailable")
    return cast(PluginManager, service)


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
    return _error(404, "NOT_FOUND", f"Resource {exc.args[0]} was not found")


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return _error(403, "PROVIDER_DISABLED", str(exc))


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return _error(400, "INVALID_REQUEST", str(exc))


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(
    request: Request, exc: AuthenticationError
) -> JSONResponse:
    return _error(401, "UNAUTHENTICATED", str(exc))


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(
    request: Request, exc: AuthorizationError
) -> JSONResponse:
    return _error(403, "FORBIDDEN", str(exc))


@app.exception_handler(ConnectionServiceError)
async def connection_error_handler(
    request: Request, exc: ConnectionServiceError
) -> JSONResponse:
    # One shape for unknown, replayed, expired, and cross-principal states, so a
    # caller cannot probe which of those it hit (AC3-SEC-04).
    return _error(400, "CONNECTION_REJECTED", str(exc))


@app.exception_handler(McpEndpointPolicyError)
async def mcp_endpoint_policy_handler(
    request: Request, exc: McpEndpointPolicyError
) -> JSONResponse:
    return _error(400, "MCP_ENDPOINT_BLOCKED", str(exc))


@app.exception_handler(McpServerAuthRequired)
async def mcp_auth_required_handler(
    request: Request, exc: McpServerAuthRequired
) -> JSONResponse:
    return _error(409, "MCP_AUTH_REQUIRED", str(exc))


@app.exception_handler(McpManagerError)
async def mcp_manager_error_handler(
    request: Request, exc: McpManagerError
) -> JSONResponse:
    return _error(409, "MCP_SERVER_REJECTED", str(exc))


@app.exception_handler(PluginManifestError)
async def plugin_manifest_handler(
    request: Request, exc: PluginManifestError
) -> JSONResponse:
    return _error(400, "PLUGIN_MANIFEST_INVALID", str(exc))


@app.exception_handler(PluginSignatureError)
async def plugin_signature_handler(
    request: Request, exc: PluginSignatureError
) -> JSONResponse:
    return _error(400, "PLUGIN_SIGNATURE_INVALID", str(exc))


@app.exception_handler(PluginTrustError)
async def plugin_trust_handler(request: Request, exc: PluginTrustError) -> JSONResponse:
    return _error(403, "PLUGIN_TRUST_INSUFFICIENT", str(exc))


@app.exception_handler(PluginPolicyDenied)
async def plugin_policy_denied_handler(
    request: Request, exc: PluginPolicyDenied
) -> JSONResponse:
    return _error(403, "PLUGIN_POLICY_DENIED", str(exc))


@app.exception_handler(PluginDependencyError)
async def plugin_dependency_handler(
    request: Request, exc: PluginDependencyError
) -> JSONResponse:
    return _error(409, "PLUGIN_DEPENDENCY_UNSATISFIED", str(exc))


# Registered last on purpose: the specific plugin failures above keep their own
# status codes, and only what none of them matched falls through to this one.
@app.exception_handler(PluginManagerError)
async def plugin_manager_error_handler(
    request: Request, exc: PluginManagerError
) -> JSONResponse:
    return _error(409, "PLUGIN_REJECTED", str(exc))


@app.exception_handler(WorkflowTemplateError)
async def workflow_template_handler(
    request: Request, exc: WorkflowTemplateError
) -> JSONResponse:
    return _error(409, exc.code, str(exc))


@app.exception_handler(ProjectionUnavailableError)
async def projection_unavailable_handler(
    request: Request, exc: ProjectionUnavailableError
) -> JSONResponse:
    return _error(409, "PROJECTION_UNAVAILABLE", str(exc))


@app.exception_handler(VerifierUnavailableError)
async def verifier_unavailable_handler(
    request: Request, exc: VerifierUnavailableError
) -> JSONResponse:
    return _error(409, "VERIFIER_UNAVAILABLE", str(exc))


@app.exception_handler(ExecutionModeMismatchError)
async def execution_mode_mismatch_handler(
    request: Request, exc: ExecutionModeMismatchError
) -> JSONResponse:
    return _error(409, "EXECUTION_MODE_MISMATCH", str(exc))


@app.exception_handler(DynamicWorkflowDisabledError)
async def dynamic_workflow_disabled_handler(
    request: Request, exc: DynamicWorkflowDisabledError
) -> JSONResponse:
    return _error(403, "DYNAMIC_WORKFLOWS_DISABLED", str(exc))


@app.exception_handler(DynamicWorkflowConflictError)
async def dynamic_workflow_conflict_handler(
    request: Request, exc: DynamicWorkflowConflictError
) -> JSONResponse:
    return _error(409, "DYNAMIC_WORKFLOW_CONFLICT", str(exc))


@app.exception_handler(CandidateSearchDisabledError)
async def candidate_search_disabled_handler(
    request: Request, exc: CandidateSearchDisabledError
) -> JSONResponse:
    return _error(403, "CANDIDATE_SEARCH_DISABLED", str(exc))


@app.exception_handler(CandidateSearchConflictError)
async def candidate_search_conflict_handler(
    request: Request, exc: CandidateSearchConflictError
) -> JSONResponse:
    code = (
        "REPLAY_BRANCH_REQUIRES_P7"
        if str(exc) == "REPLAY_BRANCH_REQUIRES_P7"
        else "CANDIDATE_SEARCH_CONFLICT"
    )
    return _error(409, code, str(exc))


@app.exception_handler(ExperienceDisabledError)
async def experience_disabled_handler(
    request: Request, exc: ExperienceDisabledError
) -> JSONResponse:
    return _error(403, "EXPERIENCE_RETRIEVAL_DISABLED", str(exc))


@app.exception_handler(ExperienceConflictError)
async def experience_conflict_handler(
    request: Request, exc: ExperienceConflictError
) -> JSONResponse:
    return _error(409, "EXPERIENCE_CONFLICT", str(exc))


def _error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    body = ErrorEnvelope(
        code=code,
        message=message,
        correlation_id=str(uuid4()),
        retryable=retryable,
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/api/v1/projects", response_model=Project, status_code=201)
async def create_project(payload: ProjectCreate, request: Request) -> Project:
    return await manager(request).create_project(payload.name, payload.repository_path)


@app.get("/api/v1/projects", response_model=list[Project])
async def list_projects(request: Request) -> list[Project]:
    return await manager(request).store.list_projects()


@app.get("/api/v1/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, request: Request) -> Project:
    project = await manager(request).store.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    return project


@app.get(
    "/api/v2/projects/{project_id}/features",
    response_model=ProjectFeatureSettings,
)
async def get_project_features(
    project_id: str, request: Request
) -> ProjectFeatureSettings:
    return await dynamic_workflows(request).get_project_features(project_id)


@app.patch(
    "/api/v2/projects/{project_id}/features",
    response_model=ProjectFeatureSettings,
)
async def update_project_features(
    project_id: str, payload: ProjectFeatureUpdate, request: Request
) -> ProjectFeatureSettings:
    return await dynamic_workflows(request).update_project_features(
        project_id,
        dynamic_workflows=payload.dynamic_workflows,
        candidate_search=payload.candidate_search,
        experience_retrieval=payload.experience_retrieval,
        expected_revision=payload.expected_revision,
    )


@app.post("/api/v1/tasks", response_model=Task, status_code=201)
async def create_task(payload: TaskCreate, request: Request) -> Task:
    return await manager(request).create_task(
        project_id=payload.project_id,
        objective=payload.objective,
        task_patch=payload.envelope_patch(),
    )


@app.get("/api/v1/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str, request: Request) -> Task:
    task = await manager(request).store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    return task


@app.get("/api/v1/tasks/{task_id}/planning", response_model=TaskPlanning)
async def get_task_planning(task_id: str, request: Request) -> TaskPlanning:
    return await manager(request).get_task_planning(task_id)


@app.get("/api/v1/tasks/{task_id}/profile", response_model=TaskProfile)
async def get_task_profile(task_id: str, request: Request) -> TaskProfile:
    return (await manager(request).get_task_planning(task_id)).current_profile


@app.post(
    "/api/v2/tasks/{task_id}/workflow/propose",
    response_model=WorkflowProposal,
    status_code=201,
)
async def propose_dynamic_workflow(
    task_id: str, payload: WorkflowProposeCreate, request: Request
) -> WorkflowProposal:
    return await dynamic_workflows(request).propose(
        task_id,
        execution_provider=payload.execution_provider,
        planner_runtime=payload.planner_runtime,
    )


@app.post(
    "/api/v1/tasks/{task_id}/strategy/override",
    response_model=StrategyOverrideResult,
    status_code=201,
)
@app.post(
    "/api/v1/tasks/{task_id}/strategy-overrides",
    response_model=StrategyOverrideResult,
    status_code=201,
)
async def create_strategy_override(
    task_id: str, payload: StrategyOverrideCreate, request: Request
) -> StrategyOverrideResult:
    return await manager(request).override_strategy(
        task_id=task_id,
        requested_mode=payload.requested_mode,
        requested_template_id=payload.requested_template_id,
        reason=payload.reason,
    )


@app.post("/api/v1/tasks/{task_id}/runs", response_model=Run, status_code=202)
async def start_run(task_id: str, payload: RunCreate, request: Request) -> Run:
    return await manager(request).start_run(
        task_id, payload.provider, current_principal(request).principal_id
    )


@app.get("/api/v1/runs", response_model=list[Run])
async def list_runs(request: Request, limit: int = 100) -> list[Run]:
    return await manager(request).store.list_runs(min(max(limit, 1), 500))


@app.get("/api/v1/runs/{run_id}", response_model=Run)
async def get_run(run_id: str, request: Request) -> Run:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    return run


@app.post(
    "/api/v2/runs/{run_id}/experiences",
    response_model=ExperienceDetail,
    status_code=201,
)
async def materialize_experience(
    run_id: str, payload: ExperienceMaterializeCreate, request: Request
) -> ExperienceDetail:
    return await experience_service(request).materialize(
        run_id, candidate_id=payload.candidate_id
    )


@app.get("/api/v2/experiences", response_model=list[Experience])
async def list_experiences(
    request: Request,
    project_id: str | None = None,
    repository_identity: str | None = None,
    include_retracted: bool = False,
) -> list[Experience]:
    return await experience_service(request).list_experiences(
        project_id=project_id,
        repository_identity=repository_identity,
        include_retracted=include_retracted,
    )


@app.get("/api/v2/experiences/{experience_id}", response_model=ExperienceDetail)
async def get_experience(experience_id: str, request: Request) -> ExperienceDetail:
    return await experience_service(request).detail(experience_id)


@app.post("/api/v2/experiences/{experience_id}/retract", response_model=Experience)
async def retract_experience(
    experience_id: str, payload: ExperienceRetractCreate, request: Request
) -> Experience:
    return await experience_service(request).retract(
        experience_id,
        reason=payload.reason,
        expected_revision=payload.expected_revision,
    )


@app.post("/api/v2/experiences/query", response_model=list[ExperienceMatch])
async def query_experience(
    payload: ExperienceQueryCreate, request: Request
) -> list[ExperienceMatch]:
    return await experience_service(request).query(
        payload.task_id,
        include_failures=payload.include_failures,
        top_k=payload.top_k,
        max_age_days=payload.max_age_days,
    )


@app.post(
    "/api/v2/tasks/{task_id}/experience-selections",
    response_model=ExperienceSelection,
    status_code=201,
)
async def create_experience_selection(
    task_id: str, payload: ExperienceSelectionCreate, request: Request
) -> ExperienceSelection:
    return await experience_service(request).select(
        task_id,
        query_id=payload.query_id,
        match_ids=payload.match_ids,
        expected_context_bundle_id=payload.expected_context_bundle_id,
    )


@app.get(
    "/api/v2/tasks/{task_id}/experience-selections",
    response_model=list[ExperienceSelection],
)
async def list_experience_selections(
    task_id: str, request: Request
) -> list[ExperienceSelection]:
    return await experience_service(request).selections(task_id)


@app.get(
    "/api/v2/tasks/{task_id}/experience-matches",
    response_model=list[ExperienceMatch],
)
async def list_selected_experience_matches(
    task_id: str, request: Request
) -> list[ExperienceMatch]:
    return await experience_service(request).selected_matches(task_id)


@app.get(
    "/api/v2/runs/{run_id}/workflow/proposals",
    response_model=list[WorkflowProposal],
)
async def list_dynamic_workflow_proposals(
    run_id: str, request: Request
) -> list[WorkflowProposal]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_workflow_proposals(run_id=run_id)


@app.get(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}",
    response_model=WorkflowProposal,
)
async def get_dynamic_workflow_proposal(
    run_id: str, proposal_id: str, request: Request
) -> WorkflowProposal:
    proposal = await manager(request).store.get_workflow_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal.run_id != run_id:
        raise DynamicWorkflowConflictError(
            f"proposal {proposal_id} does not belong to run {run_id}"
        )
    return proposal


@app.get(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}/validations",
    response_model=list[GraphValidationResult],
)
async def list_dynamic_workflow_validations(
    run_id: str, proposal_id: str, request: Request
) -> list[GraphValidationResult]:
    await get_dynamic_workflow_proposal(run_id, proposal_id, request)
    return await manager(request).store.list_graph_validations(proposal_id)


@app.post(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}/validate",
    response_model=WorkflowValidationOutcome,
)
async def validate_dynamic_workflow(
    run_id: str, proposal_id: str, request: Request
) -> WorkflowValidationOutcome:
    return await dynamic_workflows(request).validate(run_id, proposal_id)


@app.post(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}/activate",
    response_model=WorkflowActivationOutcome,
    status_code=202,
)
async def activate_dynamic_workflow(
    run_id: str, proposal_id: str, request: Request
) -> WorkflowActivationOutcome:
    return await dynamic_workflows(request).activate(run_id, proposal_id)


@app.post(
    "/api/v2/runs/{run_id}/replan",
    response_model=ReplanOutcome,
    status_code=202,
)
async def replan_dynamic_workflow(
    run_id: str, payload: ReplanCreate, request: Request
) -> ReplanOutcome:
    return await dynamic_workflows(request).replan(
        run_id,
        reason=payload.reason,
        evidence_refs=payload.evidence_refs,
    )


@app.get(
    "/api/v2/runs/{run_id}/replans",
    response_model=list[ReplanRequest],
)
async def list_replan_requests(run_id: str, request: Request) -> list[ReplanRequest]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_replan_requests(run_id)


@app.get(
    "/api/v2/runs/{run_id}/graph/revisions",
    response_model=list[RunGraphRevision],
)
async def list_graph_revisions(run_id: str, request: Request) -> list[RunGraphRevision]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_graph_revisions(run_id)


@app.get(
    "/api/v2/runs/{run_id}/graph/revisions/{revision}",
    response_model=RunGraphRevision,
)
async def get_graph_revision(
    run_id: str, revision: int, request: Request
) -> RunGraphRevision:
    result = await manager(request).store.get_graph_revision(run_id, revision)
    if result is None:
        raise KeyError(f"{run_id}/revision/{revision}")
    return result


@app.get(
    "/api/v2/runs/{run_id}/graph/diff",
    response_model=GraphRevisionDiff,
)
async def get_graph_revision_diff(
    run_id: str,
    request: Request,
    from_revision: int = Query(alias="from", ge=1),
    to_revision: int = Query(alias="to", ge=1),
) -> GraphRevisionDiff:
    return await dynamic_workflows(request).graph_diff(
        run_id, from_revision, to_revision
    )


@app.get(
    "/api/v2/runs/{run_id}/runtime-decisions",
    response_model=list[RuntimeDecision],
)
async def list_runtime_decisions(run_id: str, request: Request) -> list[RuntimeDecision]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_runtime_decisions(run_id)


@app.post(
    "/api/v2/runs/{run_id}/search",
    response_model=SearchRecord,
    status_code=201,
)
async def create_candidate_search(
    run_id: str, payload: SearchCreate, request: Request
) -> SearchRecord:
    return await candidate_search(request).create_plan(
        run_id,
        parent_node_id=payload.parent_node_id,
        mode=payload.mode,
        branch_count=payload.branch_count,
        max_parallel=payload.max_parallel,
        per_branch_budget=payload.per_branch_budget,
        total_budget=payload.total_budget,
        candidate_directives=payload.candidate_directives,
        replay_seed_match_ids=payload.replay_seed_match_ids,
        negative_guidance_match_ids=payload.negative_guidance_match_ids,
    )


@app.get(
    "/api/v2/runs/{run_id}/searches",
    response_model=list[SearchRecord],
)
async def list_candidate_searches(run_id: str, request: Request) -> list[SearchRecord]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_searches(run_id)


@app.get("/api/v2/search/{search_id}", response_model=SearchRecord)
async def get_candidate_search(search_id: str, request: Request) -> SearchRecord:
    return await candidate_search(request).get(search_id)


@app.get(
    "/api/v2/search/{search_id}/candidates",
    response_model=list[CandidateTrajectory],
)
async def list_search_candidates(
    search_id: str, request: Request
) -> list[CandidateTrajectory]:
    await candidate_search(request).get(search_id)
    return await manager(request).store.list_search_candidates(search_id)


@app.get(
    "/api/v2/search/{search_id}/scores",
    response_model=list[CandidateScore],
)
async def list_search_scores(search_id: str, request: Request) -> list[CandidateScore]:
    await candidate_search(request).get(search_id)
    return await manager(request).store.list_candidate_scores(search_id)


@app.get(
    "/api/v2/search/{search_id}/replay-seeds",
    response_model=list[TrajectorySeed],
)
async def list_replay_seeds(search_id: str, request: Request) -> list[TrajectorySeed]:
    await candidate_search(request).get(search_id)
    return await manager(request).store.list_trajectory_seeds(search_id)


@app.post("/api/v2/search/{search_id}/cancel", response_model=SearchRecord)
async def cancel_candidate_search(search_id: str, request: Request) -> SearchRecord:
    return await candidate_search(request).cancel(search_id)


@app.get("/api/v1/runs/{run_id}/artifacts", response_model=list[ArtifactRef])
async def list_artifacts(run_id: str, request: Request) -> list[ArtifactRef]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_artifacts(run_id)


@app.get("/api/v1/runs/{run_id}/loop", response_model=LoopExecution)
async def get_loop_execution(run_id: str, request: Request) -> LoopExecution:
    await _require_loop_run(run_id, request)
    return await manager(request).get_loop(run_id)


@app.get("/api/v1/runs/{run_id}/graph", response_model=GraphProjection)
async def get_run_graph(run_id: str, request: Request) -> GraphProjection:
    return await manager(request).get_graph(run_id)


@app.get("/api/v1/runs/{run_id}/trace", response_model=ExecutionTrace)
async def get_run_trace(run_id: str, request: Request) -> ExecutionTrace:
    return await manager(request).get_trace(run_id)


@app.get("/api/v1/runs/{run_id}/audit", response_model=RunAudit)
async def get_run_audit(run_id: str, request: Request) -> RunAudit:
    service = manager(request)
    run = await service.store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    task = await service.store.get_task(run.task_id)
    planning = await service.store.get_task_planning(run.task_id)
    template = (
        await service.store.get_workflow_template(run.workflow_template_id)
        if run.workflow_template_id
        else None
    )
    if task is None or planning is None or template is None:
        raise RuntimeError(f"run {run_id} has incomplete planning provenance")
    runtime = await service.runtimes[run.provider].health()
    return RunAudit(
        run=run,
        task=task,
        profile=planning.current_profile,
        strategy=planning.current_decision,
        template=template,
        runtime=runtime,
        session=await service.store.get_session_for_run(run_id),
        events=await service.store.list_events(run_id),
        artifacts=await service.store.list_artifacts(run_id),
        verifications=await service.store.list_verifications(run_id),
        capability_results=await service.store.list_capability_results(run_id),
        trace=await service.get_trace(run_id),
    )


@app.get("/api/v1/templates", response_model=list[WorkflowTemplateSummary])
async def list_templates(
    request: Request, status: str | None = None
) -> list[WorkflowTemplateSummary]:
    parsed: TemplateStatus | None
    if status is None:
        parsed = TemplateStatus.VALIDATED
    else:
        try:
            parsed = TemplateStatus(status)
        except ValueError as exc:
            raise ValueError(f"unknown template status {status!r}") from exc
    templates = await manager(request).store.list_workflow_templates(parsed)
    return [
        WorkflowTemplateSummary(
            template_id=template.template_id,
            version=template.version,
            mode=template.mode,
            status=template.status,
            checksum=template.checksum,
        )
        for template in templates
    ]


@app.get("/api/v1/approvals", response_model=list[ApprovalRecord])
async def list_approvals(
    request: Request, run_id: str | None = None, status: str | None = None
) -> list[ApprovalRecord]:
    parsed: ApprovalStatus | None = None
    if status is not None:
        try:
            parsed = ApprovalStatus(status)
        except ValueError as exc:
            raise ValueError(f"unknown approval status {status!r}") from exc
    return await manager(request).store.list_approvals(run_id, parsed)


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=ApprovalRecord)
async def decide_approval(
    approval_id: str, payload: ApprovalDecisionCreate, request: Request
) -> ApprovalRecord:
    return await manager(request).resolve_approval(approval_id, payload.decision)


@app.get(
    "/api/v1/runs/{run_id}/verifications",
    response_model=list[VerificationResult],
)
async def list_verifications(run_id: str, request: Request) -> list[VerificationResult]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_verifications(run_id)


@app.get(
    "/api/v1/verifications/{verification_id}",
    response_model=VerificationResult,
)
async def get_verification(verification_id: str, request: Request) -> VerificationResult:
    result = await manager(request).store.get_verification(verification_id)
    if result is None:
        raise KeyError(verification_id)
    return result


@app.post("/api/v1/runs/{run_id}/pause", response_model=Run)
async def pause_run(run_id: str, request: Request) -> Run:
    return await manager(request).pause(run_id)


@app.post("/api/v1/runs/{run_id}/resume", response_model=Run)
async def resume_run(run_id: str, request: Request) -> Run:
    return await manager(request).resume(run_id)


@app.post("/api/v1/runs/{run_id}/cancel", response_model=Run)
async def cancel_run(run_id: str, request: Request) -> Run:
    return await manager(request).cancel(run_id)


@app.get("/api/v1/runtimes", response_model=list[RuntimeHealth])
async def list_runtimes(request: Request) -> list[RuntimeHealth]:
    runtimes = manager(request).runtimes.values()
    return await asyncio.gather(*(runtime.health() for runtime in runtimes))


@app.get("/api/v1/runtimes/{runtime_id}/health", response_model=RuntimeHealth)
async def runtime_health(runtime_id: str, request: Request) -> RuntimeHealth:
    for runtime in manager(request).runtimes.values():
        health = await runtime.health()
        if health.runtime_id == runtime_id:
            return health
    raise KeyError(runtime_id)


@app.get("/api/v1/runtimes/{runtime_id}/sessions", response_model=list[SessionRef])
async def runtime_sessions(runtime_id: str, request: Request) -> list[SessionRef]:
    for runtime in manager(request).runtimes.values():
        health = await runtime.health()
        if health.runtime_id == runtime_id:
            return await manager(request).store.list_sessions(health.provider)
    raise KeyError(runtime_id)


@app.get("/api/v1/me", response_model=MeResponse)
async def get_me(request: Request) -> MeResponse:
    who = current_principal(request)
    memberships = await manager(request).store.list_workspace_memberships(
        principal_id=who.principal_id
    )
    return MeResponse(
        principal=who,
        memberships=memberships,
        auth_mode=auth_runtime(request.app).mode,
    )


@app.get("/api/v1/workspaces", response_model=list[WorkspaceEntity])
async def list_workspaces(request: Request) -> list[WorkspaceEntity]:
    who = current_principal(request)
    return await manager(request).store.list_workspaces_for_principal(who.principal_id)


@app.get("/api/v1/auth/providers", response_model=list[AuthProviderInfo])
async def auth_providers(request: Request) -> list[AuthProviderInfo]:
    runtime = auth_runtime(request.app)
    if runtime.mode == "OIDC" and runtime.identity.oidc is not None:
        return [AuthProviderInfo(mode="OIDC", issuer=runtime.identity.oidc.config.issuer)]
    return [AuthProviderInfo(mode="LOCAL_PRINCIPAL")]


@app.get("/api/v1/auth/login")
async def auth_login(request: Request) -> RedirectResponse:
    runtime = auth_runtime(request.app)
    if runtime.mode != "OIDC":
        raise ValueError("login is not required in LOCAL_PRINCIPAL mode")
    url = await runtime.identity.begin_login()
    return RedirectResponse(url, status_code=302)


@app.get("/api/v1/auth/callback")
async def auth_callback(request: Request, state: str, code: str) -> RedirectResponse:
    runtime = auth_runtime(request.app)
    _, session = await runtime.identity.complete_login(state=state, code=code)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        runtime.cookie_name,
        session.auth_session_id,
        max_age=runtime.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=runtime.cookie_secure,
    )
    return response


@app.post("/api/v1/auth/logout", status_code=204)
async def auth_logout(request: Request) -> Response:
    runtime = auth_runtime(request.app)
    auth_session_id = request.cookies.get(runtime.cookie_name)
    if auth_session_id:
        await runtime.identity.logout(auth_session_id)
    response = Response(status_code=204)
    response.delete_cookie(runtime.cookie_name)
    return response


@app.get("/api/v1/capabilities", response_model=list[Capability])
async def list_capabilities(request: Request) -> list[Capability]:
    return await manager(request).store.list_capabilities()


async def _require_workspace_access(
    request: Request,
    workspace_id: str,
    *,
    administer: bool = False,
) -> None:
    who = current_principal(request)
    memberships = await manager(request).store.list_workspace_memberships(
        workspace_id=workspace_id,
        principal_id=who.principal_id,
    )
    if not memberships:
        raise AuthorizationError("principal is not a member of the requested workspace")
    if administer and memberships[0].role not in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
        raise AuthorizationError("workspace owner or admin role is required")


@app.get(
    "/api/v1/runs/{run_id}/research-evidence",
    response_model=list[EvidenceRecord],
)
async def list_run_research_evidence(
    run_id: str,
    request: Request,
    workspace_id: str,
    capability_id: str | None = None,
) -> list[EvidenceRecord]:
    """Read a run's Evidence Store, in the store's own deterministic order.

    Read-only by construction: evidence is written on the gateway's execution path,
    where the trust label is assigned, and there is deliberately no HTTP way to put a
    record here or to relabel one.

    ``workspace_id`` is a required parameter rather than something derived from the
    run because ``Run`` carries no workspace: it links to a task, a project and a
    principal, and none of the three reaches a workspace today. Requiring the caller
    to name a workspace they are a member of is therefore the strongest gate available
    at this boundary, and narrowing it to the run's own workspace is M6 work that
    needs the missing link first.

    Ordering is ``(created_at, evidence_id)`` in all three store implementations, so a
    caller may page or diff two responses without re-sorting.
    """

    await _require_workspace_access(request, workspace_id)
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_research_evidence(
        run_id, capability_id=capability_id
    )


@app.get("/api/v1/mcp/servers", response_model=list[McpServerDefinition])
async def list_mcp_servers(
    request: Request, workspace_id: str | None = None
) -> list[McpServerDefinition]:
    who = current_principal(request)
    memberships = await manager(request).store.list_workspace_memberships(
        principal_id=who.principal_id
    )
    visible = {item.workspace_id for item in memberships}
    if workspace_id is not None:
        if workspace_id not in visible:
            raise AuthorizationError("principal is not a member of the requested workspace")
        visible = {workspace_id}
    return [
        server
        for server in await remote_mcp(request).store.list_mcp_servers(workspace_id)
        if server.workspace_id in visible
    ]


@app.post("/api/v1/mcp/servers", response_model=McpServerDefinition, status_code=201)
async def register_mcp_server(
    payload: McpServerCreate, request: Request
) -> McpServerDefinition:
    await _require_workspace_access(request, payload.workspace_id, administer=True)
    who = current_principal(request)
    return await remote_mcp(request).register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=payload.workspace_id,
            connector_id=payload.connector_id,
            name=payload.name,
            endpoint=payload.endpoint,
            protocol_versions=payload.protocol_versions,
            auth_profile_ref=payload.auth_profile_ref,
            trust_level=payload.trust_level,
            owner_principal_id=who.principal_id,
            health_policy=payload.health_policy,
            discovery_policy=payload.discovery_policy,
            allowed_tool_patterns=payload.allowed_tool_patterns,
            denied_tool_patterns=payload.denied_tool_patterns,
            tool_mappings=payload.tool_mappings,
        )
    )


async def _mcp_server_for_request(
    mcp_server_id: str, request: Request, *, administer: bool = False
) -> McpServerDefinition:
    server = await remote_mcp(request).store.get_mcp_server(mcp_server_id)
    if server is None:
        raise KeyError(mcp_server_id)
    await _require_workspace_access(request, server.workspace_id, administer=administer)
    return server


@app.get("/api/v1/mcp/servers/{mcp_server_id}", response_model=McpServerDefinition)
async def get_mcp_server(mcp_server_id: str, request: Request) -> McpServerDefinition:
    return await _mcp_server_for_request(mcp_server_id, request)


@app.post(
    "/api/v1/mcp/servers/{mcp_server_id}/refresh-discovery",
    response_model=McpDiscoverySnapshot,
)
async def refresh_mcp_discovery(
    mcp_server_id: str,
    request: Request,
    force: bool = Query(default=False),
) -> McpDiscoverySnapshot:
    server = await _mcp_server_for_request(mcp_server_id, request, administer=True)
    who = current_principal(request)
    return await remote_mcp(request).refresh_discovery(
        server.mcp_server_id,
        principal_id=who.principal_id,
        workspace_id=server.workspace_id,
        force=force,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.get(
    "/api/v1/mcp/servers/{mcp_server_id}/discovery",
    response_model=McpDiscoverySnapshot,
)
async def get_mcp_server_discovery(
    mcp_server_id: str, request: Request
) -> McpDiscoverySnapshot:
    """The most recent discovery snapshot for one server.

    Read-only: unlike ``refresh-discovery`` this never contacts the server, so any
    workspace member may call it. A server with no snapshot yet is a 404, which is
    also what a non-existent server returns.
    """

    server = await _mcp_server_for_request(mcp_server_id, request)
    snapshots = await remote_mcp(request).store.list_mcp_discovery_snapshots(
        server.mcp_server_id
    )
    if not snapshots:
        raise KeyError(mcp_server_id)
    return snapshots[0]


@app.post("/api/v1/mcp/servers/{mcp_server_id}/enable", response_model=McpServerDefinition)
async def enable_mcp_server(mcp_server_id: str, request: Request) -> McpServerDefinition:
    server = await _mcp_server_for_request(mcp_server_id, request, administer=True)
    return await remote_mcp(request).enable(
        server.mcp_server_id,
        principal_id=current_principal(request).principal_id,
        workspace_id=server.workspace_id,
    )


@app.post("/api/v1/mcp/servers/{mcp_server_id}/disable", response_model=McpServerDefinition)
async def disable_mcp_server(mcp_server_id: str, request: Request) -> McpServerDefinition:
    server = await _mcp_server_for_request(mcp_server_id, request, administer=True)
    return await remote_mcp(request).disable(
        server.mcp_server_id,
        principal_id=current_principal(request).principal_id,
        workspace_id=server.workspace_id,
    )


@app.get(
    "/api/v1/mcp/servers/{mcp_server_id}/capabilities",
    response_model=list[Capability],
)
async def list_mcp_server_capabilities(
    mcp_server_id: str, request: Request
) -> list[Capability]:
    server = await _mcp_server_for_request(mcp_server_id, request)
    return await remote_mcp(request).capabilities(
        server.mcp_server_id, workspace_id=server.workspace_id
    )


def _connection_summary(connection: Connection) -> ConnectionSummary:
    """Project a stored connection for the API. Never carries the token handle."""

    return ConnectionSummary(
        connection_id=connection.connection_id,
        connector_id=connection.connector_id,
        workspace_id=connection.workspace_id,
        principal_id=connection.principal_id,
        scope=connection.scope,
        status=connection.status,
        granted_scopes=connection.granted_scopes,
        workspace_shareable=connection.workspace_shareable,
        created_at=connection.created_at,
        last_health_check=connection.last_health_check,
    )


@app.get("/api/v1/connectors", response_model=list[ConnectorDefinition])
async def list_connectors(request: Request) -> list[ConnectorDefinition]:
    return await manager(request).store.list_connector_definitions()


@app.get("/api/v1/connections", response_model=list[ConnectionSummary])
async def list_connections(request: Request) -> list[ConnectionSummary]:
    """List only the connections this principal may see.

    Returns a summary rather than the stored model: a Connection carries
    ``token_handle_ref``, which is broker-internal and never leaves the API (INV3-002).
    """

    store = manager(request).store
    who = current_principal(request)
    memberships = await store.list_workspace_memberships(principal_id=who.principal_id)
    workspaces = {item.workspace_id for item in memberships}
    return [
        _connection_summary(item)
        for item in await store.list_connections()
        # A user connection is private to its owner (INV3-008); a workspace
        # connection is visible only to members of that workspace.
        if item.principal_id == who.principal_id
        or (item.scope is ConnectionScope.WORKSPACE and item.workspace_id in workspaces)
    ]


def connections(request: Request) -> ConnectionService:
    service = getattr(request.app.state, "connections", None)
    if service is None:
        raise ValueError("no OAuth connector is configured")
    return cast(ConnectionService, service)


@app.post("/api/v1/connectors/{connector_id}/connect", response_model=AuthorizationStart)
async def connect_connector(
    connector_id: str, payload: ConnectCreate, request: Request
) -> AuthorizationStart:
    """Begin an OAuth authorization for the calling principal."""

    url = await connections(request).begin(
        connector_id=connector_id,
        principal=current_principal(request),
        workspace_id=payload.workspace_id,
        scopes=payload.scopes,
        redirect_target=payload.redirect_target,
    )
    return AuthorizationStart(authorization_url=url)


@app.get("/api/v1/oauth/callback/{connector_id}", response_model=ConnectionSummary)
async def oauth_callback(
    connector_id: str, state: str, code: str, request: Request
) -> ConnectionSummary:
    """Redeem an authorization code.

    Deliberately *not* exempt from the session middleware. The cookie is SameSite=Lax,
    so it accompanies this top-level redirect, and requiring it gives a second binding
    beyond ``state``: the returning browser must be the session that began the flow.
    """

    connection = await connections(request).complete(
        connector_id=connector_id,
        state=state,
        code=code,
        principal=current_principal(request),
    )
    return _connection_summary(connection)


@app.post(
    "/api/v1/connections/{connection_id}/reauthorize", response_model=AuthorizationStart
)
async def reauthorize_connection(
    connection_id: str, payload: ConnectCreate, request: Request
) -> AuthorizationStart:
    """Re-consent, the only way scopes ever widen (SDD 6.3)."""

    service = connections(request)
    who = current_principal(request)
    existing = await service.store.get_connection(connection_id)
    if existing is None or existing.principal_id != who.principal_id:
        raise KeyError(connection_id)
    url = await service.begin(
        connector_id=existing.connector_id,
        principal=who,
        workspace_id=existing.workspace_id,
        scopes=payload.scopes,
        connection_id=connection_id,
        redirect_target=payload.redirect_target,
    )
    return AuthorizationStart(authorization_url=url)


@app.post("/api/v1/connections/{connection_id}/revoke", response_model=ConnectionSummary)
async def revoke_connection(connection_id: str, request: Request) -> ConnectionSummary:
    connection = await connections(request).revoke(
        connection_id=connection_id, principal=current_principal(request)
    )
    return _connection_summary(connection)


@app.get("/api/v1/connections/{connection_id}/health")
async def connection_health(connection_id: str, request: Request) -> dict[str, Any]:
    return await connections(request).health(
        connection_id=connection_id, principal=current_principal(request)
    )


@app.post("/api/v1/capabilities/resolve", response_model=ResolvedCapability)
async def resolve_capability(
    payload: CapabilityResolveRequest, request: Request
) -> ResolvedCapability:
    store = manager(request).store
    who = current_principal(request)
    if payload.principal_id is not None and payload.principal_id != who.principal_id:
        # Resolving as another principal would disclose whether they hold a
        # connection, and which one (INV3-008).
        raise AuthorizationError("cannot resolve capabilities for another principal")
    if payload.workspace_id is not None:
        memberships = await store.list_workspace_memberships(principal_id=who.principal_id)
        if payload.workspace_id not in {item.workspace_id for item in memberships}:
            raise AuthorizationError("principal is not a member of the requested workspace")
    resolved = await CapabilityResolver(store).resolve(
        payload.capability_id,
        version=payload.version,
        principal_id=who.principal_id,
        workspace_id=payload.workspace_id,
    )
    if resolved is None:
        raise KeyError(payload.capability_id)
    return resolved


@app.get("/api/v1/skills", response_model=list[MetaSkill])
async def list_skills(request: Request) -> list[MetaSkill]:
    return await manager(request).store.list_skills()


async def _visible_workspaces(request: Request) -> set[str]:
    who = current_principal(request)
    memberships = await manager(request).store.list_workspace_memberships(
        principal_id=who.principal_id
    )
    return {item.workspace_id for item in memberships}


@app.get("/api/v1/plugins", response_model=list[MetaPlugin])
async def list_plugins(request: Request) -> list[MetaPlugin]:
    """Built-in plugins, plus whatever is installed in the caller's workspaces.

    The v0.1 shape is unchanged so the console keeps rendering, but the listing is no
    longer global: before M4 every authenticated principal saw every registry row,
    including rows contributed by another tenant's installation.
    """

    visible = await _visible_workspaces(request)
    installations = await plugins(request).list_installations()
    installed_anywhere = {item.plugin_id for item in installations}
    installed_here = {
        item.plugin_id for item in installations if item.workspace_id in visible
    }
    return [
        entry
        for entry in await manager(request).store.list_plugins()
        # A registry row nobody installed is a seeded built-in and belongs to everyone;
        # anything a workspace installed belongs only to that workspace's members.
        if entry.plugin_id not in installed_anywhere or entry.plugin_id in installed_here
    ]


@app.get("/api/v1/plugins/installations", response_model=list[PluginInstallation])
async def list_plugin_installations(
    request: Request, workspace_id: str | None = None
) -> list[PluginInstallation]:
    visible = await _visible_workspaces(request)
    if workspace_id is not None:
        if workspace_id not in visible:
            raise AuthorizationError("principal is not a member of the requested workspace")
        visible = {workspace_id}
    return [
        installation
        for installation in await plugins(request).list_installations(workspace_id)
        if installation.workspace_id in visible
    ]


@app.get("/api/v1/audit/plugins", response_model=list[PluginAuditEvent])
async def list_plugin_audit_events(
    request: Request,
    plugin_id: str | None = None,
    workspace_id: str | None = None,
) -> list[PluginAuditEvent]:
    visible = await _visible_workspaces(request)
    if workspace_id is not None:
        if workspace_id not in visible:
            raise AuthorizationError("principal is not a member of the requested workspace")
        visible = {workspace_id}
    return [
        event
        for event in await plugins(request).store.list_plugin_audit_events(plugin_id=plugin_id)
        if event.workspace_id is not None and event.workspace_id in visible
    ]


@app.post("/api/v1/plugins/install", response_model=PluginInstallation, status_code=201)
async def install_plugin(
    payload: PluginInstallRequest, request: Request
) -> PluginInstallation:
    await _require_workspace_access(request, payload.workspace_id, administer=True)
    return await plugins(request).install(
        payload.reference,
        workspace_id=payload.workspace_id,
        principal_id=current_principal(request).principal_id,
        consent_digest=payload.consent_digest,
        consent_capability_ids=payload.consent_capability_ids,
        expected_digest=payload.expected_digest,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.get("/api/v1/plugins/{plugin_id}", response_model=PluginDetail)
async def get_plugin_detail(
    plugin_id: str, request: Request, workspace_id: str | None = None
) -> PluginDetail:
    if workspace_id is not None:
        await _require_workspace_access(request, workspace_id)
    return await plugins(request).detail(plugin_id, workspace_id=workspace_id)


@app.post("/api/v1/plugins/{plugin_id}/enable", response_model=PluginInstallation)
async def enable_plugin(
    plugin_id: str, payload: PluginWorkspaceRequest, request: Request
) -> PluginInstallation:
    await _require_workspace_access(request, payload.workspace_id, administer=True)
    return await plugins(request).enable(
        plugin_id,
        workspace_id=payload.workspace_id,
        principal_id=current_principal(request).principal_id,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.post("/api/v1/plugins/{plugin_id}/disable", response_model=PluginInstallation)
async def disable_plugin(
    plugin_id: str, payload: PluginWorkspaceRequest, request: Request
) -> PluginInstallation:
    await _require_workspace_access(request, payload.workspace_id, administer=True)
    return await plugins(request).disable(
        plugin_id,
        workspace_id=payload.workspace_id,
        principal_id=current_principal(request).principal_id,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.post("/api/v1/plugins/{plugin_id}/upgrade", response_model=PluginInstallation)
async def upgrade_plugin(
    plugin_id: str, payload: PluginInstallRequest, request: Request
) -> PluginInstallation:
    await _require_workspace_access(request, payload.workspace_id, administer=True)
    return await plugins(request).upgrade(
        plugin_id,
        payload.reference,
        workspace_id=payload.workspace_id,
        principal_id=current_principal(request).principal_id,
        consent_digest=payload.consent_digest,
        consent_capability_ids=payload.consent_capability_ids,
        expected_digest=payload.expected_digest,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.post("/api/v1/plugins/{plugin_id}/rollback", response_model=PluginInstallation)
async def rollback_plugin(
    plugin_id: str, payload: PluginWorkspaceRequest, request: Request
) -> PluginInstallation:
    await _require_workspace_access(request, payload.workspace_id, administer=True)
    return await plugins(request).rollback(
        plugin_id,
        workspace_id=payload.workspace_id,
        principal_id=current_principal(request).principal_id,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.delete("/api/v1/plugins/{plugin_id}", response_model=PluginInstallation)
async def remove_plugin(
    plugin_id: str,
    request: Request,
    workspace_id: str,
    force: bool = Query(default=False),
) -> PluginInstallation:
    """Retire an installation. Prior-run evidence is never touched (AC3-PLG-05)."""

    await _require_workspace_access(request, workspace_id, administer=True)
    return await plugins(request).remove(
        plugin_id,
        workspace_id=workspace_id,
        principal_id=current_principal(request).principal_id,
        force=force,
        correlation_id=request.headers.get("x-request-id"),
    )


@app.get("/api/v1/benchmarks/acr-arch", response_model=AcrArchSummary)
async def get_acr_arch(
    request: Request,
    mode: ExecutionMode | None = None,
    provider: Provider | None = None,
    task_type: TaskType | None = None,
    verifier: str | None = None,
    selector_version: str | None = None,
) -> AcrArchSummary:
    return await acr_arch_summary(
        manager(request).store,
        mode=mode,
        provider=provider,
        task_type=task_type,
        verifier=verifier,
        selector_version=selector_version,
    )


@app.post(
    "/api/v1/benchmarks/acr-arch/run",
    response_model=BenchmarkRun,
    status_code=201,
)
async def run_acr_arch(payload: BenchmarkRunCreate, request: Request) -> BenchmarkRun:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live benchmark runs require the explicit local CLI release gate")
    return await AcrArchRunner().persist(manager(request).store)


@app.get(
    "/api/v1/benchmarks/acr-arch/tasks/{task_id}",
    response_model=BenchmarkTaskDetail,
)
async def get_acr_arch_task(task_id: str, request: Request) -> BenchmarkTaskDetail:
    detail = await acr_arch_task_detail(manager(request).store, task_id)
    if detail is None:
        raise KeyError(task_id)
    return detail


@app.get("/api/v2/benchmarks/search", response_model=SearchBenchmarkSummary)
async def get_search_benchmark() -> SearchBenchmarkSummary:
    return SearchBenchmarkRunner().run()


@app.post(
    "/api/v2/benchmarks/search/run",
    response_model=SearchBenchmarkSummary,
)
async def run_search_benchmark(payload: BenchmarkRunCreate) -> SearchBenchmarkSummary:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live search calibration requires the explicit local release gate")
    return SearchBenchmarkRunner().run()


@app.get(
    "/api/v2/benchmarks/dynamic",
    response_model=DynamicWorkflowBenchmarkSummary,
)
async def get_dynamic_workflow_benchmark() -> DynamicWorkflowBenchmarkSummary:
    return DynamicWorkflowBenchmarkRunner().run()


@app.post(
    "/api/v2/benchmarks/dynamic/run",
    response_model=DynamicWorkflowBenchmarkSummary,
)
async def run_dynamic_workflow_benchmark(
    payload: BenchmarkRunCreate,
) -> DynamicWorkflowBenchmarkSummary:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live dynamic calibration requires the explicit local release gate")
    return DynamicWorkflowBenchmarkRunner().run()


@app.get(
    "/api/v2/benchmarks/experience",
    response_model=ExperienceBenchmarkSummary,
)
async def get_experience_benchmark() -> ExperienceBenchmarkSummary:
    return ExperienceBenchmarkRunner().run()


@app.post(
    "/api/v2/benchmarks/experience/run",
    response_model=ExperienceBenchmarkSummary,
)
async def run_experience_benchmark(
    payload: BenchmarkRunCreate,
) -> ExperienceBenchmarkSummary:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live experience calibration requires the explicit local release gate")
    return ExperienceBenchmarkRunner().run()


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    try:
        cursor_start = int(last_event_id) if last_event_id is not None else after
    except ValueError as exc:
        raise ValueError("Last-Event-ID must be an integer") from exc

    async def stream() -> AsyncIterator[str]:
        cursor = cursor_start
        terminal_event_seen = any(
            event.sequence <= cursor and event.normalized_type in SSE_TERMINAL_EVENTS
            for event in await manager(request).store.list_events(run_id)
        )
        while not await request.is_disconnected():
            events = await manager(request).store.list_events(run_id, cursor)
            for event in events:
                cursor = event.sequence
                terminal_event_seen = (
                    terminal_event_seen or event.normalized_type in SSE_TERMINAL_EVENTS
                )
                data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
                yield f"id: {cursor}\nevent: agent_event\ndata: {data}\n\n"
            current = await manager(request).store.get_run(run_id)
            if current is None or (
                current.state in TERMINAL_RUN_STATES
                and cursor >= current.last_sequence
                and terminal_event_seen
            ):
                break
            if current.state in TERMINAL_RUN_STATES:
                # Defensively tolerate a store or recovery path that exposes terminal state
                # just before its durable terminal event becomes visible.
                await asyncio.sleep(0.01)
                continue
            yield ": keepalive\n\n"
            await manager(request).wait_for_events(run_id, cursor)

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _require_loop_run(run_id: str, request: Request) -> Run:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run.execution_mode is not ExecutionMode.LOOP:
        raise ExecutionModeMismatchError(run)
    return run
