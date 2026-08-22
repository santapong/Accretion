# Accretion

Accretion is a local-first control plane for observing and supervising Codex and
Claude Code from one browser dashboard.

It discovers existing agent history, keeps normalized transcripts in a local
SQLite database, and gives managed sessions a common set of controls: follow-up
input, interruption, resume, and interactive permission decisions.

## v0.1 capabilities

- Codex integration over the official App Server `stdio` JSON-RPC protocol.
- Claude Code integration through the Python Agent SDK.
- Read-only import of existing Codex and Claude session history.
- Managed session creation, streaming timelines, steering, interruption, and resume.
- Command, file-change, and tool approval decisions in the dashboard.
- Persistent local session, event, and approval history with deletion controls.
- FastAPI REST/WebSocket backend and responsive React frontend.
- Linux-first setup with no cloud service or Accretion account.

## Requirements

- Linux
- Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/)
- Node.js 22 or newer with Corepack
- At least one authenticated provider CLI:
  - [`codex`](https://learn.chatgpt.com/docs/codex-cli)
  - [`claude`](https://docs.anthropic.com/en/docs/claude-code/cli-usage)

## Quick start

```bash
uv sync --all-groups
corepack pnpm --dir frontend install --frozen-lockfile
corepack pnpm --dir frontend build
uv run accretion
```

Open <http://127.0.0.1:8787>. Accretion binds to loopback only by default.

For a development server with backend and frontend reload enabled:

```bash
./scripts/dev.sh
```

## Configuration

Settings use the `ACCRETION_` prefix and can be placed in `.env`.

```dotenv
ACCRETION_HOST=127.0.0.1
ACCRETION_PORT=8787
ACCRETION_DATA_DIR=/home/me/.local/share/accretion
ACCRETION_WORKSPACE_ROOTS='["/home/me/projects","/mnt/data/company"]'
ACCRETION_CODEX_COMMAND=codex
```

Only existing directories beneath `ACCRETION_WORKSPACE_ROOTS` can be used to
start a managed session. By default, the current user's home and the directory
where Accretion was launched are allowed.

## Development

```bash
# Backend quality gates
uv run ruff format --check backend
uv run ruff check backend
uv run mypy backend/src
uv run pytest
# Optional: inspect real installed CLIs and local history (no agent turn is started)
ACCRETION_RUN_LIVE_TESTS=1 uv run pytest -m live

# Frontend quality gates
corepack pnpm --dir frontend typecheck
corepack pnpm --dir frontend test
corepack pnpm --dir frontend build
```

The REST API is documented at <http://127.0.0.1:8787/docs> while the backend is
running. See [Architecture](docs/ARCHITECTURE.md), [Contributing](CONTRIBUTING.md),
and the [v0.1 roadmap](ROADMAP.md) for more detail.

## Security and privacy

Accretion runs with the permissions of the local user and does not store provider
credentials or complete process environments. It does persist agent output and
tool activity, which may contain sensitive project content. Keep the data directory
private and use the per-session or clear-history controls when retention is not
appropriate.

v0.1 is intentionally single-user and loopback-only. Do not expose it to a network.

## License

[MIT](LICENSE)
