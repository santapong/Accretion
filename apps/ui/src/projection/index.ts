/**
 * The React Flow projection: the canvas, its node markup, its badges and its retry edge.
 *
 * The package boundary is the point of M9d. Everything here renders persisted state and
 * nothing here may read or write it: no module under `src/projection/` imports `../api`,
 * `@tanstack/react-query`, `fetch` or `EventSource`, and `projection.test.ts` fails if one
 * ever does. The data arrives as props from `RunExecution.tsx`, which owns every query the
 * run page makes.
 */
export { LoopBackEdge } from "./LoopBackEdge";
export { NodeBadges, RoutingBadges } from "./NodeBadges";
export { ProjectionCanvas } from "./ProjectionCanvas";
export { ProjectionNodeLabel } from "./ProjectionNodeLabel";
