import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { EventStream } from "../EventStream";
import { RunExecution } from "../RunExecution";
import { durationLabel } from "../runDuration";
import { shortId, terminal, useNow } from "../runState";
import { StatePill } from "../StatePill";

export function LiveRunPage() {
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
