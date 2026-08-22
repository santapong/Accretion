#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

cleanup() {
  jobs -pr | xargs -r kill
}
trap cleanup EXIT INT TERM

uv run uvicorn accretion.api:create_app --factory --reload --host 127.0.0.1 --port 8787 &
corepack pnpm --dir frontend dev &
wait
