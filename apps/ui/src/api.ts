import type {
  AcrArchSummary,
  ApprovalDecisionValue,
  ApprovalRecord,
  BenchmarkRun,
  BenchmarkTaskDetail,
  Capability,
  CapabilityResolveRequest,
  ExecutionTrace,
  ExperienceBenchmarkSummary,
  ExperienceDetail,
  ExperienceMatch,
  ExperienceSelection,
  GraphProjection,
  GraphRevisionDiff,
  GraphValidationResult,
  LoopExecution,
  MetaPlugin,
  MetaSkill,
  AuthProviderInfo,
  AuthorizationStart,
  ConnectCreate,
  ConnectionSummary,
  ConnectorDefinition,
  EnterpriseAuthGrant,
  EnterpriseAuthProfileResponse,
  McpDiscoverySnapshot,
  McpServerDefinition,
  MeResponse,
  PluginAuditEvent,
  PluginDetail,
  PluginInstallation,
  ResolvedCapability,
  RollbackCreate,
  RouterActivation,
  RouterLineage,
  RouterModelVersion,
  RouterPromotionReport,
  WorkspaceEntity,
  Project,
  ProjectCreate,
  ProjectFeatureSettings,
  ReplanOutcome,
  ReplanRequest,
  ConfigurationCandidate,
  RoutingDecisionReceipt,
  RoutingOverrideCreate,
  ShadowReport,
  Run,
  RunAudit,
  RunCreate,
  RunGraphRevision,
  RuntimeDecision,
  RuntimeHealth,
  CandidateScore,
  CandidateTrajectory,
  DynamicWorkflowBenchmarkSummary,
  SearchBenchmarkSummary,
  SearchCreate,
  SearchRecord,
  SessionRef,
  StrategyOverrideCreate,
  StrategyOverrideResult,
  Task,
  TaskCreate,
  TaskPlanning,
  VerificationResult,
  WorkflowActivationOutcome,
  WorkflowProposal,
  WorkflowTemplateSummary,
  WorkflowValidationOutcome,
  TrajectorySeed,
} from "./types";

export interface AcrArchFilters {
  mode?: string;
  provider?: string;
  task_type?: string;
  verifier?: string;
  selector_version?: string;
}

export type { MeResponse };

export interface PluginAuditFilters {
  plugin_id?: string;
  workspace_id?: string;
}

export interface EnterpriseAuthAuditFilters {
  connector_id?: string;
  principal_id?: string;
  workspace_id?: string;
}

const API_ROOT = import.meta.env.VITE_API_URL ?? "";

function redirectToLogin(status: number): void {
  if (status === 401) {
    window.location.assign(`${API_ROOT}/api/v1/auth/login`);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { credentials: "include" });
  if (!response.ok) {
    redirectToLogin(response.status);
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

/**
 * `headers` exists for one requirement and is empty for every other call.
 *
 * SDD §11 refuses `POST /api/v1/router-promotions/{id}/promote` and
 * `POST /api/v1/router-models/{id}/rollback` without an `Idempotency-Key`, so those two
 * calls have to send a header that no other client function needs. The alternative — a
 * second `postJsonWithKey` helper beside this one — would have put a mutating request path
 * outside the function every other request goes through, and `tests/
 * test_v03_m6_api_client_contract.py` reads `postJson<T>(` followed by the path literal to
 * check that path against the live app, so a call through a differently named helper is a
 * call the contract test cannot see.
 */
async function postJson<T>(
  path: string,
  payload: unknown,
  headers: Readonly<Record<string, string>> = {},
): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    redirectToLogin(response.status);
    const body = await response.json().catch(() => null) as { message?: string } | null;
    throw new Error(body?.message ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

async function patchJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    redirectToLogin(response.status);
    const body = await response.json().catch(() => null) as { message?: string } | null;
    throw new Error(body?.message ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  me: () => getJson<MeResponse>("/api/v1/me"),
  runtimes: () => getJson<RuntimeHealth[]>("/api/v1/runtimes"),
  runtimeSessions: (runtimeId: string) =>
    getJson<SessionRef[]>(`/api/v1/runtimes/${runtimeId}/sessions`),
  runs: () => getJson<Run[]>("/api/v1/runs?limit=50"),
  run: (runId: string) => getJson<Run>(`/api/v1/runs/${runId}`),
  audit: (runId: string) => getJson<RunAudit>(`/api/v1/runs/${runId}/audit`),
  projects: () => getJson<Project[]>("/api/v1/projects"),
  createProject: (payload: ProjectCreate) => postJson<Project>("/api/v1/projects", payload),
  createTask: (payload: TaskCreate) => postJson<Task>("/api/v1/tasks", payload),
  task: (taskId: string) => getJson<Task>(`/api/v1/tasks/${taskId}`),
  planning: (taskId: string) => getJson<TaskPlanning>(`/api/v1/tasks/${taskId}/planning`),
  overrideStrategy: (taskId: string, payload: StrategyOverrideCreate) =>
    postJson<StrategyOverrideResult>(`/api/v1/tasks/${taskId}/strategy/override`, payload),
  startRun: (taskId: string, payload: RunCreate) =>
    postJson<Run>(`/api/v1/tasks/${taskId}/runs`, payload),
  projectFeatures: (projectId: string) =>
    getJson<ProjectFeatureSettings>(`/api/v2/projects/${projectId}/features`),
  updateProjectFeatures: (
    projectId: string,
    features: {
      dynamicWorkflows?: boolean;
      candidateSearch?: boolean;
      experienceRetrieval?: boolean;
    },
    revision: number,
  ) =>
    patchJson<ProjectFeatureSettings>(`/api/v2/projects/${projectId}/features`, {
      dynamic_workflows: features.dynamicWorkflows,
      candidate_search: features.candidateSearch,
      experience_retrieval: features.experienceRetrieval,
      expected_revision: revision,
    }),
  proposeWorkflow: (taskId: string, provider: string) =>
    postJson<WorkflowProposal>(`/api/v2/tasks/${taskId}/workflow/propose`, {
      execution_provider: provider,
      planner_runtime: "DETERMINISTIC",
    }),
  workflowProposals: (runId: string) =>
    getJson<WorkflowProposal[]>(`/api/v2/runs/${runId}/workflow/proposals`),
  workflowValidations: (runId: string, proposalId: string) =>
    getJson<GraphValidationResult[]>(
      `/api/v2/runs/${runId}/workflow/proposals/${proposalId}/validations`,
    ),
  validateWorkflow: (runId: string, proposalId: string) =>
    postJson<WorkflowValidationOutcome>(
      `/api/v2/runs/${runId}/workflow/proposals/${proposalId}/validate`,
      {},
    ),
  activateWorkflow: (runId: string, proposalId: string) =>
    postJson<WorkflowActivationOutcome>(
      `/api/v2/runs/${runId}/workflow/proposals/${proposalId}/activate`,
      {},
    ),
  graphRevisions: (runId: string) =>
    getJson<RunGraphRevision[]>(`/api/v2/runs/${runId}/graph/revisions`),
  graphDiff: (runId: string, from: number, to: number) =>
    getJson<GraphRevisionDiff>(
      `/api/v2/runs/${runId}/graph/diff?from=${from}&to=${to}`,
    ),
  runtimeDecisions: (runId: string) =>
    getJson<RuntimeDecision[]>(`/api/v2/runs/${runId}/runtime-decisions`),
  replans: (runId: string) =>
    getJson<ReplanRequest[]>(`/api/v2/runs/${runId}/replans`),
  replan: (runId: string, reason: string, evidenceRefs: string[]) =>
    postJson<ReplanOutcome>(`/api/v2/runs/${runId}/replan`, {
      reason,
      evidence_refs: evidenceRefs,
    }),
  // v0.4 M2 — one routing receipt, read and amended by its own id. There is deliberately no
  // "receipts of a run" call: the run page discovers receipt ids from the audit it already
  // fetches (`routingIndex.ts`), so the panel costs no request until a receipt is selected.
  routingDecision: (receiptId: string) =>
    getJson<RoutingDecisionReceipt>(`/api/v1/routing-decisions/${receiptId}`),
  routingCandidates: (receiptId: string) =>
    getJson<ConfigurationCandidate[]>(`/api/v1/routing-decisions/${receiptId}/candidates`),
  overrideRoutingDecision: (receiptId: string, payload: RoutingOverrideCreate) =>
    postJson<RoutingDecisionReceipt>(
      `/api/v1/routing-decisions/${receiptId}/override`,
      payload,
    ),
  cancelRoutingDecision: (receiptId: string) =>
    postJson<RoutingDecisionReceipt>(`/api/v1/routing-decisions/${receiptId}/cancel`, {}),
  // v0.4 M6 — the §17.2 shadow comparison. The version list carries every router version of
  // the workspace and no status filter, so SHADOW is selected client-side; the report is the
  // M6.2 read M8.2's promotion gate is defined over, asked for by the version's own id so
  // that a workspace with no shadow stage never requests one.
  routerModels: (workspaceId: string) =>
    getJson<RouterModelVersion[]>(`/api/v1/router-models?workspace_id=${workspaceId}`),
  shadowPolicyReport: (versionId: string) =>
    getJson<ShadowReport>(`/api/v1/shadow-policies/${versionId}/report`),
  // v0.4 M8 — the §17.3 router administration surface. Every call names the workspace
  // because the server does: the two reads require membership of it, the two writes require
  // administering it, and a resource belonging to another workspace is a 404 rather than a
  // 403 so that these paths cannot be used to enumerate ids across a tenancy boundary.
  //
  // `POST /api/v1/router-promotions` (evaluate) is deliberately absent. Producing a sealed
  // report is what a later promotion accepts as its authorisation, and in v0.4 that act
  // stays API-only; the page promotes reports that already exist and never mints one.
  routerLineage: (versionId: string, workspaceId: string) =>
    getJson<RouterLineage>(
      `/api/v1/router-models/${versionId}/lineage?workspace_id=${workspaceId}`,
    ),
  routerPromotion: (reportId: string, workspaceId: string) =>
    getJson<RouterPromotionReport>(
      `/api/v1/router-promotions/${reportId}?workspace_id=${workspaceId}`,
    ),
  // The key is derived from the natural key of the act rather than generated per attempt,
  // which is what the server's own reasoning asks for: a report authorises one release and
  // a version is withdrawn once, so a retry of either must replay rather than write again.
  promoteRouter: (reportId: string, workspaceId: string) =>
    postJson<RouterActivation>(
      `/api/v1/router-promotions/${reportId}/promote?workspace_id=${workspaceId}`,
      {},
      { "Idempotency-Key": `promote-${reportId}` },
    ),
  rollbackRouter: (versionId: string, workspaceId: string, payload: RollbackCreate) =>
    postJson<RouterActivation>(
      `/api/v1/router-models/${versionId}/rollback?workspace_id=${workspaceId}`,
      payload,
      { "Idempotency-Key": `rollback-${versionId}` },
    ),
  createSearch: (runId: string, payload: SearchCreate) =>
    postJson<SearchRecord>(`/api/v2/runs/${runId}/search`, payload),
  searches: (runId: string) =>
    getJson<SearchRecord[]>(`/api/v2/runs/${runId}/searches`),
  searchCandidates: (searchId: string) =>
    getJson<CandidateTrajectory[]>(`/api/v2/search/${searchId}/candidates`),
  searchScores: (searchId: string) =>
    getJson<CandidateScore[]>(`/api/v2/search/${searchId}/scores`),
  replaySeeds: (searchId: string) =>
    getJson<TrajectorySeed[]>(`/api/v2/search/${searchId}/replay-seeds`),
  cancelSearch: (searchId: string) =>
    postJson<SearchRecord>(`/api/v2/search/${searchId}/cancel`, {}),
  materializeExperience: (runId: string, candidateId?: string) =>
    postJson<ExperienceDetail>(`/api/v2/runs/${runId}/experiences`, {
      candidate_id: candidateId,
    }),
  queryExperiences: (taskId: string) =>
    postJson<ExperienceMatch[]>("/api/v2/experiences/query", {
      task_id: taskId,
      include_failures: true,
      top_k: 5,
    }),
  experience: (experienceId: string) =>
    getJson<ExperienceDetail>(`/api/v2/experiences/${experienceId}`),
  selectedExperienceMatches: (taskId: string) =>
    getJson<ExperienceMatch[]>(`/api/v2/tasks/${taskId}/experience-matches`),
  selectExperiences: (
    taskId: string,
    payload: {
      query_id: string;
      match_ids: string[];
      expected_context_bundle_id: string;
    },
  ) => postJson<ExperienceSelection>(
    `/api/v2/tasks/${taskId}/experience-selections`, payload,
  ),
  loop: (runId: string) => getJson<LoopExecution>(`/api/v1/runs/${runId}/loop`),
  graph: (runId: string) => getJson<GraphProjection>(`/api/v1/runs/${runId}/graph`),
  trace: (runId: string) => getJson<ExecutionTrace>(`/api/v1/runs/${runId}/trace`),
  templates: () => getJson<WorkflowTemplateSummary[]>("/api/v1/templates"),
  approvals: (runId?: string, status?: string) =>
    getJson<ApprovalRecord[]>(
      `/api/v1/approvals?${runId ? `run_id=${runId}&` : ""}${status ? `status=${status}` : ""}`,
    ),
  decideApproval: (approvalId: string, decision: ApprovalDecisionValue) =>
    postJson<ApprovalRecord>(`/api/v1/approvals/${approvalId}/decision`, { decision }),
  verifications: (runId: string) =>
    getJson<VerificationResult[]>(`/api/v1/runs/${runId}/verifications`),
  verification: (verificationId: string) =>
    getJson<VerificationResult>(`/api/v1/verifications/${verificationId}`),
  capabilities: () => getJson<Capability[]>("/api/v1/capabilities"),
  skills: () => getJson<MetaSkill[]>("/api/v1/skills"),
  plugins: () => getJson<MetaPlugin[]>("/api/v1/plugins"),
  pluginDetail: (pluginId: string, workspaceId?: string) =>
    getJson<PluginDetail>(
      `/api/v1/plugins/${pluginId}${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
    ),
  pluginInstallations: (workspaceId?: string) =>
    getJson<PluginInstallation[]>(
      `/api/v1/plugins/installations${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
    ),
  pluginAudit: (filters: PluginAuditFilters = {}) => {
    const query = new URLSearchParams(
      Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
    );
    return getJson<PluginAuditEvent[]>(`/api/v1/audit/plugins?${query}`);
  },
  enterpriseAuthProfile: () =>
    getJson<EnterpriseAuthProfileResponse>("/api/v1/enterprise-auth/profile"),
  enterpriseAuthAudit: (filters: EnterpriseAuthAuditFilters = {}) => {
    const query = new URLSearchParams(
      Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
    );
    return getJson<EnterpriseAuthGrant[]>(`/api/v1/audit/enterprise-auth?${query}`);
  },
  enterpriseAuthorizeMcpServer: (mcpServerId: string) =>
    postJson<ConnectionSummary>(
      `/api/v1/mcp/servers/${mcpServerId}/enterprise-authorize`,
      {},
    ),
  connectors: () => getJson<ConnectorDefinition[]>("/api/v1/connectors"),
  connections: () => getJson<ConnectionSummary[]>("/api/v1/connections"),
  connect: (connectorId: string, payload: ConnectCreate) =>
    postJson<AuthorizationStart>(`/api/v1/connectors/${connectorId}/connect`, payload),
  reauthorize: (connectionId: string, payload: ConnectCreate) =>
    postJson<AuthorizationStart>(
      `/api/v1/connections/${connectionId}/reauthorize`,
      payload,
    ),
  revoke: (connectionId: string) =>
    postJson<ConnectionSummary>(`/api/v1/connections/${connectionId}/revoke`, {}),
  connectionHealth: (connectionId: string) =>
    getJson<Record<string, unknown>>(`/api/v1/connections/${connectionId}/health`),
  mcpServers: (workspaceId?: string) =>
    getJson<McpServerDefinition[]>(
      `/api/v1/mcp/servers${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
    ),
  mcpServerCapabilities: (mcpServerId: string) =>
    getJson<Capability[]>(`/api/v1/mcp/servers/${mcpServerId}/capabilities`),
  mcpServerDiscovery: (mcpServerId: string) =>
    getJson<McpDiscoverySnapshot>(`/api/v1/mcp/servers/${mcpServerId}/discovery`),
  resolveCapability: (payload: CapabilityResolveRequest) =>
    postJson<ResolvedCapability>("/api/v1/capabilities/resolve", payload),
  workspaces: () => getJson<WorkspaceEntity[]>("/api/v1/workspaces"),
  authProviders: () => getJson<AuthProviderInfo[]>("/api/v1/auth/providers"),
  acrArch: (filters: AcrArchFilters = {}) => {
    const query = new URLSearchParams(
      Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
    );
    return getJson<AcrArchSummary>(`/api/v1/benchmarks/acr-arch?${query}`);
  },
  runAcrArch: () => postJson<BenchmarkRun>(
    "/api/v1/benchmarks/acr-arch/run",
    { execution_source: "REPLAY" },
  ),
  acrArchTask: (taskId: string) =>
    getJson<BenchmarkTaskDetail>(`/api/v1/benchmarks/acr-arch/tasks/${taskId}`),
  searchBenchmark: () =>
    getJson<SearchBenchmarkSummary>("/api/v2/benchmarks/search"),
  runSearchBenchmark: () =>
    postJson<SearchBenchmarkSummary>(
      "/api/v2/benchmarks/search/run",
      { execution_source: "REPLAY" },
    ),
  dynamicBenchmark: () =>
    getJson<DynamicWorkflowBenchmarkSummary>("/api/v2/benchmarks/dynamic"),
  runDynamicBenchmark: () =>
    postJson<DynamicWorkflowBenchmarkSummary>(
      "/api/v2/benchmarks/dynamic/run",
      { execution_source: "REPLAY" },
    ),
  experienceBenchmark: () =>
    getJson<ExperienceBenchmarkSummary>("/api/v2/benchmarks/experience"),
  runExperienceBenchmark: () =>
    postJson<ExperienceBenchmarkSummary>(
      "/api/v2/benchmarks/experience/run",
      { execution_source: "REPLAY" },
    ),
  eventUrl: (runId: string, after = 0) =>
    `${API_ROOT}/api/v1/runs/${runId}/events?after=${after}`,
};
