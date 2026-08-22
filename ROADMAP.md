# Accretion roadmap

## v0.1 — Local orbit

The first release proves that Codex and Claude Code can be observed and
controlled through one provider-neutral, local interface.

### Implemented

- [x] FastAPI, React, TypeScript, SQLite project foundation.
- [x] Normalized provider, session, timeline event, and approval models.
- [x] Codex App Server `stdio` adapter with history, turn, event, and approval support.
- [x] Claude Agent SDK adapter with history, streaming, interruption, and permissions.
- [x] Imported read-only history and managed-session resume boundary.
- [x] REST API and reconnecting WebSocket event stream.
- [x] Dashboard, timeline, provider health, session controls, and approval UI.
- [x] Local persistence, workspace-root validation, and history deletion.
- [x] Unit, API, provider-contract, and frontend component tests.
- [x] Linux setup, CI, architecture, and contributor documentation.

### Release gate

- [ ] Complete one real managed Codex run, including an approval and interruption.
- [ ] Complete one real managed Claude run, including an approval and interruption.
- [ ] Perform visual QA in a connected browser at desktop and mobile widths.
- [ ] Create the GitHub `v0.1` milestone and mirror the release-gate items as issues.
- [ ] Publish release notes and create signed tag `v0.1.0`.

## After v0.1

- macOS and Windows support.
- Authenticated remote access and multi-user operation.
- Native desktop packaging and system-tray notifications.
- Saved views, full-text search, usage/cost summaries, and export.
- Provider plugin SDK and additional agent runtimes.
- Reliable attachment to externally owned live processes where providers expose it.
