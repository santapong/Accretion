import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { shortId } from "../runState";
import { StatePill } from "../StatePill";
import { lines } from "./formLines";
import type {
  ExperienceDetail,
  ExperienceMatch,
  Provider,
  SearchMode,
  SearchRecord,
  Task,
  TaskPlanning,
  WorkflowProposal,
  WorkflowValidationOutcome,
} from "../types";

type StrategyMode = "DIRECT" | "LOOP" | "GRAPH" | "HYBRID";

function Score({ label, value }: { label: string; value: number | null | undefined }) {
  return <div className="score"><span>{value == null ? "UNKNOWN" : value.toFixed(2)}</span><p>{label}</p></div>;
}

export function PlanningReview({ planning, onUpdate }: {
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
