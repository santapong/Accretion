import { FormEvent, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useParams,
} from "react-router-dom";
import { api, type AcrArchFilters } from "./api";
import { RunExecution } from "./RunExecution";
import type {
  AgentEvent,
  Project,
  Run,
  TaskCreate,
  TaskPlanning,
  WorkflowProposal,
  WorkflowValidationOutcome,
} from "./types";
import "./styles.css";

const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-5)}`;
}

function StatePill({ state }: { state: string }) {
  return <span className={`pill pill-${state.toLowerCase()}`}>{state.replaceAll("_", " ")}</span>;
}

type StrategyMode = "DIRECT" | "LOOP" | "GRAPH" | "HYBRID";

function lines(value: FormDataEntryValue | null) {
  return String(value ?? "").split("\n").map((item) => item.trim()).filter(Boolean);
}

function ProjectCreator({ onCreated }: { onCreated: (project: Project) => void }) {
  const [status, setStatus] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setStatus("Creating project…");
    try {
      const project = await api.createProject({
        name: String(data.get("name")),
        repository_path: String(data.get("repository_path")),
      });
      form.reset();
      onCreated(project);
      setStatus(`Created ${project.name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Project creation failed.");
    }
  }

  return (
    <form className="project-form" onSubmit={submit}>
      <label>Project name<input name="name" required placeholder="Accretion" /></label>
      <label>Local repository path<input name="repository_path" required placeholder="/workspace/repository" /></label>
      <button className="secondary-button" type="submit">Add project</button>
      {status ? <p className="form-status" role="status">{status}</p> : null}
    </form>
  );
}

function NewTaskForm({ projects, onPlanning }: {
  projects: Project[];
  onPlanning: (planning: TaskPlanning) => void;
}) {
  const [projectId, setProjectId] = useState("");
  const [status, setStatus] = useState<string>();
  const selectedProjectId = projectId || projects[0]?.project_id || "";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const requiredOutputs = lines(data.get("required_outputs")).map((path) => ({
      path,
      kind: "file",
      non_empty: true,
    }));
    const payload: TaskCreate = {
      project_id: String(data.get("project_id")),
      objective: String(data.get("objective")),
      task_type: String(data.get("task_type")) as TaskCreate["task_type"],
      risk_level: String(data.get("risk_level")) as TaskCreate["risk_level"],
      constraints: lines(data.get("constraints")),
      success_criteria: lines(data.get("success_criteria")),
      allowed_capabilities: lines(data.get("allowed_capabilities")),
      denied_capabilities: lines(data.get("denied_capabilities")),
      required_outputs: requiredOutputs,
      budgets: {
        wall_time_seconds: Number(data.get("wall_time_seconds")),
        max_turns: Number(data.get("max_turns")),
        max_tool_calls: Number(data.get("max_tool_calls")),
        max_loop_iterations: Number(data.get("max_loop_iterations")),
        max_parallel_runs: Number(data.get("max_parallel_runs")),
      },
    };
    setStatus("Profiling task…");
    try {
      const task = await api.createTask(payload);
      const planning = await api.planning(task.envelope.task_id);
      onPlanning(planning);
      setStatus("Task created. Review the deterministic plan below.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Task creation failed.");
    }
  }

  return (
    <form className="task-form" onSubmit={submit}>
      <label className="field-wide">Objective<textarea name="objective" required rows={3} placeholder="Describe the outcome without routing instructions." /></label>
      <label>Project<select name="project_id" required value={selectedProjectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select a project</option>{projects.map((project) => <option value={project.project_id} key={project.project_id}>{project.name}</option>)}</select></label>
      <label>Task type<select name="task_type" defaultValue="OTHER"><option>RESEARCH</option><option>ANALYSIS</option><option>IMPLEMENT</option><option>REVIEW</option><option>EXPERIMENT</option><option>OTHER</option></select></label>
      <label>Risk<select name="risk_level" defaultValue="LOW"><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
      <label>Wall time (seconds)<input name="wall_time_seconds" type="number" min="1" defaultValue="1800" /></label>
      <label>Max turns<input name="max_turns" type="number" min="1" defaultValue="20" /></label>
      <label>Max tool calls<input name="max_tool_calls" type="number" min="1" defaultValue="100" /></label>
      <label>Loop iterations<input name="max_loop_iterations" type="number" min="1" defaultValue="1" /></label>
      <label>Parallel runs<input name="max_parallel_runs" type="number" min="1" defaultValue="1" /></label>
      <label>Constraints <small>one per line</small><textarea name="constraints" rows={4} /></label>
      <label>Success criteria <small>one per line</small><textarea name="success_criteria" rows={4} /></label>
      <label>Allowed capabilities <small>one per line</small><textarea name="allowed_capabilities" rows={4} /></label>
      <label>Denied capabilities <small>one per line</small><textarea name="denied_capabilities" rows={4} /></label>
      <label className="field-wide">Required output paths <small>one repository-relative file path per line</small><textarea name="required_outputs" rows={3} placeholder={"reports/result.json\nsrc/generated-summary.md"} /></label>
      <div className="form-actions field-wide"><button className="primary-button" disabled={!projects.length} type="submit">Create and profile task</button>{status ? <p className="form-status" role="status">{status}</p> : null}</div>
    </form>
  );
}

function Score({ label, value }: { label: string; value: number | null | undefined }) {
  return <div className="score"><span>{value == null ? "UNKNOWN" : value.toFixed(2)}</span><p>{label}</p></div>;
}

function PlanningReview({ planning, onUpdate }: {
  planning: TaskPlanning;
  onUpdate: (planning: TaskPlanning) => void;
}) {
  const profile = planning.current_profile;
  const decision = planning.current_decision;
  const unknownFeatures = profile.unknown_features ?? [];
  const observedFeatures = profile.observed_features ?? [];
  const templatesQuery = useQuery({ queryKey: ["templates"], queryFn: api.templates });
  const [mode, setMode] = useState<StrategyMode>(decision.selected_mode as StrategyMode);
  const [templateId, setTemplateId] = useState(decision.selected_template_id);
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState<string>();
  const [provider, setProvider] = useState("FAKE");
  const [dynamicProposal, setDynamicProposal] = useState<WorkflowProposal>();
  const [dynamicValidation, setDynamicValidation] = useState<WorkflowValidationOutcome>();
  const modeTemplates = (templatesQuery.data ?? []).filter(
    (template) => template.mode === mode,
  );
  const selectedTemplate = modeTemplates.some((template) => template.template_id === templateId)
    ? templateId
    : modeTemplates[0]?.template_id ?? "";

  async function override(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback("Evaluating override…");
    try {
      const result = await api.overrideStrategy(planning.task_id, {
        requested_mode: mode,
        requested_template_id: selectedTemplate,
        reason,
      });
      setFeedback(result.override.accepted ? "Override accepted and audited." : `Override denied: ${result.override.denial_reason}`);
      onUpdate(await api.planning(planning.task_id));
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Override failed.");
    }
  }

  async function run() {
    setFeedback("Creating run…");
    try {
      const created = await api.startRun(planning.task_id, { provider: provider as "FAKE" | "CODEX" | "CLAUDE" });
      setFeedback(`Run ${shortId(created.run_id)} created.`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Run creation failed.");
    }
  }

  async function proposeDynamic() {
    setFeedback("Preparing a governed P5 workflow proposal…");
    try {
      const task = await api.task(planning.task_id);
      const features = await api.projectFeatures(task.envelope.project_id);
      if (!features.dynamic_workflows) {
        await api.updateProjectFeatures(
          task.envelope.project_id,
          true,
          features.revision,
        );
      }
      const proposal = await api.proposeWorkflow(planning.task_id, provider);
      const validation = await api.validateWorkflow(proposal.run_id!, proposal.proposal_id);
      setDynamicProposal(validation.proposal);
      setDynamicValidation(validation);
      setFeedback(
        validation.validation.status === "ACCEPT"
          ? "P5 graph accepted. Review the proposal before activation."
          : validation.fallback_run_id
            ? `Proposal rejected; static fallback ${shortId(validation.fallback_run_id)} started.`
            : "Proposal requires operator attention.",
      );
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Dynamic proposal failed.");
    }
  }

  async function activateDynamic() {
    if (!dynamicProposal?.run_id) return;
    setFeedback("Activating the validated P5 graph…");
    try {
      const activation = await api.activateWorkflow(
        dynamicProposal.run_id,
        dynamicProposal.proposal_id,
      );
      setFeedback(
        `Dynamic run ${shortId(activation.run_id)} activated at revision ${activation.revision.revision}.`,
      );
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Dynamic activation failed.");
    }
  }

  return (
    <section className="planning-review" aria-label="Task planning review">
      <header className="panel-header"><div><p className="eyebrow">Deterministic profile</p><h2>Planning decision</h2></div><StatePill state={decision.selected_mode} /></header>
      <div className="planning-content">
        <div className="score-grid">
          <Score label="complexity" value={profile.complexity} />
          <Score label="structure" value={profile.structure_certainty} />
          <Score label="feedback" value={profile.feedback_dependency} />
          <Score label="dependencies" value={profile.dependency_complexity} />
          <Score label="parallelism" value={profile.parallelism_potential} />
          <Score label="uncertainty" value={profile.uncertainty} />
          <Score label="verifier" value={profile.verifier_strength} />
          <Score label="confidence" value={profile.profile_confidence} />
        </div>
        {unknownFeatures.length ? <div className="notice"><strong>Unknown features</strong><p>{unknownFeatures.join(", ")}</p></div> : null}
        <div className="decision-grid">
          <div><p className="eyebrow">Selected strategy</p><h3>{decision.selected_mode} / {decision.selected_template_id}</h3><p>{decision.rationale}</p></div>
          <div><p className="eyebrow">Matched rules</p><p>{(decision.matched_rules ?? []).join(" · ")}</p><p>Alternatives: {(decision.alternatives ?? []).join(", ")}</p></div>
          <div><p className="eyebrow">Safety requirements</p><p>Approval: {decision.requires_approval ? "required" : "not required"}<br />Independent verifier: {decision.requires_independent_verifier ? "required" : "not required"}</p></div>
        </div>
        <details><summary>Feature evidence ({observedFeatures.length})</summary><div className="evidence-list">{observedFeatures.map((item) => <article key={`${item.feature}-${item.source}`}><strong>{item.feature}</strong><span>{item.available ? JSON.stringify(item.value) : "UNKNOWN"}</span><p>{item.rationale}</p></article>)}</div></details>
        <div className="planning-actions">
          <form className="override-form" onSubmit={override}>
            <label>Override mode<select value={mode} onChange={(event) => setMode(event.target.value as StrategyMode)}><option>DIRECT</option><option>LOOP</option><option>GRAPH</option><option>HYBRID</option></select></label>
            <label>Template<select value={selectedTemplate} onChange={(event) => setTemplateId(event.target.value)}>{modeTemplates.map((template) => <option value={template.template_id} key={template.template_id}>{template.template_id}</option>)}</select></label>
            <label className="reason-field">Reason<input value={reason} onChange={(event) => setReason(event.target.value)} required placeholder="Required for the audit record" /></label>
            <button className="secondary-button" type="submit" disabled={!selectedTemplate}>Request override</button>
          </form>
          <div className="run-control">
            <label>Runtime<select value={provider} onChange={(event) => setProvider(event.target.value)}><option>FAKE</option><option>CODEX</option><option>CLAUDE</option></select></label>
            <button className="primary-button" type="button" onClick={run}>Create run</button>
            <button className="secondary-button" type="button" onClick={proposeDynamic}>Propose P5 graph</button>
            {dynamicValidation?.validation.status === "ACCEPT" ? (
              <button className="primary-button" type="button" onClick={activateDynamic}>
                Activate revision 1
              </button>
            ) : null}
          </div>
        </div>
        {dynamicProposal ? (
          <aside className="dynamic-proposal-preview">
            <div><p className="eyebrow">Pending dynamic workflow</p><h3>{(dynamicProposal.fragment_refs ?? []).join(" · ")}</h3></div>
            <StatePill state={dynamicValidation?.validation.status ?? "PENDING"} />
            <p>{dynamicProposal.rationale_summary}</p>
            <small>{dynamicProposal.nodes.length} nodes · {(dynamicProposal.edges ?? []).length} edges · planner {dynamicProposal.planner_version}</small>
          </aside>
        ) : null}
        {feedback ? <p className="form-status" role="status">{feedback}</p> : null}
      </div>
    </section>
  );
}

export function EventStream({ run }: { run: Run | undefined }) {
  const queryClient = useQueryClient();
  const [liveEvents, setLiveEvents] = useState<{ runId: string; events: AgentEvent[] }>({
    runId: "",
    events: [],
  });
  const [connectionState, setConnectionState] = useState<{ runId: string; value: string }>({
    runId: "",
    value: "idle",
  });
  const [reconnect, setReconnect] = useState(0);
  const runId = run?.run_id;
  const runState = run?.state;
  const auditQuery = useQuery({
    queryKey: ["run-audit", runId],
    queryFn: () => api.audit(runId!),
    enabled: Boolean(runId),
    retry: false,
  });

  const eventsById = new Map(
    [
      ...(auditQuery.data?.events ?? []),
      ...(liveEvents.runId === runId ? liveEvents.events : []),
    ].map((event) => [event.event_id, event]),
  );
  const events = [...eventsById.values()].sort((left, right) => left.sequence - right.sequence);
  const connection = connectionState.runId === runId
    ? connectionState.value
    : auditQuery.data
      ? "connecting"
      : "idle";

  useEffect(() => {
    if (!runId || !auditQuery.data) return;
    let recovering = false;
    let expected = auditQuery.data.run.last_sequence + 1;
    const source = new EventSource(api.eventUrl(runId, auditQuery.data.run.last_sequence));
    source.addEventListener("open", () => setConnectionState({ runId, value: "live" }));
    source.addEventListener("agent_event", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as AgentEvent;
      if (event.sequence < expected) return;
      if (event.sequence !== expected) {
        recovering = true;
        source.close();
        setConnectionState({ runId, value: "recovering snapshot" });
        void Promise.all([
          queryClient.refetchQueries({ queryKey: ["run-audit", runId] }),
          queryClient.refetchQueries({ queryKey: ["run-graph", runId] }),
          queryClient.refetchQueries({ queryKey: ["run-trace", runId] }),
          queryClient.refetchQueries({ queryKey: ["runs"] }),
        ]).then(() => setReconnect((value) => value + 1));
        return;
      }
      expected = event.sequence + 1;
      setLiveEvents((current) => ({
        runId,
        events: [
          ...(current.runId === runId
            ? current.events.filter((item) => item.event_id !== event.event_id)
            : []),
          event,
        ],
      }));
    });
    source.addEventListener("error", () => {
      if (!recovering) {
        setConnectionState({
          runId,
          value: runState && terminal.has(runState) ? "complete" : "reconnecting",
        });
      }
    });
    return () => source.close();
  }, [auditQuery.data, queryClient, reconnect, runId, runState]);

  if (!run) {
    return <div className="empty">Select a run to inspect its normalized trace.</div>;
  }

  return (
    <section className="event-panel">
      <header className="panel-header">
        <div>
          <p className="eyebrow">Normalized trace</p>
          <h2>{shortId(run.run_id)}</h2>
        </div>
        <span className={`connection connection-${connection}`}><i />{connection}</span>
      </header>
      <div className="event-list" aria-live="polite">
        {auditQuery.isLoading ? <div className="empty">Loading authoritative snapshot…</div> : null}
        {!auditQuery.isLoading && events.length === 0 ? <div className="empty">Waiting for events…</div> : null}
        {events.map((event) => (
          <article className="event" key={event.event_id}>
            <span className="sequence">{String(event.sequence).padStart(3, "0")}</span>
            <div>
              <strong>{event.normalized_type.replaceAll("_", " ")}</strong>
              <p>{event.provider} · {event.native_type}</p>
            </div>
            <time>{event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : "pending"}</time>
          </article>
        ))}
      </div>
    </section>
  );
}

function RuntimeCards() {
  const runtimeQuery = useQuery({
    queryKey: ["runtimes"],
    queryFn: api.runtimes,
    refetchInterval: 5000,
  });
  return (
    <section className="runtime-grid" aria-label="Runtime health">
      {(runtimeQuery.data ?? []).map((runtime) => (
        <article className="runtime-card" key={runtime.runtime_id}>
          <div className="runtime-title"><span>{runtime.provider.slice(0, 1)}</span><h2>{runtime.provider}</h2></div>
          <StatePill state={runtime.status} />
          <dl>
            <div><dt>Version</dt><dd>{runtime.runtime_version}</dd></div>
            <div><dt>Pressure</dt><dd>{runtime.observed_usage_pressure}</dd></div>
            <div><dt>Active</dt><dd>{runtime.active_runs}</dd></div>
          </dl>
        </article>
      ))}
      {runtimeQuery.isError ? <div className="error">Runtime health is unavailable.</div> : null}
    </section>
  );
}

function DashboardPage() {
  const runQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 2500 });
  const approvalQuery = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.approvals(undefined, "PENDING"),
    refetchInterval: 2500,
  });
  const runs = runQuery.data ?? [];
  const activeCount = runs.filter((run) => !terminal.has(run.state)).length;
  const failureCount = runs.filter((run) => run.state === "FAILED").length;
  return (
    <>
      <header className="hero">
        <div><p className="eyebrow">Runtime observatory</p><h1>One control plane.<br /><em>Every execution visible.</em></h1></div>
        <div className="dashboard-stats">
          <div className="stat"><span>{activeCount}</span><p>active runs</p></div>
          <div className="stat"><span>{failureCount}</span><p>failures</p></div>
          <div className="stat"><span>{approvalQuery.data?.length ?? 0}</span><p>approvals</p></div>
        </div>
      </header>
      <RuntimeCards />
      <section className="runs-panel page-panel">
        <header className="panel-header"><div><p className="eyebrow">Recent activity</p><h2>Runs</h2></div><Link to="/tasks/new" className="secondary-button">New task</Link></header>
        <div className="run-list">
          {runs.map((run) => (
            <Link className="run" key={run.run_id} to={`/runs/${run.run_id}`}>
              <span className="provider-dot">{run.provider.slice(0, 1)}</span>
              <span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events</small></span>
              <StatePill state={run.state} />
            </Link>
          ))}
          {!runs.length ? <div className="empty">No runs yet. Create and profile a task.</div> : null}
        </div>
      </section>
    </>
  );
}

function NewTaskPage() {
  const queryClient = useQueryClient();
  const projectQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [planning, setPlanning] = useState<TaskPlanning>();
  return (
    <>
      <section className="task-studio page-panel">
        <header className="section-heading"><div><p className="eyebrow">Create and review</p><h2>New task</h2></div><span>deterministic-profiler-v1</span></header>
        <ProjectCreator onCreated={(project) => { queryClient.setQueryData<Project[]>(["projects"], (current = []) => [...current, project]); }} />
        <NewTaskForm projects={projectQuery.data ?? []} onPlanning={setPlanning} />
      </section>
      {planning ? <PlanningReview planning={planning} onUpdate={setPlanning} /> : null}
    </>
  );
}

function LiveRunPage() {
  const { runId } = useParams();
  const runQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 2500 });
  const selected = (runQuery.data ?? []).find((run) => run.run_id === runId);
  return (
    <div className="inspector-stack page-stack">
      <header className="section-heading"><div><p className="eyebrow">Live run</p><h2>{runId ? shortId(runId) : "Run not selected"}</h2></div>{selected ? <StatePill state={selected.state} /> : null}</header>
      <RunExecution run={selected} />
      <EventStream run={selected} />
    </div>
  );
}

function RuntimeSessions({ runtimeId }: { runtimeId: string }) {
  const sessions = useQuery({
    queryKey: ["runtime-sessions", runtimeId],
    queryFn: () => api.runtimeSessions(runtimeId),
    refetchInterval: 5000,
  });
  return (
    <ul className="registry-list">
      {(sessions.data ?? []).map((session) => (
        <li key={session.session_id}><strong>{shortId(session.session_id)}</strong><span>{shortId(session.run_id)} · {session.native_session_id ? shortId(session.native_session_id) : "pending native session"}</span></li>
      ))}
      {!sessions.data?.length ? <li><span>No persisted sessions.</span></li> : null}
    </ul>
  );
}

function RuntimeMonitorPage() {
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: api.runtimes, refetchInterval: 5000 });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Provider health</p><h2>Runtime monitor</h2></div></header>
      <div className="registry-grid">
        {(runtimes.data ?? []).map((runtime) => (
          <article className="registry-card" key={runtime.runtime_id}>
            <header><h3>{runtime.provider}</h3><StatePill state={runtime.status} /></header>
            <p>{runtime.runtime_version} · {runtime.auth_mode} · {runtime.observed_usage_pressure}</p>
            <RuntimeSessions runtimeId={runtime.runtime_id} />
          </article>
        ))}
      </div>
    </section>
  );
}

function HistoryPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const [selectedId, setSelectedId] = useState<string>();
  const selected = selectedId ?? runs.data?.[0]?.run_id;
  const audit = useQuery({
    queryKey: ["run-audit", selected],
    queryFn: () => api.audit(selected!),
    enabled: Boolean(selected),
  });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Immutable evidence</p><h2>Run history / trace replay</h2></div></header>
      <div className="history-grid">
        <div className="run-list">{(runs.data ?? []).map((run) => <button className={selected === run.run_id ? "run selected" : "run"} key={run.run_id} onClick={() => setSelectedId(run.run_id)}><span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events</small></span><StatePill state={run.state} /></button>)}</div>
        {audit.data ? (
          <article className="audit-chain">
            <h3>Complete provenance</h3>
            <ol>
              <li>Task <strong>{shortId(audit.data.task.envelope.task_id)}</strong></li>
              <li>Profile <strong>{shortId(audit.data.profile.profile_id)}</strong></li>
              <li>Strategy <strong>{audit.data.strategy.selected_mode}</strong></li>
              <li>Template <strong>{audit.data.template.template_id}@{audit.data.template.version}</strong></li>
              <li>Runtime <strong>{audit.data.runtime.provider} / {audit.data.runtime.runtime_version}</strong></li>
              <li>Events <strong>{audit.data.events?.length ?? 0}</strong></li>
              <li>Artifacts <strong>{audit.data.artifacts?.length ?? 0}</strong></li>
              <li>Verifications <strong>{audit.data.verifications?.length ?? 0}</strong></li>
            </ol>
            <p>{audit.data.trace.traversals?.length ?? 0} traversals · {audit.data.trace.tool_calls?.length ?? 0} tool calls · {audit.data.trace.checkpoints?.length ?? 0} checkpoints</p>
            <Link to={`/runs/${audit.data.run.run_id}`} className="primary-button">Open live run</Link>
          </article>
        ) : <div className="empty">Select a run to replay its linked audit trace.</div>}
      </div>
    </section>
  );
}

function ApprovalsPage() {
  const queryClient = useQueryClient();
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  async function decide(approvalId: string, decision: "APPROVE" | "DENY") {
    await api.decideApproval(approvalId, decision);
    await queryClient.invalidateQueries({ queryKey: ["approvals"] });
  }
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Human authority</p><h2>Verifiers / approvals</h2></div></header>
      <div className="registry-list">{(approvals.data ?? []).map((approval) => <article className="approval-request" key={approval.approval_id}><div><strong>{approval.summary || approval.method}</strong><small>{shortId(approval.run_id)} · {approval.status}</small></div>{approval.status === "PENDING" ? <div className="approval-actions"><button className="primary-button" onClick={() => decide(approval.approval_id, "APPROVE")}>Approve</button><button className="secondary-button" onClick={() => decide(approval.approval_id, "DENY")}>Deny</button></div> : <StatePill state={approval.status} />}</article>)}</div>
      {!approvals.data?.length ? <div className="empty">No approval records.</div> : null}
    </section>
  );
}

function CapabilitiesPage() {
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
  const skills = useQuery({ queryKey: ["skills"], queryFn: api.skills });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Read-only registry</p><h2>Capabilities, skills, and plugins</h2></div></header>
      <div className="registry-grid">
        <article className="registry-card"><h3>Capabilities</h3><ul className="registry-list">{(capabilities.data ?? []).map((item) => <li key={`${item.capability_id}:${item.version}`}><strong>{item.capability_id}@{item.version}</strong><span>{item.kind} · {item.risk} · {item.backend}</span></li>)}</ul></article>
        <article className="registry-card"><h3>Skills</h3><ul className="registry-list">{(skills.data ?? []).map((item) => <li key={`${item.skill_id}:${item.version}`}><strong>{item.skill_id}@{item.version}</strong><span>{(item.required_capabilities ?? []).join(", ")}</span></li>)}</ul></article>
        <article className="registry-card"><h3>Plugins</h3><ul className="registry-list">{(plugins.data ?? []).map((item) => <li key={`${item.plugin_id}:${item.version}`}><strong>{item.plugin_id}@{item.version}</strong><span>{(item.skill_refs ?? []).join(", ")}</span></li>)}</ul></article>
      </div>
    </section>
  );
}

function BenchmarkPage() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<AcrArchFilters>({});
  const [selectedTask, setSelectedTask] = useState<string>();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["acr-arch", filters],
    queryFn: () => api.acrArch(filters),
  });
  const detail = useQuery({
    queryKey: ["acr-arch-task", selectedTask],
    queryFn: () => api.acrArchTask(selectedTask!),
    enabled: Boolean(selectedTask),
  });
  const options = summary.data?.filters ?? {};

  function select(name: keyof AcrArchFilters, value: string) {
    setFilters((current) => ({ ...current, [name]: value || undefined }));
  }

  async function replay() {
    setStatus("Replaying frozen provider traces…");
    try {
      const run = await api.runAcrArch();
      setStatus(`Reproduced ${run.scenario_count} scenarios from ${run.trace_sha256.slice(0, 12)}…`);
      await queryClient.invalidateQueries({ queryKey: ["acr-arch"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Benchmark replay failed.");
    }
  }

  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Architecture evaluation</p><h2>ACR-ARCH</h2></div><button className="primary-button" onClick={replay}>Reproduce replay</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>tasks</span></div>
        <div><strong>{summary.data?.scenario_count ?? 0}</strong><span>mode scenarios</span></div>
        <div><strong>{summary.data?.suite_version ?? "—"}</strong><span>corpus version</span></div>
        <div><strong>{summary.data?.configuration_version ?? "—"}</strong><span>config version</span></div>
      </div>
      <div className="benchmark-filters" aria-label="ACR-ARCH filters">
        {(["mode", "provider", "task_type", "verifier", "selector_version"] as const).map((name) => (
          <label key={name}>{name.replaceAll("_", " ")}<select value={filters[name] ?? ""} onChange={(event) => select(name, event.target.value)}><option value="">All</option>{(options[name] ?? []).map((value) => <option key={value}>{value}</option>)}</select></label>
        ))}
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      <div className="benchmark-table-wrap">
        <table className="benchmark-table">
          <thead><tr><th>Task</th><th>Mode</th><th>Provider</th><th>Success</th><th>Quality</th><th>Cost</th><th>Latency</th><th>Risk</th><th>Human</th><th>Utility</th><th>Regret</th></tr></thead>
          <tbody>{(summary.data?.metrics ?? []).map((metric) => <tr key={metric.metric_id}><td><button onClick={() => setSelectedTask(metric.benchmark_task_id)}>{metric.benchmark_task_id}</button></td><td>{metric.mode}</td><td>{metric.provider}</td><td><StatePill state={metric.success ? "PASS" : "FAIL"} /></td><td>{metric.quality.toFixed(3)}</td><td>{metric.cost.toFixed(3)}</td><td>{metric.latency.toFixed(3)}</td><td>{metric.risk.toFixed(3)}</td><td>{metric.human_burden.toFixed(3)}</td><td>{metric.utility.toFixed(3)}</td><td>{metric.architecture_regret.toFixed(3)}</td></tr>)}</tbody>
        </table>
        {!summary.isLoading && !summary.data?.metrics?.length ? <div className="empty">No benchmark scenarios match these filters.</div> : null}
      </div>
      {detail.data ? <aside className="benchmark-detail"><div><p className="eyebrow">Versioned task</p><h3>{detail.data.task.title}</h3></div><StatePill state={detail.data.task.category} /><p>{detail.data.task.environment_ref}@{detail.data.task.environment_version} · {detail.data.task.verifier_id}@{detail.data.task.verifier_version}</p><ul>{detail.data.task.success_criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}</ul></aside> : null}
    </section>
  );
}

const navigation = [
  ["/", "Dashboard"], ["/tasks/new", "New task"], ["/runtimes", "Runtimes"],
  ["/history", "History"], ["/approvals", "Approvals"], ["/capabilities", "Capabilities"],
  ["/benchmarks/acr-arch", "ACR-ARCH"],
] as const;

function OperatorShell() {
  return (
    <main>
      <nav>
        <Link className="brand-link" to="/"><span className="brand-mark">A</span><span><strong>Accretion</strong><small>Operator / P4</small></span></Link>
        <div className="nav-links">{navigation.map(([path, label]) => <NavLink end={path === "/"} key={path} to={path}>{label}</NavLink>)}</div>
        <div className="nav-status"><i />Control plane</div>
      </nav>
      <div className="shell">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/tasks/new" element={<NewTaskPage />} />
          <Route path="/runs/:runId" element={<LiveRunPage />} />
          <Route path="/runtimes" element={<RuntimeMonitorPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/capabilities" element={<CapabilitiesPage />} />
          <Route path="/benchmarks/acr-arch" element={<BenchmarkPage />} />
          <Route path="*" element={<section className="page-panel"><h2>Page not found</h2><Link to="/">Return to dashboard</Link></section>} />
        </Routes>
      </div>
    </main>
  );
}

export default function App() {
  return <BrowserRouter><OperatorShell /></BrowserRouter>;
}
