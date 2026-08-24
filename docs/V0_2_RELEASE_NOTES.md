# Accretion v0.2.0 release notes

Target version: `v0.2.0`. Release date: pending a fully passing release audit.

Accretion v0.2.0 extends the immutable v0.1 static control plane with opt-in,
deterministically governed dynamic orchestration. Dynamic features remain
disabled by default and cannot expand permissions, bypass approvals or
verifiers, expose credentials, or erase durable execution history.

<img src="assets/v02-release-gate.svg" alt="v0.2 release evidence flowing from the immutable v0.1 control through P5 dynamic, P6 search, P7 transfer, and the full clean-checkout gate before promotion to main" width="100%" />

## Highlights

- **P5 validated dynamic workflows:** typed proposals, deterministic validation,
  one bounded repair, immutable graph revisions, safe replanning, observable
  runtime selection, and a validated v0.1 static fallback.
- **P6 bounded candidate search:** best-of-N, hypothesis, cross-provider, and
  generator-reviewer modes with isolated worktrees/sessions, shared budgets,
  independent ranking, explicit stop reasons, and crash-safe promotion.
- **P7 verified experience:** immutable terminal evidence, deterministic local
  embeddings, compatibility and transfer-risk scoring, negative procedural
  knowledge, controlled replay seeds, fresh controls, and repeated revalidation.
- **Complete operator frontend:** eleven routes spanning task planning, live
  execution, governance, ACR-ARCH, and dedicated P5, P6, and P7 research views.
- **Frozen research evidence:** reproducible ACR-ARCH, P5 static/dynamic, P6
  quality-versus-compute, and P7 experience-transfer suites with exact hashes
  and preserved negative/null results.

## Research results

| Gate | Frozen result |
|---|---|
| P5 dynamic workflow | `+0.224631` utility on heterogeneous/uncertain tasks; predictable cohort `+0.009195`; static fallback PASS |
| P6 bounded search | verified acceptance rises from 8/12 at N=1 to 12/12 at N=4; mean quality `0.472500 → 0.768333` |
| P7 verified experience | replay quality `+0.070500`, 20% fewer tools, 95% stale rejection, 3.33% negative transfer |

These are deterministic replay-fixture results. Signed-in Codex and Claude
calibration is recorded separately in the release audit and does not rewrite
the frozen metrics.

## Compatibility and migration

- Python 3.12 or newer and Node.js 22 or newer remain required.
- PostgreSQL 16 with pgvector is the supported database used by the release gate.
- Apply Alembic migrations through `0009_p7_experience_contracts`.
- Existing v0.1 endpoints remain available; v0.2 behavior is additive under
  `/api/v2` and independent project/deployment feature flags.
- Validated live CLI ranges are Codex CLI `>=0.148,<0.149` and Claude Code
  `>=2.1.231,<2.2`.

## Install after release

```bash
git clone --branch v0.2.0 https://github.com/santapong/Accretion.git
cd Accretion
cp .env.example .env
uv sync --all-groups
npm ci
docker compose up -d postgres
uv run alembic upgrade head
```

Run `make api` and `make ui`, then open `http://localhost:5173`. Start with the
deterministic fake runtime. See the [frontend guide](FRONTEND_GUIDE.md),
[developer guide](DEVELOPER_GUIDE.md), and [showcase](SHOWCASE.md).

## Evidence and limitations

The [release audit](V0_2_RELEASE_AUDIT.md) identifies the exact candidate,
checks, tool versions, provider calibration, browser evidence, and current
GO/NO-GO decision. Do not use the install command above until that audit records
GO and the tag exists. The v0.1 tag remains immutable and reproducible through its
[baseline record](V0_1_BASELINE.md).

v0.2 does not include learned routing, self-modifying policy, automatic
experience promotion, unrestricted production deployment, or the v0.3
identity/plugin/connection platform. The current frontend bundle-size advisory
is non-blocking and remains documented technical debt.
