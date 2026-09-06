import type { RouterModelVersion } from "./types";

/**
 * The workspace's shadow stages that could have scored one run, newest first.
 *
 * `list_router_model_versions` orders by `(created_at, contract_id)`, and the stage an
 * operator opening a run wants is the one currently accumulating evidence. A version scoped
 * to another project is dropped here rather than passed to the route as `project_id`,
 * because that parameter would also drop the workspace-scoped versions — the ones whose
 * `project_id` is null — and those score every project's runs.
 *
 * Exported because `RunExecution.tsx` asks the same question when it decides which cached
 * report the canvas's `shadowed` badge is read from. One rule, in one place: two spellings of
 * "which stage is this run being compared against" would let the canvas and the panel below
 * it disagree, and neither would be wrong on its own terms.
 */
export function shadowStages(
  versions: readonly RouterModelVersion[] | undefined,
  projectId: string | null | undefined,
): readonly RouterModelVersion[] {
  return (Array.isArray(versions) ? versions : [])
    .filter(
      (version) =>
        version.status === "SHADOW" && (!version.project_id || version.project_id === projectId),
    )
    .reverse();
}
