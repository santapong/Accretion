import { Check, CircleAlert, FileCode2, MessageSquareText, TerminalSquare } from "lucide-react";
import type { TimelineEvent } from "../types";

function textFrom(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const text = value
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "text" in item) return String(item.text);
        return "";
      })
      .filter(Boolean)
      .join("\n");
    return text || null;
  }
  return null;
}

function eventText(event: TimelineEvent): string | null {
  const payload = event.payload;
  return (
    textFrom(payload.delta) ??
    textFrom(payload.text) ??
    textFrom(payload.message) ??
    textFrom((payload.message as Record<string, unknown> | undefined)?.content) ??
    textFrom((payload.item as Record<string, unknown> | undefined)?.text)
  );
}

function eventMeta(event: TimelineEvent) {
  const payload = event.payload;
  const item = payload.item as Record<string, unknown> | undefined;
  const command = textFrom(item?.command) ?? textFrom(payload.command);
  const changes = item?.changes ?? payload.changes;
  if (command) return { icon: TerminalSquare, label: "Command", content: command };
  if (changes) {
    return {
      icon: FileCode2,
      label: "File change",
      content: JSON.stringify(changes, null, 2),
    };
  }
  if (event.kind === "error") {
    return { icon: CircleAlert, label: "Error", content: eventText(event) ?? "Provider error" };
  }
  if (event.kind === "completed" || event.kind === "interrupted") {
    return {
      icon: Check,
      label: event.kind === "completed" ? "Turn completed" : "Turn interrupted",
      content: null,
    };
  }
  const text = eventText(event);
  if (text) return { icon: MessageSquareText, label: "Agent", content: text };
  return null;
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const visible = events.map((event) => ({ event, meta: eventMeta(event) })).filter((row) => row.meta);
  if (!visible.length) {
    return (
      <div className="empty-timeline">
        <span className="orbit orbit--small" />
        <h3>Waiting for signal</h3>
        <p>Agent messages, commands, and file changes will stream into this timeline.</p>
      </div>
    );
  }
  return (
    <div className="timeline">
      {visible.map(({ event, meta }) => {
        if (!meta) return null;
        const Icon = meta.icon;
        return (
          <article className={`timeline-event timeline-event--${event.kind}`} key={event.id ?? event.created_at}>
            <div className="timeline-event__rail">
              <span className="timeline-event__icon"><Icon size={15} /></span>
            </div>
            <div className="timeline-event__body">
              <div className="timeline-event__meta">
                <strong>{meta.label}</strong>
                <time>{new Date(event.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
              </div>
              {meta.content && <pre>{meta.content}</pre>}
              {event.kind === "item" && (
                <details>
                  <summary>Raw provider event</summary>
                  <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                </details>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}
