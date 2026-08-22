import { render, screen } from "@testing-library/react";
import { StatusBadge } from "../src/components/StatusBadge";
import { Timeline } from "../src/components/Timeline";

describe("session UI", () => {
  it("renders an approval status", () => {
    render(<StatusBadge status="waiting_approval" />);
    expect(screen.getByText("Needs approval")).toBeInTheDocument();
  });

  it("renders timeline text", () => {
    render(
      <Timeline
        events={[
          {
            id: 1,
            session_id: "one",
            kind: "message",
            payload: { text: "Repository inspection complete" },
            provider_event_id: null,
            created_at: "2026-08-22T12:00:00Z",
          },
        ]}
      />,
    );
    expect(screen.getByText("Repository inspection complete")).toBeInTheDocument();
  });
});
