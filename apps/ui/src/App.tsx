import { FormEvent, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { RunExecution } from "./RunExecution";
import type { AgentEvent, Project, Run, TaskCreate, TaskPlanning } from "./types";
import "./styles.css";

const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-5)}`;
}

function StatePill({ state }: { state: string }) {
  return <span className={`pill pill-${state.toLowerCase()}`}>{state.replaceAll("_", " ")}</span>;
}

const strategyTemplates = {
  DIRECT: "direct-v1",
  LOOP: "feedback-loop-v1",
  GRAPH: "fixed-graph-v1",
  HYBRID: "hybrid-rd-v1",
} as const;

type StrategyMode = keyof typeof strategyTemplates;

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

  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].project_id);
  }, [projectId, projects]);

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
      <label>Project<select name="project_id" required value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select a project</option>{projects.map((project) => <option value={project.project_id} key={project.project_id}>{project.name}</option>)}</select></label>
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
  const [mode, setMode] = useState<StrategyMode>(decision.selected_mode as StrategyMode);
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState<string>();
  const [provider, setProvider] = useState("FAKE");
  const directSupported = decision.selected_mode === "DIRECT" && decision.selected_template_id === "direct-v1";
  const loopSupported = decision.selected_mode === "LOOP" && decision.selected_template_id === "feedback-loop-v1";
  const executionSupported = directSupported || loopSupported;

  async function override(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback("Evaluating override…");
    try {
      const result = await api.overrideStrategy(planning.task_id, {
        requested_mode: mode,
        requested_template_id: strategyTemplates[mode],
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
            <label className="reason-field">Reason<input value={reason} onChange={(event) => setReason(event.target.value)} required placeholder="Required for the audit record" /></label>
            <button className="secondary-button" type="submit">Request override</button>
          </form>
          <div className="run-control">
            <label>Runtime<select value={provider} onChange={(event) => setProvider(event.target.value)}><option>FAKE</option><option>CODEX</option><option>CLAUDE</option></select></label>
            <button className="primary-button" type="button" disabled={!executionSupported} onClick={run}>Create run</button>
            {!executionSupported ? <p>Execution is blocked until P3. GRAPH, HYBRID, and safe-unknown decisions require graph and checkpoint support.</p> : null}
          </div>
        </div>
        {feedback ? <p className="form-status" role="status">{feedback}</p> : null}
      </div>
    </section>
  );
}

function EventStream({ run }: { run: Run | undefined }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connection, setConnection] = useState("idle");
  const runId = run?.run_id;
  const runState = run?.state;

  useEffect(() => {
    setEvents([]);
    if (!runId) return;
    setConnection("connecting");
    const source = new EventSource(api.eventUrl(runId));
    source.addEventListener("open", () => setConnection("live"));
    source.addEventListener("agent_event", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as AgentEvent;
      setEvents((current) => [...current.filter((item) => item.event_id !== event.event_id), event]);
    });
    source.addEventListener("error", () => {
      setConnection(runState && terminal.has(runState) ? "complete" : "reconnecting");
    });
    return () => source.close();
  }, [runId, runState]);

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
        {events.length === 0 ? <div className="empty">Waiting for events…</div> : null}
        {events.map((event) => (
          <article className="event" key={event.event_id}>
            <span className="sequence">{String(event.sequence).padStart(3, "0")}</span>
            <div>
              <strong>{event.normalized_type.replaceAll("_", " ")}</strong>
              <p>{event.provider} · {event.native_type}</p>
            </div>
            <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const queryClient = useQueryClient();
  const runtimeQuery = useQuery({ queryKey: ["runtimes"], queryFn: api.runtimes, refetchInterval: 5000 });
  const runQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 2500 });
  const projectQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [selectedId, setSelectedId] = useState<string>();
  const [planning, setPlanning] = useState<TaskPlanning>();
  const runs = runQuery.data ?? [];
  const selected = runs.find((run) => run.run_id === selectedId) ?? runs[0];
  const activeCount = runs.filter((run) => !terminal.has(run.state)).length;

  return (
    <main>
      <nav>
        <div className="brand-mark">A</div>
        <div><strong>Accretion</strong><span>Operator / P2</span></div>
        <div className="nav-status"><i />Control plane</div>
      </nav>

      <div className="shell">
        <section className="task-studio">
          <header className="section-heading"><div><p className="eyebrow">Create and review</p><h2>New task</h2></div><span>deterministic-profiler-v1</span></header>
          <ProjectCreator onCreated={(project) => { queryClient.setQueryData<Project[]>(["projects"], (current = []) => [...current, project]); }} />
          <NewTaskForm projects={projectQuery.data ?? []} onPlanning={setPlanning} />
        </section>
        {planning ? <PlanningReview planning={planning} onUpdate={setPlanning} /> : null}

        <header className="hero">
          <div>
            <p className="eyebrow">Runtime observatory</p>
            <h1>One control plane.<br /><em>Every execution visible.</em></h1>
          </div>
          <div className="stat"><span>{activeCount}</span><p>active runs</p></div>
        </header>

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

        <div className="workspace-grid">
          <section className="runs-panel">
            <header className="panel-header"><div><p className="eyebrow">Recent activity</p><h2>Runs</h2></div><span>{runs.length}</span></header>
            <div className="run-list">
              {runs.map((run) => (
                <button className={selected?.run_id === run.run_id ? "run selected" : "run"} key={run.run_id} onClick={() => setSelectedId(run.run_id)}>
                  <span className="provider-dot">{run.provider.slice(0, 1)}</span>
                  <span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events</small></span>
                  <StatePill state={run.state} />
                </button>
              ))}
              {runs.length === 0 ? <div className="empty">No runs yet. Create and profile a task above.</div> : null}
            </div>
          </section>
          <div className="inspector-stack">
            <RunExecution run={selected} />
            <EventStream run={selected} />
          </div>
        </div>
      </div>
    </main>
  );
}
