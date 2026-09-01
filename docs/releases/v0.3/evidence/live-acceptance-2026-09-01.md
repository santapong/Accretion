# Live acceptance evidence — 2026-09-01

Produced by `scripts/live_acceptance.py` against signed-in vendor CLIs.
These three criteria cannot run in CI (`ACCRETION_LIVE_PROVIDERS` is never
set there), so they are recorded as `manual` in
`docs/acceptance/criteria.toml` and this file is the evidence those records
point at. A `manual` record goes stale after 180 days: re-run this script and
update `last_verified` before then.

## Run

- Started: `2026-09-01T02:43:52.744305+00:00`
- Repository commit: `e963e5981fc980615d8689b0466953403a68a38d`
- Codex CLI: `codex-cli 0.148.0`
- Claude CLI: `2.1.252 (Claude Code)`
- Host: `Linux 7.1.5+kali-amd64`

## Results

| Criterion | Obligation | Result | Claim |
| --- | --- | --- | --- |
| `V01-P0-002` | MUST | **PASS** | Codex App Server carries at least two independent threads |
| `V01-P0-004` | MUST | **PASS** | Claude and Codex run concurrently in separate worktrees |
| `V01-P4-008` | SHOULD | **PASS** | A Claude-produced artifact is independently verified by Codex |

### V01-P0-002 — Codex App Server carries at least two independent threads

Result: **PASS**

- health: READY, runtime_version=codex-cli 0.148.0, auth_mode=SUBSCRIPTION
- thread 1 native_run_id=01a05ada-06de-77e0-aed6-4dd4c99da0a8, 18 events, terminal=RUNTIME_CALL_COMPLETED
- thread 2 native_run_id=01a05ada-06de-77e0-aed6-4dbe52acca57, 14 events, terminal=RUNTIME_CALL_COMPLETED
- sessions are distinct: True

### V01-P0-004 — Claude and Codex run concurrently in separate worktrees

Result: **PASS**

- codex workspace: concurrent-codex
- claude workspace: concurrent-claude
- workspaces are disjoint: True
- codex terminal=RUNTIME_CALL_COMPLETED, claude terminal=RUNTIME_CALL_COMPLETED, both dispatched concurrently in 6.6s
- codex worktree files: none; claude worktree files: none
- working-tree paths present in both workspaces: none

### V01-P4-008 — A Claude-produced artifact is independently verified by Codex

Result: **PASS**

- claude terminal=RUNTIME_CALL_COMPLETED, artifact.txt written=True
- artifact content: 'ACCRETION LIVE ARTIFACT'
- codex terminal=RUNTIME_CALL_COMPLETED, verdict token present=True
- verifier session ses_01M1DDMP20154MG01GH8M7KNH3 is not the producer session ses_01M1DDMDWRF1QVMKWXJEG73775

