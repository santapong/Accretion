import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { shortId } from "../runState";
import { StatePill } from "../StatePill";

export function ApprovalsPage() {
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
