# Accretion

Accretion is a local-first, observable meta-harness for supervising Codex and
Claude Code through a provider-neutral control plane. The current implementation
targets the v0.1 P0 runtime-feasibility milestone.

## What is included

- FastAPI control plane with durable PostgreSQL state and append-only run events
- provider-neutral runtime contracts and a deterministic fake runtime
- structured Codex App Server and Claude Code adapters
- one Git worktree lease per mutable run
- resumable Server-Sent Events (SSE)
- a minimal React operator dashboard
- versioned Accretion v0.1-v0.3 system design specifications

Dynamic workflow generation, learned routing, full verification, managed MCP,
plugins, and identity are intentionally deferred to later milestones.

## Development

Prerequisites: Python 3.12, `uv`, Node.js 22+, npm, Git, and Docker Compose.

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --all-groups
npm install
uv run alembic upgrade head
uv run uvicorn accretion.api.main:app --reload
npm run dev --workspace @accretion/ui
```

The API runs at `http://localhost:8000`; the UI runs at
`http://localhost:5173`.

## Checks

```bash
uv run ruff check .
uv run mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
npm run check
npm run api:generate
```

Live provider tests are opt-in because they use signed-in provider sessions:

```bash
ACCRETION_LIVE_PROVIDERS=1 uv run pytest -m live
```

See [the P0 runbook](docs/P0_RUNBOOK.md) for runtime compatibility and acceptance
instructions.

## License

MIT
