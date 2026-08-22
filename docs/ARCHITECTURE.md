# Architecture

Accretion is a single-user local web application. FastAPI owns provider processes,
normalizes their events, and serves both the API and compiled React application.

```text
React dashboard
    │ REST + WebSocket
FastAPI service ─── SQLite event/history store
    ├── Codex adapter ─── codex app-server (stdio JSONL)
    └── Claude adapter ── Claude Agent SDK / Claude Code CLI
```

## Session boundary

Accretion has full control only over provider sessions connected through its own
adapter instances. Historical sessions created elsewhere are imported as
`managed = false`. Resuming one establishes a new managed connection before any
follow-up, interruption, or approval action is enabled.

An active terminal process owned by another application is not attached or sent
operating-system signals. In Accretion, interrupt means cancel the provider's active
turn; resume means continue its persisted conversation.

## Backend

`AccretionService` is the application boundary. It validates workspaces, coordinates
provider adapters, persists state, translates provider events into lifecycle changes,
and publishes WebSocket envelopes.

The provider interface supports:

- health and capability discovery;
- historical session discovery;
- start and resume;
- follow-up input and active-turn interruption;
- pending approval resolution;
- best-effort shutdown.

Codex uses App Server's stable `stdio` transport rather than its experimental
WebSocket transport. The adapter initializes once, correlates JSON-RPC requests,
maps thread IDs to local session IDs, and handles server-initiated approval requests.
The protocol is described in the
[official OpenAI App Server documentation](https://learn.chatgpt.com/docs/app-server).

Claude uses `ClaudeSDKClient` for multi-turn conversations and permission callbacks.
Existing history is discovered from Claude Code's local project session files because
the SDK does not expose a cross-project history-list operation.

## Persistence

SQLite uses three tables: `sessions`, append-only `events`, and `approvals`. Provider
event IDs are unique within a session so reconnect duplicates are ignored. Active
sessions are marked offline on backend restart until explicitly resumed.

No provider credentials or full environment snapshots are persisted. Timeline payloads
are retained verbatim enough to support audit and replay in the UI.

## API and events

All REST routes are versioned below `/api/v1`. The `/api/v1/events` WebSocket first
sends a complete session snapshot and then monotonically sequenced event envelopes.
Clients reconnect and refresh authoritative REST state rather than treating the socket
as the sole database.

The backend serves `frontend/dist` for same-origin operation. Network exposure and
authentication are deliberately outside v0.1; the default host is `127.0.0.1`.
