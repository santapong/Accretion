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
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { PluginsPage } from "./pages/PluginsPage";
import { McpServersPage } from "./pages/McpServersPage";
import { CapabilityInspectorPage } from "./pages/CapabilityInspectorPage";
import { IdentityPage } from "./pages/IdentityPage";
import type {
  AgentEvent,
  ExperienceDetail,
  ExperienceMatch,
  Project,
  Provider,
  Run,
  SearchMode,
  SearchRecord,
  Task,
  TaskCreate,
  TaskPlanning,
  WorkflowProposal,
  WorkflowValidationOutcome,
} from "./types";
// Tokens and the Tailwind layers first, then the legacy sheet. Order is documentation
// rather than cascade: styles.css is unlayered, so it wins either way.
import "./theme.css";
import "./styles.css";
import { durationLabel } from "./runDuration";

const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-5)}`;
}

/**
 * A clock that ticks only while something on screen is still running.
 *
 * A live run's elapsed time has to advance on its own — the run list polls every 2.5s,
 * but `HistoryPage` does not poll at all, so without this a live row there would show a
 * duration frozen at first render. The interval is created only when `active`, so a page
 * of finished runs installs no timer and the common case costs nothing.
 */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  // Up to one second stale on the tick that first activates the timer, which a duration
  // label cannot show. Resynchronising on activation would mean setting state inside the
  // effect, and a cascading render is a worse trade than a second of lag.
  return now;
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
  const [dynamicTask, setDynamicTask] = useState<Task>();
  const [searchMode, setSearchMode] = useState<SearchMode>("BEST_OF_N");
  const [searchRecord, setSearchRecord] = useState<SearchRecord>();
  const [experienceMatches, setExperienceMatches] = useState<ExperienceMatch[]>([]);
  const [experienceDetails, setExperienceDetails] = useState<Record<string, ExperienceDetail>>({});
  const [selectedMatchIds, setSelectedMatchIds] = useState<string[]>(
    planning.context_bundle.experience_match_refs ?? [],
  );
  const modeTemplates = (templatesQuery.data ?? []).filter(
    (template) => template.mode === mode,
  );
  const selectedTemplate = modeTemplates.some((template) => template.template_id === templateId)
    ? templateId
    : modeTemplates[0]?.template_id ?? "";

  useEffect(() => {
    if (planning.context_bundle.version !== "context-bundle-v2") return;
    let active = true;
    void api.selectedExperienceMatches(planning.task_id).then(async (matches) => {
      const details = await Promise.all(
        matches.map((match) => api.experience(match.experience_id)),
      );
      if (!active) return;
      setExperienceMatches(matches);
      setExperienceDetails(Object.fromEntries(
        details.map((detail) => [detail.experience.experience_id, detail]),
      ));
      setSelectedMatchIds(matches.map((match) => match.match_id));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [planning.context_bundle.version, planning.task_id]);

  async function retrieveExperiences() {
    setFeedback("Retrieving compatible verified experience…");
    try {
      const task = await api.task(planning.task_id);
      const features = await api.projectFeatures(task.envelope.project_id);
      if (!features.experience_retrieval) {
        await api.updateProjectFeatures(
          task.envelope.project_id,
          {
            dynamicWorkflows: true,
            candidateSearch: true,
            experienceRetrieval: true,
          },
          features.revision,
        );
      }
      const matches = await api.queryExperiences(planning.task_id);
      const details = await Promise.all(
        matches.map((match) => api.experience(match.experience_id)),
      );
      setExperienceMatches(matches);
      setExperienceDetails(Object.fromEntries(
        details.map((detail) => [detail.experience.experience_id, detail]),
      ));
      setSelectedMatchIds([]);
      setFeedback(
        matches.length
          ? `Retrieved ${matches.length} ranked experience match(es).`
          : "No repository-compatible experience matched this task.",
      );
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Experience retrieval failed.");
    }
  }

  function toggleExperience(matchId: string) {
    setSelectedMatchIds((current) => current.includes(matchId)
      ? current.filter((item) => item !== matchId)
      : current.length < 3 ? [...current, matchId] : current);
  }

  async function selectExperiences() {
    if (!selectedMatchIds.length || !experienceMatches.length) return;
    setFeedback("Freezing selected experience into ContextBundle v2…");
    try {
      await api.selectExperiences(planning.task_id, {
        query_id: experienceMatches[0].query_id,
        match_ids: selectedMatchIds,
        expected_context_bundle_id: planning.context_bundle.context_bundle_id,
      });
      onUpdate(await api.planning(planning.task_id));
      setFeedback("Experience selection frozen. A P5 proposal can now reference it.");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Experience selection failed.");
    }
  }

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
      const created = await api.startRun(planning.task_id, { provider: provider as Provider });
      setFeedback(`Run ${shortId(created.run_id)} created.`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Run creation failed.");
    }
  }

  async function proposeDynamic() {
    setFeedback("Preparing a governed P5 workflow proposal…");
    try {
      const task = await api.task(planning.task_id);
      setDynamicTask(task);
      const features = await api.projectFeatures(task.envelope.project_id);
      if (!features.dynamic_workflows) {
        await api.updateProjectFeatures(
          task.envelope.project_id,
          { dynamicWorkflows: true },
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

  async function attachSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dynamicProposal?.run_id || !dynamicTask) return;
    const data = new FormData(event.currentTarget);
    const replayMatches = experienceMatches.filter((match) =>
      (planning.context_bundle.experience_match_refs ?? []).includes(match.match_id)
    );
    const positiveReplayIds = replayMatches
      .filter((match) => match.polarity === "POSITIVE" && match.assessment.replay_eligible)
      .map((match) => match.match_id);
    const negativeGuidanceIds = replayMatches
      .filter((match) => match.polarity === "NEGATIVE" && match.assessment.negative_guidance_eligible)
      .map((match) => match.match_id);
    if (searchMode === "REPLAY_BRANCH" && !positiveReplayIds.length) {
      setFeedback("Replay requires at least one selected, currently eligible positive match.");
      return;
    }
    const branchCount = searchMode === "GENERATOR_REVIEWER"
      ? 2
      : searchMode === "REPLAY_BRANCH"
        ? 1 + positiveReplayIds.length
        : Number(data.get("branch_count"));
    const directives = searchMode === "HYPOTHESIS_BRANCH"
      ? lines(data.get("candidate_directives"))
      : [];
    setFeedback("Attaching a bounded P6 search plan…");
    try {
      const features = await api.projectFeatures(dynamicTask.envelope.project_id);
      if (!features.candidate_search) {
        await api.updateProjectFeatures(
          dynamicTask.envelope.project_id,
          { dynamicWorkflows: true, candidateSearch: true },
          features.revision,
        );
      }
      const record = await api.createSearch(dynamicProposal.run_id, {
        parent_node_id: String(data.get("parent_node_id")),
        mode: searchMode,
        branch_count: branchCount,
        max_parallel: Math.min(Number(data.get("max_parallel")), branchCount),
        per_branch_budget: {
          schema_version: "2.0",
          wall_time_seconds: Number(data.get("branch_wall")),
          max_turns: Number(data.get("branch_turns")),
          max_tool_calls: Number(data.get("branch_tools")),
        },
        total_budget: {
          schema_version: "2.0",
          wall_time_seconds: Number(data.get("total_wall")),
          max_turns: Number(data.get("total_turns")),
          max_tool_calls: Number(data.get("total_tools")),
        },
        candidate_directives: directives,
        replay_seed_match_ids: searchMode === "REPLAY_BRANCH" ? positiveReplayIds : [],
        negative_guidance_match_ids: searchMode === "REPLAY_BRANCH" ? negativeGuidanceIds : [],
      });
      setSearchRecord(record);
      setFeedback(`P6 ${record.plan.mode} plan attached to ${record.plan.parent_node_id}.`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Search planning failed.");
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

  const eligibleSearchNodes = (dynamicProposal?.nodes ?? []).filter(
    (node) => node.kind === "AGENT" && !(node.capability_refs ?? []).length,
  );
  const taskBudgets = dynamicTask?.envelope.budgets;
  const defaultTotalWall = Math.min(taskBudgets?.wall_time_seconds ?? 240, 240);
  const defaultTotalTurns = Math.min(taskBudgets?.max_turns ?? 8, 8);
  const defaultTotalTools = Math.min(taskBudgets?.max_tool_calls ?? 24, 24);

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
        <section className="experience-planner" aria-label="Verified experience retrieval">
          <header>
            <div><p className="eyebrow">P7 operator selection</p><h3>Verified experience</h3></div>
            <StatePill state={planning.context_bundle.version === "context-bundle-v2" ? "FROZEN" : "OPTIONAL"} />
          </header>
          <p>Retrieve only from this repository, inspect compatibility, then freeze up to three matches before proposing a workflow.</p>
          <div className="experience-actions">
            <button className="secondary-button" type="button" onClick={retrieveExperiences} disabled={planning.context_bundle.version === "context-bundle-v2"}>Retrieve matches</button>
            <button className="primary-button" type="button" onClick={selectExperiences} disabled={!selectedMatchIds.length || planning.context_bundle.version === "context-bundle-v2"}>Freeze {selectedMatchIds.length || "selected"}</button>
          </div>
          <div className="experience-match-grid">
            {experienceMatches.map((match) => {
              const detail = experienceDetails[match.experience_id];
              const eligible = match.assessment.disposition === "ACCEPTED"
                && (match.assessment.replay_eligible || match.assessment.negative_guidance_eligible);
              return (
                <article className={`experience-match experience-${match.assessment.disposition.toLowerCase()}`} key={match.match_id}>
                  <header><label><input type="checkbox" checked={selectedMatchIds.includes(match.match_id)} disabled={!eligible || planning.context_bundle.version === "context-bundle-v2"} onChange={() => toggleExperience(match.match_id)} />Rank {match.rank} · {match.polarity}</label><StatePill state={match.assessment.disposition} /></header>
                  <div className="experience-score-row"><span>{match.assessment.final_score.toFixed(3)} match</span><span>{match.assessment.transfer_risk.toFixed(3)} transfer risk</span><span>{match.trust} trust</span></div>
                  {detail ? <p>{detail.experience.source_kind} {shortId(detail.experience.source_run_id)} · {detail.experience.provider}/{detail.experience.runtime_version} · commit {detail.experience.source_commit.slice(0, 12)}</p> : null}
                  <small>{detail?.segments.map((segment) => segment.kind).join(" → ") ?? "Loading procedural segments…"}</small>
                  {(match.assessment.reasons ?? []).length ? <p className="experience-reasons">{match.assessment.reasons?.join(" · ")}</p> : null}
                </article>
              );
            })}
            {!experienceMatches.length ? <p className="quiet">No experience query has been run for this task.</p> : null}
          </div>
        </section>
        <div className="planning-actions">
          <form className="override-form" onSubmit={override}>
            <label>Override mode<select value={mode} onChange={(event) => setMode(event.target.value as StrategyMode)}><option>DIRECT</option><option>LOOP</option><option>GRAPH</option><option>HYBRID</option></select></label>
            <label>Template<select value={selectedTemplate} onChange={(event) => setTemplateId(event.target.value)}>{modeTemplates.map((template) => <option value={template.template_id} key={template.template_id}>{template.template_id}</option>)}</select></label>
            <label className="reason-field">Reason<input value={reason} onChange={(event) => setReason(event.target.value)} required placeholder="Required for the audit record" /></label>
            <button className="secondary-button" type="submit" disabled={!selectedTemplate}>Request override</button>
          </form>
          <div className="run-control">
            <label>Runtime<select value={provider} onChange={(event) => setProvider(event.target.value)}><option>FAKE</option><option>CODEX</option><option>CLAUDE</option><option>OPENCODE</option></select></label>
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
        {dynamicValidation?.validation.status === "ACCEPT" && eligibleSearchNodes.length ? (
          <form className="search-plan-form" onSubmit={attachSearch} aria-label="P6 search plan">
            <div className="search-plan-heading">
              <div><p className="eyebrow">Optional test-time compute</p><h3>Attach bounded P6 search</h3></div>
              {searchRecord ? <StatePill state={searchRecord.status} /> : null}
            </div>
            <label>Agent node<select name="parent_node_id">{eligibleSearchNodes.map((node) => <option key={node.local_id} value={node.local_id}>{node.local_id} · {node.objective}</option>)}</select></label>
            <label>Mode<select value={searchMode} onChange={(event) => setSearchMode(event.target.value as SearchMode)}><option value="BEST_OF_N">Best of N</option><option value="HYPOTHESIS_BRANCH">Hypothesis branches</option><option value="CROSS_PROVIDER">Cross provider</option><option value="GENERATOR_REVIEWER">Generator + reviewer</option>{planning.context_bundle.version === "context-bundle-v2" ? <option value="REPLAY_BRANCH">Fresh + verified replay</option> : null}</select></label>
            <label>Branches<input name="branch_count" type="number" min="1" max="4" defaultValue="2" disabled={searchMode === "GENERATOR_REVIEWER" || searchMode === "REPLAY_BRANCH"} /></label>
            <label>Parallel<input name="max_parallel" type="number" min="1" max="4" defaultValue={Math.min(taskBudgets?.max_parallel_runs ?? 1, 2)} /></label>
            <label>Branch seconds<input name="branch_wall" type="number" min="1" defaultValue={Math.min(defaultTotalWall, 120)} /></label>
            <label>Branch turns<input name="branch_turns" type="number" min="1" defaultValue={Math.min(defaultTotalTurns, 4)} /></label>
            <label>Branch tools<input name="branch_tools" type="number" min="1" defaultValue={Math.min(defaultTotalTools, 12)} /></label>
            <label>Total seconds<input name="total_wall" type="number" min="1" defaultValue={defaultTotalWall} /></label>
            <label>Total turns<input name="total_turns" type="number" min="1" defaultValue={defaultTotalTurns} /></label>
            <label>Total tools<input name="total_tools" type="number" min="1" defaultValue={defaultTotalTools} /></label>
            {searchMode === "HYPOTHESIS_BRANCH" ? <label className="search-directives">Hypotheses <small>one per branch</small><textarea name="candidate_directives" required rows={3} /></label> : null}
            <button className="secondary-button" type="submit" disabled={Boolean(searchRecord)}>Attach search plan</button>
            <p className="quiet">Candidates receive no protected capabilities. Replay always retains candidate 1 as a fresh control and revalidates every selected seed.</p>
          </form>
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
    const source = new EventSource(api.eventUrl(runId, auditQuery.data.run.last_sequence), { withCredentials: true });
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
      <div
        className="event-list"
        aria-live="polite"
        role="log"
        tabIndex={0}
        aria-label="Normalized event trace"
      >
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
  const now = useNow(activeCount > 0);
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
              <span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events{durationLabel(run, now) ? ` · ${durationLabel(run, now)}` : ""}</small></span>
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
        <header className="section-heading"><div><p className="eyebrow">Create and review</p><h1>New task</h1></div><span>deterministic-profiler-v1</span></header>
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
  const now = useNow(Boolean(selected) && !terminal.has(selected!.state));
  return (
    <div className="inspector-stack page-stack">
      <header className="section-heading"><div><p className="eyebrow">Live run</p><h1>{runId ? shortId(runId) : "Run not selected"}</h1>{durationLabel(selected, now) ? <p className="run-duration">Elapsed {durationLabel(selected, now)}</p> : null}</div>{selected ? <StatePill state={selected.state} /> : null}</header>
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
      <header className="section-heading"><div><p className="eyebrow">Provider health</p><h1>Runtime monitor</h1></div></header>
      <div className="registry-grid">
        {(runtimes.data ?? []).map((runtime) => (
          <article className="registry-card" key={runtime.runtime_id}>
            <header><h2>{runtime.provider}</h2><StatePill state={runtime.status} /></header>
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
  const now = useNow((runs.data ?? []).some((run) => !terminal.has(run.state)));
  const selected = selectedId ?? runs.data?.[0]?.run_id;
  const audit = useQuery({
    queryKey: ["run-audit", selected],
    queryFn: () => api.audit(selected!),
    enabled: Boolean(selected),
  });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Immutable evidence</p><h1>Run history / trace replay</h1></div></header>
      <div className="history-grid">
        <div className="run-list">{(runs.data ?? []).map((run) => <button className={selected === run.run_id ? "run selected" : "run"} key={run.run_id} onClick={() => setSelectedId(run.run_id)}><span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events{durationLabel(run, now) ? ` · ${durationLabel(run, now)}` : ""}</small></span><StatePill state={run.state} /></button>)}</div>
        {audit.data ? (
          <article className="audit-chain">
            <h2>Complete provenance</h2>
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
      <header className="section-heading"><div><p className="eyebrow">Human authority</p><h1>Verifiers / approvals</h1></div></header>
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
      <header className="section-heading"><div><p className="eyebrow">Read-only registry</p><h1>Capabilities, skills, and plugins</h1></div></header>
      <p className="page-status">A capability listed here may still be unusable. <Link to="/admin/capabilities/inspect">Inspect a capability</Link> to see the binding and connection a run would really use.</p>
      <div className="registry-grid">
        <article className="registry-card"><h2>Capabilities</h2><ul className="registry-list">{(capabilities.data ?? []).map((item) => <li key={`${item.capability_id}:${item.version}`}><strong>{item.capability_id}@{item.version}</strong><span>{item.kind} · {item.risk} · {item.backend}</span></li>)}</ul></article>
        <article className="registry-card"><h2>Skills</h2><ul className="registry-list">{(skills.data ?? []).map((item) => <li key={`${item.skill_id}:${item.version}`}><strong>{item.skill_id}@{item.version}</strong><span>{(item.required_capabilities ?? []).join(", ")}</span></li>)}</ul></article>
        <article className="registry-card"><h2>Plugins</h2><ul className="registry-list">{(plugins.data ?? []).map((item) => <li key={`${item.plugin_id}:${item.version}`}><strong>{item.plugin_id}@{item.version}</strong><span>{(item.skill_refs ?? []).join(", ")}</span></li>)}</ul></article>
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
      <header className="section-heading"><div><p className="eyebrow">Architecture evaluation</p><h1>ACR-ARCH</h1></div><button className="primary-button" onClick={replay}>Reproduce replay</button></header>
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
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
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

function SearchBenchmarkPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["search-benchmark"],
    queryFn: api.searchBenchmark,
  });
  const curve = summary.data?.curve ?? [];
  const points = curve.map((point, index) => ({
    ...point,
    x: 64 + index * 190,
    y: 190 - point.mean_quality * 145,
  }));

  async function replay() {
    setStatus("Replaying frozen P6 candidate traces…");
    try {
      const report = await api.runSearchBenchmark();
      setStatus(`Reproduced ${report.task_count} held-out tasks from ${report.trace_sha256.slice(0, 12)}…`);
      await queryClient.invalidateQueries({ queryKey: ["search-benchmark"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Search replay failed.");
    }
  }

  return (
    <section className="page-panel search-benchmark-page">
      <header className="section-heading"><div><p className="eyebrow">P6 research evidence</p><h1>Quality vs compute</h1></div><button className="primary-button" onClick={replay}>Reproduce N=1/2/4</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>held-out tasks</span></div>
        <div><strong>{curve.at(-1)?.acceptance_rate == null ? "—" : `${Math.round(curve.at(-1)!.acceptance_rate * 100)}%`}</strong><span>N=4 accepted</span></div>
        <div><strong>{summary.data?.null_gain_task_ids.length ?? 0}</strong><span>null-gain results</span></div>
        <div><strong>{summary.data?.selector_version ?? "—"}</strong><span>selector</span></div>
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      <div className="search-research-grid">
        <figure className="search-curve">
          <figcaption><strong>Verified quality curve</strong><span>Mean selected quality rises only when a candidate passes independent verification.</span></figcaption>
          <svg viewBox="0 0 520 230" role="img" aria-label="Mean verified quality for one, two, and four candidates">
            <path d="M 44 26 V 194 H 500" className="curve-axis" />
            {[0.25, 0.5, 0.75, 1].map((value) => <g key={value}><path d={`M 44 ${190 - value * 145} H 500`} className="curve-grid" /><text x="8" y={194 - value * 145}>{value.toFixed(2)}</text></g>)}
            <polyline points={points.map((point) => `${point.x},${point.y}`).join(" ")} className="curve-line" />
            {points.map((point) => <g key={point.candidate_count}><circle cx={point.x} cy={point.y} r="6" /><text className="curve-value" x={point.x} y={point.y - 14}>{point.mean_quality.toFixed(3)}</text><text className="curve-label" x={point.x} y="216">N={point.candidate_count}</text></g>)}
          </svg>
        </figure>
        <div className="provider-comparison">
          <h2>Cross-provider replay</h2>
          {(summary.data?.provider_comparison ?? []).map((provider) => <article key={provider.provider}><div><strong>{provider.provider}</strong><span className="provider-rate">{Math.round(provider.acceptance_rate * 100)}% accepted</span></div><span>{provider.mean_best_quality.toFixed(3)}</span><small>mean best eligible quality · {provider.accepted_tasks}/{provider.task_count} tasks accepted</small></article>)}
          <p>Frozen fixture hashes</p>
          <code>{summary.data?.corpus_sha256 ?? "loading corpus…"}</code>
          <code>{summary.data?.trace_sha256 ?? "loading traces…"}</code>
          <code>{summary.data?.config_sha256 ?? "loading config…"}</code>
        </div>
      </div>
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Task</th><th>Family</th><th>N=1</th><th>N=2</th><th>N=4</th><th>N2→N4 gain</th><th>Selected provider</th></tr></thead>
          <tbody>{(summary.data?.tasks ?? []).map((task) => <tr key={task.task_id} className={summary.data?.null_gain_task_ids.includes(task.task_id) ? "null-result" : undefined}><td>{task.task_id} · {task.title}</td><td>{task.family}</td><td>{task.quality_by_candidate_count["1"].toFixed(3)}</td><td>{task.quality_by_candidate_count["2"].toFixed(3)}</td><td>{task.quality_by_candidate_count["4"].toFixed(3)}</td><td>{task.gain_from_two_to_four.toFixed(3)}</td><td>{task.selected_provider_at_four ?? "none"}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}

function DynamicBenchmarkPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["dynamic-benchmark"],
    queryFn: api.dynamicBenchmark,
  });

  async function replay() {
    setStatus("Replaying frozen P5 static and dynamic treatments…");
    try {
      const report = await api.runDynamicBenchmark();
      setStatus(`Reproduced ${report.trace_count} traces; gate ${report.gate.passed ? "passed" : "failed"}.`);
      await queryClient.invalidateQueries({ queryKey: ["dynamic-benchmark"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Dynamic benchmark replay failed.");
    }
  }

  const gate = summary.data?.gate;
  return (
    <section className="page-panel experience-benchmark-page">
      <header className="section-heading"><div><p className="eyebrow">P5 preregistered evidence</p><h1>Dynamic workflow gate</h1></div><button className="primary-button" onClick={replay}>Reproduce static vs dynamic</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>held-out tasks</span></div>
        <div><strong>{summary.data?.trace_count ?? 0}</strong><span>paired traces</span></div>
        <div><strong>{gate?.research_classification ?? "—"}</strong><span>research result</span></div>
        <div><strong>{gate ? (gate.passed ? "PASS" : "FAIL") : "—"}</strong><span>release gate</span></div>
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      {gate ? <div className="experience-gate-grid" aria-label="P5 dynamic workflow gate checks">
        <article><StatePill state={gate.benefit_passed ? "PASS" : "EXPERIMENTAL"} /><strong>Heterogeneous benefit</strong><span>{gate.heterogeneous_uncertain_uplift >= 0 ? "+" : ""}{gate.heterogeneous_uncertain_uplift.toFixed(3)} utility</span></article>
        <article><StatePill state={gate.predictable_non_inferiority_passed ? "PASS" : "FAIL"} /><strong>Predictable cohort</strong><span>{gate.predictable_uplift >= 0 ? "+" : ""}{gate.predictable_uplift.toFixed(3)} utility</span></article>
        <article><StatePill state={gate.safety_invariants_passed ? "PASS" : "FAIL"} /><strong>v0.1 invariants</strong><span>No risk or false-accept regression</span></article>
        <article><StatePill state={gate.static_fallback_operational ? "PASS" : "FAIL"} /><strong>Static fallback</strong><span>Invalid proposals degrade safely</span></article>
      </div> : null}
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Treatment</th><th>Success</th><th>Quality</th><th>Utility</th><th>Turns</th><th>Tools</th><th>Invalid</th><th>Replan</th><th>Human</th><th>Graph nodes/depth</th><th>Variation</th></tr></thead>
          <tbody>{(summary.data?.treatments ?? []).map((treatment) => <tr key={treatment.treatment}><td>{treatment.treatment}</td><td>{Math.round(treatment.success_rate * 100)}%</td><td>{treatment.mean_quality.toFixed(3)}</td><td>{treatment.mean_utility.toFixed(3)}</td><td>{treatment.mean_turns.toFixed(1)}</td><td>{treatment.mean_tool_calls.toFixed(1)}</td><td>{Math.round(treatment.invalid_proposal_rate * 100)}%</td><td>{Math.round(treatment.replan_rate * 100)}%</td><td>{Math.round(treatment.human_intervention_rate * 100)}%</td><td>{treatment.mean_graph_nodes.toFixed(1)} / {treatment.mean_graph_depth.toFixed(1)}</td><td>{Math.round(treatment.structural_variation_rate * 100)}%</td></tr>)}</tbody>
        </table>
      </div>
      <div className="experience-benchmark-evidence">
        <div><h2>Cohort comparison</h2><ul>{(summary.data?.cohorts ?? []).map((cohort) => <li key={cohort.cohort}><strong>{cohort.cohort}</strong> · static {cohort.static_mean_utility.toFixed(3)} · dynamic {cohort.dynamic_mean_utility.toFixed(3)} · uplift {cohort.utility_uplift >= 0 ? "+" : ""}{cohort.utility_uplift.toFixed(3)}</li>)}</ul></div>
        <div><h2>Frozen fixture hashes</h2><code>{summary.data?.corpus_sha256 ?? "loading corpus…"}</code><code>{summary.data?.trace_sha256 ?? "loading traces…"}</code><code>{summary.data?.config_sha256 ?? "loading config…"}</code></div>
      </div>
    </section>
  );
}

function ExperienceBenchmarkPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["experience-benchmark"],
    queryFn: api.experienceBenchmark,
  });

  async function replay() {
    setStatus("Replaying frozen P7 experience treatments…");
    try {
      const report = await api.runExperienceBenchmark();
      setStatus(`Reproduced ${report.trace_count} traces; gate ${report.gate.passed ? "passed" : "failed"}.`);
      await queryClient.invalidateQueries({ queryKey: ["experience-benchmark"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Experience benchmark replay failed.");
    }
  }

  const gate = summary.data?.gate;
  return (
    <section className="page-panel experience-benchmark-page">
      <header className="section-heading"><div><p className="eyebrow">P7 preregistered evidence</p><h1>Experience transfer gate</h1></div><button className="primary-button" onClick={replay}>Reproduce P7 gate</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>held-out tasks</span></div>
        <div><strong>{summary.data?.source_count ?? 0}</strong><span>frozen sources</span></div>
        <div><strong>{summary.data?.trace_count ?? 0}</strong><span>treatment traces</span></div>
        <div><strong>{gate ? (gate.passed ? "PASS" : "FAIL") : "—"}</strong><span>release gate</span></div>
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      {gate ? <div className="experience-gate-grid" aria-label="P7 gate checks">
        <article><StatePill state={gate.false_accepts_not_increased ? "PASS" : "FAIL"} /><strong>False accepts</strong><span>No increase</span></article>
        <article><StatePill state={gate.stale_rejection_passed ? "PASS" : "FAIL"} /><strong>Stale rejection</strong><span>{Math.round(gate.stale_rejection_rate * 100)}%</span></article>
        <article><StatePill state={gate.negative_transfer_passed ? "PASS" : "FAIL"} /><strong>Negative transfer</strong><span>{(gate.negative_transfer_rate * 100).toFixed(2)}%</span></article>
        <article><StatePill state={gate.benefit_passed ? "PASS" : "FAIL"} /><strong>Replay benefit</strong><span>+{gate.replay_quality_uplift.toFixed(3)} quality · {Math.round(gate.replay_tool_call_reduction * 100)}% fewer tools</span></article>
      </div> : null}
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Treatment</th><th>Success</th><th>Quality</th><th>Uplift</th><th>Turns</th><th>Tools</th><th>Tool reduction</th><th>Negative transfer</th><th>False accepts</th><th>Use / reject / null</th></tr></thead>
          <tbody>{(summary.data?.treatments ?? []).map((treatment) => <tr key={treatment.treatment}><td>{treatment.treatment.replaceAll("_", " ")}</td><td>{Math.round(treatment.success_rate * 100)}%</td><td>{treatment.mean_quality.toFixed(3)}</td><td>{treatment.quality_uplift.toFixed(3)}</td><td>{treatment.mean_turns.toFixed(1)}</td><td>{treatment.mean_tool_calls.toFixed(1)}</td><td>{Math.round(treatment.tool_call_reduction * 100)}%</td><td>{treatment.negative_transfers}</td><td>{treatment.false_accepts}</td><td>{Math.round(treatment.experience_use_rate * 100)} / {Math.round(treatment.experience_rejection_rate * 100)} / {Math.round(treatment.experience_null_rate * 100)}%</td></tr>)}</tbody>
        </table>
      </div>
      <div className="experience-benchmark-evidence">
        <div><h2>Reported negative results</h2><ul>{(summary.data?.tasks ?? []).filter((task) => task.negative_transfer_treatments.length).map((task) => <li key={task.task_id}><strong>{task.task_id}</strong> · {task.title} · {task.negative_transfer_treatments.join(", ")}</li>)}</ul></div>
        <div><h2>Frozen fixture hashes</h2><code>{summary.data?.corpus_sha256 ?? "loading corpus…"}</code><code>{summary.data?.source_sha256 ?? "loading sources…"}</code><code>{summary.data?.trace_sha256 ?? "loading traces…"}</code><code>{summary.data?.config_sha256 ?? "loading config…"}</code></div>
      </div>
    </section>
  );
}

const navigation = [
  ["/", "Dashboard"], ["/tasks/new", "New task"], ["/runtimes", "Runtimes"],
  ["/history", "History"], ["/approvals", "Approvals"], ["/capabilities", "Capabilities"],
  ["/admin/connections", "Connections"], ["/admin/plugins", "Plugins"],
  ["/admin/mcp", "MCP servers"], ["/admin/capabilities/inspect", "Capability inspector"],
  ["/admin/identity", "Identity"],
  ["/benchmarks/acr-arch", "ACR-ARCH"], ["/benchmarks/dynamic", "P5 Dynamic"],
  ["/benchmarks/search", "P6 Search"],
  ["/benchmarks/experience", "P7 Experience"],
] as const;

function OperatorShell() {
  const meQuery = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const me = meQuery.data;
  const identity = me?.principal
    ? `${me.principal.display_name ?? me.principal.subject}${me.memberships?.[0] ? " · " + me.memberships[0].role : ""}`
    : "Control plane";
  return (
    <main>
      <nav>
        <Link className="brand-link" to="/"><span className="brand-mark">A</span><span><strong>Accretion</strong><small>Operator / v{__APP_VERSION__}</small></span></Link>
        <div className="nav-links">{navigation.map(([path, label]) => <NavLink end={path === "/"} key={path} to={path}>{label}</NavLink>)}</div>
        <div className="nav-status"><i />{identity}</div>
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
          <Route path="/admin/connections" element={<ConnectionsPage />} />
          <Route path="/admin/plugins" element={<PluginsPage />} />
          <Route path="/admin/mcp" element={<McpServersPage />} />
          <Route path="/admin/capabilities/inspect" element={<CapabilityInspectorPage />} />
          <Route path="/admin/identity" element={<IdentityPage />} />
          <Route path="/benchmarks/acr-arch" element={<BenchmarkPage />} />
          <Route path="/benchmarks/dynamic" element={<DynamicBenchmarkPage />} />
          <Route path="/benchmarks/search" element={<SearchBenchmarkPage />} />
          <Route path="/benchmarks/experience" element={<ExperienceBenchmarkPage />} />
          <Route path="*" element={<section className="page-panel"><h1>Page not found</h1><Link to="/">Return to dashboard</Link></section>} />
        </Routes>
      </div>
    </main>
  );
}

export default function App() {
  return <BrowserRouter><OperatorShell /></BrowserRouter>;
}
