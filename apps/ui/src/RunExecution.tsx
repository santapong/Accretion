import { useMemo, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "./api";
import { layoutProjection } from "./graphLayout";
import { badgeParts, nodeBadges, type NodeBadge, type NodeBadgeIndex } from "./runBadges";
import type {
  ApprovalRecord,
  CandidateScore,
  CandidateTrajectory,
  ExperienceDetail,
  ExperienceMatch,
  GraphProjection,
  GraphProjectionNode,
  GraphRevisionDiff,
  GraphValidationResult,
  LoopExecution,
  Run,
  RuntimeDecision,
  SearchRecord,
  TrajectorySeed,
  ValidationFinding,
  VerificationResult,
} from "./types";

const terminalStates = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "REQUIRES_HUMAN"]);

function display(value: string) {
  return value.replaceAll("_", " ");
}

function StatusBadge({ state }: { state: string }) {
  return <span className={`pill pill-${state.toLowerCase()}`}>{display(state)}</span>;
}

function isGraphProjection(value: unknown): value is GraphProjection {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<GraphProjection>;
  return typeof candidate.run_id === "string"
    && (candidate.nodes === undefined || Array.isArray(candidate.nodes))
    && (candidate.edges === undefined || Array.isArray(candidate.edges));
}

function LoopBackEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  style,
  label,
  data,
}: EdgeProps) {
  const compact = Boolean((data as { compact?: boolean } | undefined)?.compact);
  const minimumDepth = compact ? 46 : 92;
  const curveDepth = Math.max(minimumDepth, Math.abs(sourceX - targetX) * 0.34);
  const controlY = Math.max(sourceY, targetY) + curveDepth;
  const edgePath = `M ${sourceX} ${sourceY} C ${sourceX + 72} ${controlY}, ${targetX - 72} ${controlY}, ${targetX} ${targetY}`;
  const labelX = (sourceX + targetX) / 2;

  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} className="projection-loop-edge" />
      {label ? (
        <EdgeLabelRenderer>
          <span
            className="projection-edge-label nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${controlY}px)` }}
          >
            {label}
          </span>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const edgeTypes = { loopBack: LoopBackEdge };

/**
 * The provenance badges for one node, in the order the audit recorded them.
 *
 * Rendered both inside the React Flow node (AC3-UI-05, SDD 16.6) and in the
 * `projection-node-summary` list that mirrors the canvas for assistive technology, so
 * the two can never disagree about what the gateway resolved.
 */
function NodeBadges({ badges }: { badges: readonly NodeBadge[] }) {
  return (
    <>
      {badges.map((badge) => (
        <span className="node-badge" key={badge.requestId} data-capability-id={badge.capabilityId}>
          <span className="node-badge-capability">{badge.capabilityId}</span>
          {badgeParts(badge).map(([kind, value]) => (
            <span className="node-badge-part" data-badge-part={kind} key={kind}>
              {kind} {value}
            </span>
          ))}
        </span>
      ))}
    </>
  );
}

function ProjectionNodeLabel(
  { node, badges }: { node: GraphProjectionNode; badges: readonly NodeBadge[] },
) {
  return (
    <div className="projection-node-content">
      <span className="projection-node-kind">{node.kind}</span>
      <strong>{node.label}</strong>
      <span className="projection-node-status"><i />{display(node.status)}</span>
      {node.provider ? <span className="projection-provider">{node.provider}</span> : null}
      {node.iteration != null && node.max_iterations != null ? (
        <span className="iteration-badge">Iteration {node.iteration} / {node.max_iterations}</span>
      ) : null}
      {node.verifier_state ? <StatusBadge state={node.verifier_state} /> : null}
      {node.kind === "GATE" && node.status === "WAITING" ? (
        <span className="gate-waiting-hint">Waiting for approval</span>
      ) : null}
      {badges.length ? (
        <span className="projection-node-badges">
          <NodeBadges badges={badges} />
        </span>
      ) : null}
    </div>
  );
}

function ProjectionCanvas({ projection, badges }: { projection: GraphProjection; badges: NodeBadgeIndex }) {
  const projectionNodes = useMemo(() => projection.nodes ?? [], [projection.nodes]);
  const projectionEdges = useMemo(() => projection.edges ?? [], [projection.edges]);
  const layout = useMemo(() => layoutProjection(projection), [projection]);
  const parentIds = useMemo(
    () => new Set(projectionNodes.map((node) => node.parent_id).filter(Boolean)),
    [projectionNodes],
  );
  const flowNodes = useMemo<Node[]>(() => {
    // React Flow requires subflow parents to precede their children.
    const ordered = [...projectionNodes].sort((left, right) =>
      Number(Boolean(left.parent_id)) - Number(Boolean(right.parent_id)),
    );
    return ordered.map((node) => {
      const geometry = layout[node.node_id] ?? { x: 0, y: 0, width: 168, height: 112 };
      const isGroup = parentIds.has(node.node_id);
      return {
        id: node.node_id,
        position: { x: geometry.x, y: geometry.y },
        parentId: node.parent_id ?? undefined,
        extent: node.parent_id ? ("parent" as const) : undefined,
        initialWidth: geometry.width,
        initialHeight: geometry.height,
        style: isGroup ? { width: geometry.width, height: geometry.height } : undefined,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className: [
          "projection-node",
          `projection-node-${node.status.toLowerCase()}`,
          `projection-node-kind-${node.kind.toLowerCase()}`,
          isGroup ? "projection-node-group" : "",
        ].filter(Boolean).join(" "),
        data: {
          label: <ProjectionNodeLabel node={node} badges={badges.get(node.node_id) ?? []} />,
        },
      };
    });
  }, [projectionNodes, layout, parentIds, badges]);

  const flowEdges = useMemo<Edge[]>(() => {
    const parentByNode = new Map(
      projectionNodes.map((node) => [node.node_id, node.parent_id ?? null]),
    );
    return projectionEdges.map((edge) => {
      const sharedParent = parentByNode.get(edge.source) != null
        && parentByNode.get(edge.source) === parentByNode.get(edge.target);
      return {
        id: edge.edge_id,
        source: edge.source,
        target: edge.target,
        type: edge.kind === "LOOP_BACK" ? "loopBack" : "smoothstep",
        label: edge.label ?? (edge.kind === "LOOP_BACK" ? "retry" : undefined),
        animated: edge.active,
        data: edge.kind === "LOOP_BACK" && sharedParent ? { compact: true } : undefined,
        className: `projection-edge projection-edge-${edge.kind.toLowerCase().replaceAll("_", "-")}`,
        markerEnd: { type: MarkerType.ArrowClosed, color: edge.active ? "#75db91" : "#657069" },
        style: { stroke: edge.active ? "#75db91" : "#657069", strokeWidth: edge.active ? 2 : 1.25 },
      };
    });
  }, [projectionNodes, projectionEdges]);

  const labels = new Map(projectionNodes.map((node) => [node.node_id, node.label]));
  const loopNode = projectionNodes.find((node) => node.iteration != null && node.max_iterations != null);

  return (
    <section className="projection-card" aria-labelledby="projection-heading">
      <header className="projection-heading">
        <div>
          <p className="eyebrow">Read-only topology</p>
          <h3 id="projection-heading">{projection.workflow_template_id}</h3>
        </div>
        <div className="projection-meta">
          {loopNode ? <span className="iteration-badge">Iteration {loopNode.iteration} / {loopNode.max_iterations}</span> : null}
          <span>Graph v{projection.run_graph_version}</span>
        </div>
      </header>
      <div className="projection-flow" aria-label="Execution graph">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          edgeTypes={edgeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.35}
          maxZoom={1.5}
          colorMode="dark"
        >
          <Background color="#3a493f" gap={24} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <ul className="projection-node-summary" aria-label="Projection node states">
        {projectionNodes.map((node) => (
          <li key={node.node_id}>
            <span>{node.label}</span>
            <StatusBadge state={node.status} />
            <NodeBadges badges={badges.get(node.node_id) ?? []} />
          </li>
        ))}
      </ul>
      <ul className="projection-routes" aria-label="Projection routes">
        {projectionEdges.map((edge) => (
          <li
            key={edge.edge_id}
            className={edge.kind === "LOOP_BACK" ? "loop-route" : undefined}
            data-edge-visual={edge.kind === "LOOP_BACK" ? "curved-loop-back" : "standard"}
          >
            <span>{labels.get(edge.source) ?? edge.source} → {labels.get(edge.target) ?? edge.target}</span>
            <strong>{display(edge.kind)}</strong>
            <small>{edge.traversal_count} {edge.traversal_count === 1 ? "traversal" : "traversals"}</small>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PendingApprovals({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<string>();
  const approvalsQuery = useQuery({
    queryKey: ["run-approvals", runId],
    queryFn: () => api.approvals(runId, "PENDING"),
    retry: false,
    refetchInterval: 2000,
  });
  const pending: ApprovalRecord[] = approvalsQuery.data ?? [];
  if (!pending.length) return null;

  async function decide(approvalId: string, decision: "APPROVE" | "DENY") {
    setFeedback("Recording decision…");
    try {
      await api.decideApproval(approvalId, decision);
      setFeedback(decision === "APPROVE" ? "Approved." : "Denied.");
      await queryClient.invalidateQueries({ queryKey: ["run-approvals", runId] });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Decision failed.");
    }
  }

  return (
    <section className="approval-panel" aria-label="Pending approvals">
      <header>
        <p className="eyebrow">Human gate</p>
        <h3>Pending approvals</h3>
      </header>
      {pending.map((approval) => (
        <article className="approval-request" key={approval.approval_id}>
          <div>
            <strong>{approval.summary || approval.native_request_id}</strong>
            <small>{approval.method}</small>
          </div>
          <div className="approval-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() => decide(approval.approval_id, "APPROVE")}
            >
              Approve
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => decide(approval.approval_id, "DENY")}
            >
              Deny
            </button>
          </div>
        </article>
      ))}
      {feedback ? <p className="form-status" role="status">{feedback}</p> : null}
    </section>
  );
}

function BudgetSummary({ loop }: { loop: LoopExecution }) {
  const remaining = loop.state.budget_remaining;
  const budgets = [
    ["Wall time", `${remaining.wall_time_seconds}s`],
    ["Turns", String(remaining.turns)],
    ["Tool calls", String(remaining.tool_calls)],
    ["Iterations", String(remaining.iterations)],
  ];

  return (
    <section className="loop-summary" aria-labelledby="loop-summary-heading">
      <header>
        <div><p className="eyebrow">Bounded execution</p><h3 id="loop-summary-heading">Loop state</h3></div>
        <StatusBadge state={loop.status} />
      </header>
      <div className="loop-metrics">
        <div><span>{loop.state.iteration} / {loop.spec.max_iterations}</span><small>iteration</small></div>
        {budgets.map(([label, value]) => <div key={label}><span>{value}</span><small>{label} left</small></div>)}
      </div>
      <dl className="loop-details">
        <div><dt>Stop reason</dt><dd>{loop.stop_reason ? display(loop.stop_reason) : "Not stopped"}</dd></div>
        <div><dt>Acceptance policy</dt><dd>{loop.acceptance_policy_ref}</dd></div>
        <div><dt>No-progress count</dt><dd>{loop.state.consecutive_no_progress}</dd></div>
        <div><dt>Provider failures</dt><dd>{loop.state.provider_failure_count}</dd></div>
      </dl>
    </section>
  );
}

function VerificationCard({ verification }: { verification: VerificationResult }) {
  const findings = verification.findings ?? [];
  const evidenceRefs = verification.evidence_refs ?? [];
  return (
    <details className="verification-card" open={verification.status !== "PASS"}>
      <summary>
        <span><strong>{verification.verifier_id}</strong><small>{verification.verifier_version}</small></span>
        <StatusBadge state={verification.status} />
      </summary>
      <div className="verification-body">
        <dl>
          <div><dt>Target</dt><dd>{verification.target_ref}</dd></div>
          <div><dt>Duration</dt><dd>{verification.duration_ms} ms</dd></div>
          <div><dt>Score</dt><dd>{verification.score == null ? "—" : verification.score.toFixed(2)}</dd></div>
        </dl>
        <div className="verification-columns">
          <div>
            <h4>Findings</h4>
            {findings.length ? (
              <ul className="finding-list">
                {findings.map((finding, index) => (
                  <li key={`${finding.code}-${finding.fingerprint ?? index}`} className={`finding-${finding.severity.toLowerCase()}`}>
                    <span>{finding.severity}</span>
                    <div><strong>{finding.code}</strong><p>{finding.message}</p>{finding.path ? <small>{finding.path}{finding.line ? `:${finding.line}` : ""}</small> : null}</div>
                  </li>
                ))}
              </ul>
            ) : <p className="quiet">No findings.</p>}
          </div>
          <div>
            <h4>Evidence</h4>
            {evidenceRefs.length ? (
              <ul className="evidence-refs">{evidenceRefs.map((reference) => <li key={reference}><code>{reference}</code></li>)}</ul>
            ) : <p className="quiet">No evidence references.</p>}
          </div>
        </div>
      </div>
    </details>
  );
}

function VerificationPanel({ verifications }: { verifications: VerificationResult[] }) {
  const ordered = [...verifications].sort((left, right) => {
    const rightTime = right.executed_at ? Date.parse(right.executed_at) : 0;
    const leftTime = left.executed_at ? Date.parse(left.executed_at) : 0;
    return rightTime - leftTime;
  });
  const latest = ordered[0];
  return (
    <section className="verification-panel" aria-labelledby="verification-heading">
      <header>
        <div><p className="eyebrow">Acceptance evidence</p><h3 id="verification-heading">Verifications</h3></div>
        {latest ? <StatusBadge state={latest.status} /> : <span className="quiet">Pending</span>}
      </header>
      {ordered.length
        ? ordered.map((verification) => <VerificationCard verification={verification} key={verification.verification_id} />)
        : <p className="empty compact">No verification results yet.</p>}
    </section>
  );
}

function ExperienceCapture({ run }: { run: Run }) {
  const [feedback, setFeedback] = useState<string>();
  const [captured, setCaptured] = useState<ExperienceDetail>();
  const materializable = ["SUCCEEDED", "FAILED", "REQUIRES_HUMAN"].includes(run.state);

  async function materialize() {
    setFeedback("Validating and materializing terminal evidence…");
    try {
      const features = await api.projectFeatures(run.project_id);
      if (!features.experience_retrieval) {
        await api.updateProjectFeatures(
          run.project_id,
          { dynamicWorkflows: true, candidateSearch: true, experienceRetrieval: true },
          features.revision,
        );
      }
      const detail = await api.materializeExperience(run.run_id);
      setCaptured(detail);
      setFeedback("Immutable redacted experience materialized.");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Experience materialization failed.");
    }
  }

  if (!materializable && !captured) return null;
  return (
    <section className="experience-capture" aria-label="Experience materialization">
      <div><p className="eyebrow">P7 explicit capture</p><h3>Terminal experience</h3><p>Only verifier-backed procedural evidence is retained; patches, sessions, credentials, and native payloads are excluded.</p></div>
      <button className="secondary-button" type="button" onClick={materialize} disabled={Boolean(captured)}>Materialize run experience</button>
      {captured ? <small>{captured.experience.trust} {captured.experience.polarity} · {captured.segments.length} controlled segments · {captured.embedding_version}</small> : null}
      {feedback ? <p className="form-status" role="status">{feedback}</p> : null}
    </section>
  );
}

function CandidateCard({
  candidate,
  score,
  selected,
}: {
  candidate: CandidateTrajectory;
  score: CandidateScore | undefined;
  selected: boolean;
}) {
  const spend = candidate.budget_spent;
  const sourceKind = candidate.source_kind ?? "FRESH";
  return (
    <article className={`candidate-card ${selected ? "candidate-selected" : ""}`}>
      <header>
        <div><span>Candidate {String(candidate.ordinal).padStart(2, "0")}</span><strong>{candidate.provider}</strong></div>
        <StatusBadge state={candidate.status} />
      </header>
      <p className="candidate-runtime">{candidate.runtime_id} · {candidate.runtime_model} · {candidate.runtime_version}</p>
      <p className={`candidate-source candidate-source-${sourceKind.toLowerCase()}`}>{sourceKind === "REPLAY" ? "Verified replay treatment" : "Fresh control"}</p>
      {candidate.reviewer_provider ? <p className="candidate-reviewer">Reviewed by {candidate.reviewer_provider}</p> : null}
      <dl>
        <div><dt>Score</dt><dd>{score?.total_score == null ? "—" : score.total_score.toFixed(3)}</dd></div>
        <div><dt>Quality</dt><dd>{score?.quality_score == null ? "—" : score.quality_score.toFixed(3)}</dd></div>
        <div><dt>Cost proxy</dt><dd>{score ? score.cost_proxy.toFixed(3) : "—"}</dd></div>
        <div><dt>Latency proxy</dt><dd>{score ? score.latency_proxy.toFixed(3) : "—"}</dd></div>
        <div><dt>Spend</dt><dd>{spend?.turns ?? 0}t / {spend?.tool_calls ?? 0} tools</dd></div>
        <div><dt>Latency</dt><dd>{candidate.latency_ms} ms</dd></div>
      </dl>
      <p className="candidate-reason">{candidate.terminal_reason ?? score?.explanation ?? "Candidate has not completed."}</p>
      {sourceKind === "REPLAY" ? <p className="candidate-replay-state">Seed {candidate.seed_revalidation_status ?? "PENDING"} · {(candidate.trajectory_segment_refs ?? []).length} procedural segments</p> : null}
      <small>{candidate.trajectory_ref ?? candidate.session_id ?? "trajectory pending"}</small>
    </article>
  );
}

function ExperienceLineage({
  search,
  seeds,
  matches,
  details,
}: {
  search: SearchRecord;
  seeds: TrajectorySeed[];
  matches: Map<string, ExperienceMatch>;
  details: Map<string, ExperienceDetail>;
}) {
  const positiveIds = search.plan.replay_seed_match_ids ?? [];
  const negativeIds = search.plan.negative_guidance_match_ids ?? [];
  const attached = [...positiveIds, ...negativeIds];
  if (search.plan.mode !== "REPLAY_BRANCH") return null;
  return (
    <section className="experience-lineage" aria-label="Experience replay lineage">
      <header><div><p className="eyebrow">P7 explainability</p><h4>Experience replay lineage</h4></div><span>{seeds.length} frozen seed{seeds.length === 1 ? "" : "s"}</span></header>
      <div className="experience-lineage-grid">
        {attached.map((matchId) => {
          const match = matches.get(matchId);
          const detail = match ? details.get(match.experience_id) : undefined;
          const seed = seeds.find((item) => item.match_id === matchId);
          const role = positiveIds.includes(matchId) ? "REPLAY SEED" : "NEGATIVE GUIDANCE";
          return (
            <article key={matchId}>
              <header><strong>{role}</strong><StatusBadge state={match?.assessment.disposition ?? seed?.validation_status ?? "PENDING"} /></header>
              {match ? <div className="experience-score-row"><span>{match.assessment.final_score.toFixed(3)} compatibility</span><span>{match.assessment.transfer_risk.toFixed(3)} transfer risk</span><span>{match.trust} trust</span></div> : null}
              {detail ? <p>{detail.experience.source_kind} {detail.experience.source_run_id} · {detail.experience.provider}/{detail.experience.runtime_version} · commit {detail.experience.source_commit.slice(0, 12)}</p> : null}
              {seed ? <><small>Reused segments: {seed.segment_ids.join(", ")}</small><ul>{(seed.procedural_guidance ?? []).map((item) => <li key={item}>{item}</li>)}</ul><p className="experience-revalidations">Revalidation: {(seed.required_revalidations ?? []).join(" · ")}</p></> : <small>Guidance match; never eligible to seed a branch.</small>}
              {(match?.assessment.reasons ?? []).length ? <p className="experience-reasons">{match?.assessment.reasons?.join(" · ")}</p> : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function SearchTree({ run }: { run: Run }) {
  const queryClient = useQueryClient();
  const active = !terminalStates.has(run.state);
  const searchesQuery = useQuery({
    queryKey: ["run-searches", run.run_id],
    queryFn: () => api.searches(run.run_id),
    retry: false,
    refetchInterval: active ? 2000 : false,
  });
  const searches: SearchRecord[] = Array.isArray(searchesQuery.data)
    ? searchesQuery.data
    : [];
  const candidateQueries = useQueries({
    queries: searches.map((search) => ({
      queryKey: ["search-candidates", search.plan.search_id],
      queryFn: () => api.searchCandidates(search.plan.search_id),
      retry: false,
      refetchInterval: active ? 2000 : false,
    })),
  });
  const scoreQueries = useQueries({
    queries: searches.map((search) => ({
      queryKey: ["search-scores", search.plan.search_id],
      queryFn: () => api.searchScores(search.plan.search_id),
      retry: false,
      refetchInterval: active ? 2000 : false,
    })),
  });
  const seedQueries = useQueries({
    queries: searches.map((search) => ({
      queryKey: ["search-replay-seeds", search.plan.search_id],
      queryFn: () => api.replaySeeds(search.plan.search_id),
      retry: false,
      refetchInterval: active ? 2000 : false,
    })),
  });
  const matchesQuery = useQuery({
    queryKey: ["selected-experience-matches", run.task_id],
    queryFn: () => api.selectedExperienceMatches(run.task_id),
    retry: false,
    refetchInterval: active ? 2500 : false,
  });
  const selectedMatches = Array.isArray(matchesQuery.data) ? matchesQuery.data : [];
  const detailQueries = useQueries({
    queries: selectedMatches.map((match) => ({
      queryKey: ["experience-detail", match.experience_id],
      queryFn: () => api.experience(match.experience_id),
      retry: false,
    })),
  });
  if (!searchesQuery.isLoading && !searches.length) return null;

  async function cancel(searchId: string) {
    await api.cancelSearch(searchId);
    await queryClient.invalidateQueries({ queryKey: ["run-searches", run.run_id] });
  }

  return (
    <section className="search-inspector" aria-label="Candidate search tree">
      <header>
        <div><p className="eyebrow">P6 bounded test-time compute</p><h3>Candidate search tree</h3></div>
        <span>{searches.length} plan{searches.length === 1 ? "" : "s"}</span>
      </header>
      {searchesQuery.isLoading ? <p className="empty compact">Loading candidate lineage…</p> : null}
      {searches.map((search, index) => {
        const candidates = Array.isArray(candidateQueries[index]?.data)
          ? candidateQueries[index].data
          : [];
        const scores = Array.isArray(scoreQueries[index]?.data)
          ? scoreQueries[index].data
          : [];
        const scoresByCandidate = new Map(
          scores.map((score) => [score.candidate_id, score]),
        );
        const seeds = Array.isArray(seedQueries[index]?.data)
          ? seedQueries[index].data
          : [];
        const matchesById = new Map(
          selectedMatches.map((match) => [match.match_id, match]),
        );
        const detailsById = new Map(
          detailQueries.flatMap((query) => query.data ? [[query.data.experience.experience_id, query.data] as const] : []),
        );
        const cancellable = ["PLANNED", "RUNNING", "SELECTING"].includes(search.status);
        return (
          <details className="search-plan-tree" key={search.plan.search_id} open>
            <summary>
              <span><strong>{display(search.plan.mode)}</strong><small>{search.plan.parent_node_id} · r{search.plan.graph_revision}</small></span>
              <StatusBadge state={search.status} />
            </summary>
            <div className="search-plan-meta">
              <span>{search.plan.branch_count} branches / {search.plan.max_parallel} parallel</span>
              <span>{search.budget_spent?.turns ?? 0}/{search.plan.total_budget.max_turns} turns</span>
              <span>{search.budget_spent?.tool_calls ?? 0}/{search.plan.total_budget.max_tool_calls} tools</span>
              <span>{search.stop_reason ? display(search.stop_reason) : "search pending"}</span>
              {cancellable ? <button className="secondary-button" type="button" onClick={() => cancel(search.plan.search_id)}>Cancel search</button> : null}
            </div>
            <ExperienceLineage search={search} seeds={seeds} matches={matchesById} details={detailsById} />
            <div className="candidate-tree">
              {candidates.map((candidate) => (
                <CandidateCard
                  candidate={candidate}
                  key={candidate.candidate_id}
                  score={scoresByCandidate.get(candidate.candidate_id)}
                  selected={search.selected_candidate_id === candidate.candidate_id}
                />
              ))}
              {!candidates.length ? <p className="quiet">Candidates are created when the parent node starts.</p> : null}
            </div>
          </details>
        );
      })}
    </section>
  );
}

const diffSections: readonly (readonly [string, keyof GraphRevisionDiff])[] = [
  ["Added nodes", "added_nodes"],
  ["Removed nodes", "removed_nodes"],
  ["Changed nodes", "changed_nodes"],
  ["Added edges", "added_edges"],
  ["Removed edges", "removed_edges"],
  ["Changed edges", "changed_edges"],
];

/**
 * Every identity the diff carries, not just how many there are.
 *
 * Counts alone cannot tell an operator *which* node the replan dropped, and the edge
 * lists were previously not rendered at all, so a revision that rewired the graph
 * without touching a node read as an empty diff.
 */
function GraphDiffIdentities({ diff }: { diff: GraphRevisionDiff }) {
  return (
    <dl className="graph-diff-identities" role="group" aria-label="Graph revision diff identities">
      {diffSections.map(([label, key]) => {
        const identities = (diff[key] as string[] | undefined) ?? [];
        return (
          <div key={key} data-diff-section={key}>
            <dt>{label}</dt>
            <dd>
              {identities.length
                ? <ul>{identities.map((value) => <li key={value}><code>{value}</code></li>)}</ul>
                : <span className="quiet">none</span>}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/**
 * The routing evidence behind one decision: the ordered fallback chain the router would
 * walk if the selection failed, and every feature it observed while scoring. Values are
 * rendered as the decision recorded them; the decision contract carries no credential
 * field, and this renders nothing the decision does not carry.
 */
function RouterEvidence({ decision }: { decision: RuntimeDecision }) {
  const fallback = decision.fallback_order ?? [];
  const features = Object.entries(decision.observed_features ?? {});
  return (
    <div className="router-evidence">
      <div>
        <h4 id={`fallback-${decision.decision_id}`}>Fallback order</h4>
        {fallback.length ? (
          <ol className="router-fallback" aria-labelledby={`fallback-${decision.decision_id}`}>
            {fallback.map((provider, index) => (
              <li key={provider}><span>{index + 1}</span><strong>{provider}</strong></li>
            ))}
          </ol>
        ) : <p className="quiet">No fallback runtime is available.</p>}
      </div>
      <div>
        <h4 id={`features-${decision.decision_id}`}>Observed features</h4>
        {features.length ? (
          <dl className="router-features" aria-labelledby={`features-${decision.decision_id}`}>
            {features.map(([key, value]) => (
              <div key={key}><dt>{key}</dt><dd>{formatFeature(value)}</dd></div>
            ))}
          </dl>
        ) : <p className="quiet">The router recorded no observed features.</p>}
      </div>
    </div>
  );
}

function formatFeature(value: unknown): string {
  if (value == null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function DynamicWorkflowInspector({ run }: { run: Run }) {
  const queryClient = useQueryClient();
  const [replanEvidence, setReplanEvidence] = useState("");
  const [feedback, setFeedback] = useState<string>();
  const proposals = useQuery({
    queryKey: ["workflow-proposals", run.run_id],
    queryFn: () => api.workflowProposals(run.run_id),
    retry: false,
    refetchInterval: terminalStates.has(run.state) ? false : 2500,
  });
  const proposalItems = Array.isArray(proposals.data) ? proposals.data : [];
  const latestProposal = proposalItems.at(-1);
  const validations = useQuery({
    queryKey: ["workflow-validations", run.run_id, latestProposal?.proposal_id],
    queryFn: () => api.workflowValidations(run.run_id, latestProposal!.proposal_id),
    enabled: Boolean(latestProposal),
    retry: false,
  });
  const revisions = useQuery({
    queryKey: ["graph-revisions", run.run_id],
    queryFn: () => api.graphRevisions(run.run_id),
    retry: false,
    refetchInterval: terminalStates.has(run.state) ? false : 2500,
  });
  const decisions = useQuery({
    queryKey: ["runtime-decisions", run.run_id],
    queryFn: () => api.runtimeDecisions(run.run_id),
    retry: false,
  });
  const replans = useQuery({
    queryKey: ["replans", run.run_id],
    queryFn: () => api.replans(run.run_id),
    retry: false,
  });
  const revisionItems = Array.isArray(revisions.data) ? revisions.data : [];
  const decisionItems = Array.isArray(decisions.data) ? decisions.data : [];
  const replanItems = Array.isArray(replans.data) ? replans.data : [];
  const [selectedRevision, setSelectedRevision] = useState<number>();
  const previousRevision = revisionItems.length > 1
    ? revisionItems[revisionItems.length - 2].revision
    : undefined;
  const activeRevision = revisionItems.at(-1)?.revision;
  const diff = useQuery({
    queryKey: ["graph-revision-diff", run.run_id, previousRevision, activeRevision],
    queryFn: () => api.graphDiff(run.run_id, previousRevision!, activeRevision!),
    enabled: previousRevision != null && activeRevision != null,
    retry: false,
  });
  if (!proposals.isLoading && !latestProposal && !revisionItems.length) return null;

  const validationItems = Array.isArray(validations.data) ? validations.data : [];
  const latestValidation: GraphValidationResult | undefined = validationItems.at(-1);
  const validationFindings: [string, ValidationFinding][] = [
    ...(latestValidation?.errors ?? []).map(
      (finding) => ["finding-error", finding] as [string, ValidationFinding],
    ),
    ...(latestValidation?.warnings ?? []).map(
      (finding) => ["finding-warning", finding] as [string, ValidationFinding],
    ),
  ];
  const inspectedRevision = revisionItems.find(
    (item) => item.revision === selectedRevision,
  ) ?? revisionItems.at(-1);

  async function requestReplan() {
    setFeedback("Pausing at a safe boundary and validating a new revision…");
    try {
      const result = await api.replan(
        run.run_id,
        "HUMAN_REQUEST",
        replanEvidence.trim() ? [replanEvidence.trim()] : [],
      );
      setFeedback(
        result.revision
          ? `Revision ${result.revision.revision} activated with protected history preserved.`
          : `Replan ${result.request.status.toLowerCase().replaceAll("_", " ")}.`,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workflow-proposals", run.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["graph-revisions", run.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["replans", run.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["run-graph", run.run_id] }),
      ]);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Replan failed.");
    }
  }

  return (
    <section className="dynamic-inspector" aria-labelledby="dynamic-workflow-heading">
      <header>
        <div><p className="eyebrow">P5 authority boundary</p><h3 id="dynamic-workflow-heading">Dynamic workflow</h3></div>
        <StatusBadge state={activeRevision ? "ACTIVE" : "PROPOSED"} />
      </header>
      {latestProposal ? (
        <article
          className="proposal-inspector"
          data-proposal-state={activeRevision ? "ACTIVE" : "PROPOSED"}
        >
          <div className="dynamic-metrics">
            <span><strong>{latestProposal.nodes.length}</strong> nodes</span>
            <span><strong>{(latestProposal.edges ?? []).length}</strong> edges</span>
            <span><strong>{Math.round(latestProposal.confidence * 100)}%</strong> confidence</span>
          </div>
          <h4>{(latestProposal.fragment_refs ?? []).join(" · ")}</h4>
          <p>{latestProposal.rationale_summary}</p>
          <p className="proposal-authority">
            {activeRevision
              ? `Executable: activated as graph revision r${activeRevision}.`
              : "Pending proposal: not executable until a graph revision is activated."}
          </p>
          <h5 id="proposal-assumptions-heading">Assumptions</h5>
          {(latestProposal.assumptions ?? []).length ? (
            <ul className="proposal-assumptions" aria-labelledby="proposal-assumptions-heading">
              {(latestProposal.assumptions ?? []).map((assumption) => <li key={assumption}>{assumption}</li>)}
            </ul>
          ) : <p className="quiet">The planner recorded no assumptions.</p>}
          <h5 id="proposal-capabilities-heading">Required capabilities</h5>
          {(latestProposal.required_capabilities ?? []).length ? (
            <ul className="proposal-capabilities" aria-labelledby="proposal-capabilities-heading">
              {(latestProposal.required_capabilities ?? []).map((capability) => (
                <li key={capability}><code>{capability}</code></li>
              ))}
            </ul>
          ) : <p className="quiet">The proposal requires no capabilities.</p>}
          <div className="proposal-validation" role="group" aria-label="Validation findings">
            <h5>Validation</h5>
            <StatusBadge state={latestValidation?.status ?? "PENDING"} />
            {validationFindings.length ? (
              <ul className="finding-list">
                {validationFindings.map(([severityClass, finding]) => (
                  <li className={severityClass} key={`${severityClass}-${finding.code}-${finding.path}`}>
                    <span>{finding.severity}</span>
                    <div><strong>{finding.code}</strong><p>{finding.message}</p></div>
                  </li>
                ))}
              </ul>
            ) : <p className="quiet">The validator reported no findings.</p>}
          </div>
          <small>{latestProposal.planner_version} · {latestValidation?.validator_version ?? "validation pending"}</small>
        </article>
      ) : null}
      <div
        className="revision-timeline"
        role="group"
        aria-label="Graph revision timeline"
        tabIndex={0}
      >
        {revisionItems.map((revision) => (
          <article
            key={revision.revision_id}
            data-revision-role={revision.revision === activeRevision ? "active" : "prior"}
          >
            <button
              type="button"
              className="revision-select"
              aria-pressed={revision.revision === inspectedRevision?.revision}
              onClick={() => setSelectedRevision(revision.revision)}
            >
              r{revision.revision}
            </button>
            <div><strong>{revision.reason.replaceAll("_", " ")}</strong><small>{revision.normalized_graph_hash.slice(0, 12)}…</small></div>
            <small>{(revision.protected_state_refs ?? []).length} protected refs</small>
          </article>
        ))}
        {!revisionItems.length ? <p className="quiet">No graph revision is active.</p> : null}
      </div>
      {inspectedRevision ? (
        <div className="revision-detail" role="group" aria-label="Selected graph revision">
          <strong>r{inspectedRevision.revision}</strong>
          <span>{inspectedRevision.revision === activeRevision ? "active revision" : "prior revision"}</span>
          <code>{inspectedRevision.normalized_graph_hash}</code>
        </div>
      ) : null}
      {diff.data ? (
        <>
          <div className="graph-diff-summary">
            <strong>r{diff.data.from_revision} → r{diff.data.to_revision}</strong>
            <span>+{(diff.data.added_nodes ?? []).length} / −{(diff.data.removed_nodes ?? []).length} nodes</span>
            <span>{(diff.data.changed_nodes ?? []).length} changed</span>
            <span>{(diff.data.protected_state_refs ?? []).length} protected state refs</span>
          </div>
          <GraphDiffIdentities diff={diff.data} />
        </>
      ) : null}
      {decisionItems.map((decision) => (
        <details className="router-decision" key={decision.decision_id}>
          <summary><strong>Runtime: {decision.selected_runtime ?? "none"}</strong><span>{decision.policy_version}</span></summary>
          <p>{decision.selected_reason}</p>
          <ul>{decision.candidates.map((candidate) => <li key={`${candidate.provider}-${candidate.runtime_version}`}><strong>{candidate.provider}</strong><span>{candidate.score.toFixed(3)} · {candidate.available ? "available" : candidate.exclusion_reason}</span></li>)}</ul>
          <RouterEvidence decision={decision} />
        </details>
      ))}
      {replanItems.length ? <p className="quiet">{replanItems.length} durable replan request(s)</p> : null}
      {run.state === "PAUSED" && revisionItems.length ? (
        <div className="replan-control">
          <label>Replan evidence reference<input value={replanEvidence} onChange={(event) => setReplanEvidence(event.target.value)} placeholder="operator:reason-or-evidence-id" /></label>
          <button className="secondary-button" type="button" onClick={requestReplan}>Request safe replan</button>
        </div>
      ) : null}
      {feedback ? <p className="form-status" role="status">{feedback}</p> : null}
    </section>
  );
}

export function RunExecution({ run }: { run: Run | undefined }) {
  const runId = run?.run_id;
  const refetchInterval = run && !terminalStates.has(run.state) ? 2500 : false;
  const loopQuery = useQuery({
    queryKey: ["run-loop", runId],
    queryFn: () => api.loop(runId!),
    enabled: Boolean(runId),
    retry: false,
    refetchInterval,
  });
  const graphQuery = useQuery({
    queryKey: ["run-graph", runId],
    queryFn: () => api.graph(runId!),
    enabled: Boolean(runId),
    retry: false,
    refetchInterval,
  });
  const verificationQuery = useQuery({
    queryKey: ["run-verifications", runId],
    queryFn: () => api.verifications(runId!),
    enabled: Boolean(runId),
    retry: false,
    refetchInterval,
  });
  // Read-only capability provenance for the graph badges. Recomputed from the query
  // result on every render on purpose: a badge must never outlive the audit row it
  // projects, so there is no cached copy to go stale.
  const auditQuery = useQuery({
    queryKey: ["run-audit-badges", runId],
    queryFn: () => api.audit(runId!),
    enabled: Boolean(runId),
    retry: false,
    refetchInterval,
  });

  if (!run) return <section className="execution-panel empty">Select a run to inspect its orchestration state.</section>;

  const projection = isGraphProjection(graphQuery.data) ? graphQuery.data : undefined;
  const verifications = Array.isArray(verificationQuery.data) ? verificationQuery.data : [];
  const badges = nodeBadges(auditQuery.data);

  return (
    <section className="execution-panel" aria-label="Run orchestration">
      <header className="panel-header">
        <div><p className="eyebrow">Execution control</p><h2>Execution &amp; verification</h2></div>
        <StatusBadge state={run.state} />
      </header>
      <div className="execution-content">
        <ExperienceCapture run={run} />
        <DynamicWorkflowInspector run={run} />
        <SearchTree run={run} />
        <PendingApprovals runId={run.run_id} />
        {loopQuery.data ? <BudgetSummary loop={loopQuery.data} /> : null}
        {projection ? <ProjectionCanvas projection={projection} badges={badges} /> : (
          <div className="projection-unavailable">
            <strong>{graphQuery.isPending ? "Loading execution graph…" : "No graph projection is available for this run"}</strong>
            <p>Runs created before graph persistence keep their normalized trace below.</p>
          </div>
        )}
        <VerificationPanel verifications={verifications} />
      </div>
    </section>
  );
}
