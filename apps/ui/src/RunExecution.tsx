import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import type {
  ApprovalRecord,
  GraphProjection,
  GraphProjectionNode,
  GraphValidationResult,
  LoopExecution,
  Run,
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

function ProjectionNodeLabel({ node }: { node: GraphProjectionNode }) {
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
    </div>
  );
}

function ProjectionCanvas({ projection }: { projection: GraphProjection }) {
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
        data: { label: <ProjectionNodeLabel node={node} /> },
      };
    });
  }, [projectionNodes, layout, parentIds]);

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
        <StatusBadge state={latestValidation?.status ?? (activeRevision ? "ACTIVE" : "PROPOSED")} />
      </header>
      {latestProposal ? (
        <article className="proposal-inspector">
          <div className="dynamic-metrics">
            <span><strong>{latestProposal.nodes.length}</strong> nodes</span>
            <span><strong>{(latestProposal.edges ?? []).length}</strong> edges</span>
            <span><strong>{Math.round(latestProposal.confidence * 100)}%</strong> confidence</span>
          </div>
          <h4>{(latestProposal.fragment_refs ?? []).join(" · ")}</h4>
          <p>{latestProposal.rationale_summary}</p>
          <ul>{(latestProposal.assumptions ?? []).map((assumption) => <li key={assumption}>{assumption}</li>)}</ul>
          {(latestValidation?.errors ?? []).length ? (
            <ul className="finding-list">
              {(latestValidation?.errors ?? []).map((finding) => (
                <li className="finding-error" key={`${finding.code}-${finding.path}`}>
                  <span>{finding.severity}</span><div><strong>{finding.code}</strong><p>{finding.message}</p></div>
                </li>
              ))}
            </ul>
          ) : null}
          <small>{latestProposal.planner_version} · {latestValidation?.validator_version ?? "validation pending"}</small>
        </article>
      ) : null}
      <div className="revision-timeline" aria-label="Graph revision timeline">
        {revisionItems.map((revision) => (
          <article key={revision.revision_id}>
            <span>r{revision.revision}</span>
            <div><strong>{revision.reason.replaceAll("_", " ")}</strong><small>{revision.normalized_graph_hash.slice(0, 12)}…</small></div>
            <small>{(revision.protected_state_refs ?? []).length} protected refs</small>
          </article>
        ))}
        {!revisionItems.length ? <p className="quiet">No graph revision is active.</p> : null}
      </div>
      {diff.data ? (
        <div className="graph-diff-summary">
          <strong>r{diff.data.from_revision} → r{diff.data.to_revision}</strong>
          <span>+{(diff.data.added_nodes ?? []).length} / −{(diff.data.removed_nodes ?? []).length} nodes</span>
          <span>{(diff.data.changed_nodes ?? []).length} changed</span>
          <span>{(diff.data.protected_state_refs ?? []).length} protected state refs</span>
        </div>
      ) : null}
      {decisionItems.map((decision) => (
        <details className="router-decision" key={decision.decision_id}>
          <summary><strong>Runtime: {decision.selected_runtime ?? "none"}</strong><span>{decision.policy_version}</span></summary>
          <p>{decision.selected_reason}</p>
          <ul>{decision.candidates.map((candidate) => <li key={`${candidate.provider}-${candidate.runtime_version}`}><strong>{candidate.provider}</strong><span>{candidate.score.toFixed(3)} · {candidate.available ? "available" : candidate.exclusion_reason}</span></li>)}</ul>
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

  if (!run) return <section className="execution-panel empty">Select a run to inspect its orchestration state.</section>;

  const projection = isGraphProjection(graphQuery.data) ? graphQuery.data : undefined;
  const verifications = Array.isArray(verificationQuery.data) ? verificationQuery.data : [];

  return (
    <section className="execution-panel" aria-label="Run orchestration">
      <header className="panel-header">
        <div><p className="eyebrow">Execution control</p><h2>Execution &amp; verification</h2></div>
        <StatusBadge state={run.state} />
      </header>
      <div className="execution-content">
        <DynamicWorkflowInspector run={run} />
        <PendingApprovals runId={run.run_id} />
        {loopQuery.data ? <BudgetSummary loop={loopQuery.data} /> : null}
        {projection ? <ProjectionCanvas projection={projection} /> : (
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
