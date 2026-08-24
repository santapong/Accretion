# ACR-ARCH v0.1

ACR-ARCH is Accretion's frozen architecture-selection benchmark. The v0.1
corpus is deliberately small enough to reproduce on a workstation while still
covering every static execution mode and the safety/recovery boundary.

<img src="assets/benchmark-pipeline.svg" alt="Reproducible ACR-ARCH pipeline from frozen tasks, environments, configuration, and replay traces through validation and deterministic scoring to queryable reports" width="100%" />

## Frozen inputs

The benchmark inputs are versioned independently from `selector-v1`:

| Input | Version | Purpose |
|---|---:|---|
| `evals/acr_arch/tasks.v1.json` | 1.0.0 | 30 task manifests, budgets, applicable modes, and selector choices |
| `evals/acr_arch/environments.v1.json` | 1.0.0 | isolated environment and fixture provenance |
| `evals/acr_arch/config.v1.json` | 1.0.0 | utility weights |
| `evals/acr_arch/replay-traces.v1.json` | 1.0.0 | 68 frozen Claude/Codex scenario observations |

The corpus contains 5 direct/simple, 8 feedback/refinement, 7 predictable
graph, 7 hybrid engineering, and 3 safety/recovery tasks. Every task declares
at least two applicable modes. The replay bundle is balanced at 34 Claude and
34 Codex scenarios.

## Metrics

Each scenario retains raw success, quality, duration, turns, tool calls, risk
events, approvals, provider, verifier, trace reference, and environment
version. Normalized cost, latency, risk, and human burden are derived from the
task's own frozen budgets. Utility and selector regret follow SDD section 19.3:

```text
U = quality - 0.15*cost - 0.15*latency - 0.35*risk - 0.20*human_burden
regret = max_mode(U) - selector_mode(U)
```

Raw dimensions remain available through the API and UI; utility does not
replace them.

## Reproduce

```bash
python scripts/generate_acr_arch_fixtures.py
uv run pytest tests/test_acr_arch.py
```

The signed-in provider calibration is intentionally separate from the frozen replay
dataset. It selects two tasks from every category, balances five calls per provider,
and independently verifies the exact artifact written by each isolated provider run:

```bash
ACCRETION_LIVE_PROVIDERS=1 ACCRETION_CLAUDE_LIVE_MODEL=sonnet \
  uv run python scripts/run_acr_arch_live_sample.py
```

The command writes its redacted report to
`artifacts/release/acr-arch-live-sample.json`; live results never alter the frozen
replay traces or release metrics.

The tests pin the corpus and trace SHA-256 digests, validate the exact category
composition, require two or more modes per task, recompute all 68 metrics, and
round-trip the immutable records through PostgreSQL when
`ACCRETION_TEST_POSTGRES_URL` is set.

The operator API provides:

```text
GET  /api/v1/benchmarks/acr-arch
POST /api/v1/benchmarks/acr-arch/run
GET  /api/v1/benchmarks/acr-arch/tasks/{task_id}
```

The summary endpoint filters by mode, provider, task type, verifier, and
selector version. The POST endpoint only accepts `REPLAY`; live subscription
runs remain an explicit local release-gate operation so normal UI use cannot
silently consume provider quota.

The implemented operator page is `http://localhost:5173/benchmarks/acr-arch`.
See the [frontend guide](FRONTEND_GUIDE.md) for its place in the complete route
map and the snapshot authority model shared with the P6 and P7 research pages.
