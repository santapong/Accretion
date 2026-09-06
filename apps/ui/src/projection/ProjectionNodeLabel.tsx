import { StatePill } from "../StatePill";
import type { GraphProjectionNode } from "../types";
import type { NodeBadge } from "../runBadges";
import type { RoutingBadge } from "../routingBadges";
import { NodeBadges, RoutingBadges } from "./NodeBadges";

/**
 * One node's markup, rendered inside the box React Flow positions.
 *
 * Moved here from `RunExecution.tsx` by M9d unchanged, with two exceptions that produce the
 * same DOM: the state pill is `StatePill`, which renders the `pill pill-<state>` span the
 * private `StatusBadge` in `RunExecution.tsx` renders byte for byte, and the routing badge
 * is added beside the capability badges in the strip that already exists for them.
 */
export function ProjectionNodeLabel({
  node,
  badges,
  routing,
}: {
  node: GraphProjectionNode;
  badges: readonly NodeBadge[];
  routing: RoutingBadge | undefined;
}) {
  return (
    <div className="projection-node-content">
      <span className="projection-node-kind">{node.kind}</span>
      <strong>{node.label}</strong>
      <span className="projection-node-status"><i />{node.status.replaceAll("_", " ")}</span>
      {node.provider ? <span className="projection-provider">{node.provider}</span> : null}
      {node.iteration != null && node.max_iterations != null ? (
        <span className="iteration-badge">Iteration {node.iteration} / {node.max_iterations}</span>
      ) : null}
      {node.verifier_state ? <StatePill state={node.verifier_state} /> : null}
      {node.kind === "GATE" && node.status === "WAITING" ? (
        <span className="gate-waiting-hint">Waiting for approval</span>
      ) : null}
      {badges.length || routing ? (
        <span className="projection-node-badges">
          <NodeBadges badges={badges} />
          <RoutingBadges badge={routing} />
        </span>
      ) : null}
    </div>
  );
}
