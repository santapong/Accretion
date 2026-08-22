import { ShieldAlert } from "lucide-react";
import type { Approval, ApprovalDecision } from "../types";

interface Props {
  approval: Approval;
  onDecision: (decision: ApprovalDecision) => Promise<void>;
}

export function ApprovalCard({ approval, onDecision }: Props) {
  const command = approval.payload.command;
  const input = approval.payload.input;
  return (
    <aside className="approval-card">
      <div className="approval-card__heading">
        <span><ShieldAlert size={17} /></span>
        <div>
          <p className="eyebrow">Permission requested</p>
          <h3>{approval.kind.replaceAll("/", " / ")}</h3>
        </div>
      </div>
      {typeof command === "string" && <pre>{command}</pre>}
      {Boolean(input) && <pre>{JSON.stringify(input, null, 2)}</pre>}
      <div className="approval-card__actions">
        <button className="button button--danger-ghost" onClick={() => onDecision("deny")}>Deny</button>
        <button className="button button--ghost" onClick={() => onDecision("cancel")}>Cancel turn</button>
        <button className="button button--primary" onClick={() => onDecision("approve")}>Approve once</button>
        <button className="button button--warm" onClick={() => onDecision("approve_session")}>Approve session</button>
      </div>
    </aside>
  );
}
