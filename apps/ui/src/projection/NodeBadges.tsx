import { badgeParts, type NodeBadge } from "../runBadges";
import { routingBadgeParts, type RoutingBadge } from "../routingBadges";

/**
 * The provenance badges for one node, in the order the audit recorded them.
 *
 * Rendered both inside the React Flow node (AC3-UI-05, SDD 16.6) and in the
 * `projection-node-summary` list that mirrors the canvas for assistive technology, so
 * the two can never disagree about what the gateway resolved.
 */
export function NodeBadges({ badges }: { badges: readonly NodeBadge[] }) {
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

/**
 * The §17.1 routing badge for one node: what ran, and under which decision.
 *
 * Reuses the capability badge's markup rather than introducing classes of its own. That is
 * ADR4-M9-003's rule and it is not only a CSS-budget argument: the two badges say the same
 * KIND of thing — this is what the platform resolved for this node, read back from a sealed
 * record — and rendering them differently would suggest one of them is a control.
 *
 * Nothing here is focusable and nothing here is a button. `AC4-M9-043` is the claim that the
 * canvas stays a projection, and a badge that could be clicked would be the first place that
 * stopped being true.
 */
export function RoutingBadges({ badge }: { badge: RoutingBadge | undefined }) {
  if (!badge) return null;
  return (
    <span
      className="node-badge"
      data-routing-decision={badge.decision}
      data-receipt-id={badge.receiptId}
    >
      <span className="node-badge-capability">{badge.decision.replaceAll("_", " ")}</span>
      {routingBadgeParts(badge).map(([kind, value]) => (
        <span className="node-badge-part" data-badge-part={kind} key={kind}>
          {kind} {value}
        </span>
      ))}
    </span>
  );
}
