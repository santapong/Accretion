# Contributing

## Setup

1. Install Python 3.12+, `uv`, Node.js 22+, and Corepack.
2. Run `uv sync --all-groups`.
3. Run `corepack pnpm --dir frontend install --frozen-lockfile`.
4. Copy `.env.example` to `.env` if the defaults do not fit your machine.
5. Run `./scripts/dev.sh`.

## Design rules

- Keep provider-specific payloads inside adapters; expose normalized types to the UI.
- Treat imported external sessions as read-only until a provider resume succeeds.
- Persist state before publishing its WebSocket notification.
- Never log or store credentials or complete process environments.
- Keep the HTTP service loopback-only until an authenticated remote-access design lands.
- Add deterministic fake-provider tests for every lifecycle or protocol change.

## Pull-request checks

Run `./scripts/check.sh`. It performs backend formatting/linting, strict typing,
tests, frontend type checking, frontend tests, and a production build.
