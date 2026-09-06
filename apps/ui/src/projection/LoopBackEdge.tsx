import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from "@xyflow/react";

/**
 * The curved retry edge, and the label React Flow portals into the viewport for it.
 *
 * Moved here from `RunExecution.tsx` by M9d, byte for byte: the geometry, the class names
 * and the label placement are the ones `RunExecution.test.tsx` has pinned since v0.1 P4,
 * and a projection that renders differently after a move would be a rendering change
 * dressed up as a refactor.
 */
export function LoopBackEdge({
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
