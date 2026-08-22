import type { SessionStatus } from "../types";

const labels: Record<SessionStatus, string> = {
  running: "Running",
  waiting_approval: "Needs approval",
  completed: "Completed",
  interrupted: "Interrupted",
  failed: "Failed",
  offline: "Offline",
};

export function StatusBadge({ status }: { status: SessionStatus }) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" />
      {labels[status]}
    </span>
  );
}
