# Experiments and results

This page is the authoritative index for Accretion's reproducible evidence. It
separates frozen replay experiments from signed-in provider calibration and from
the release engineering gate, so a passing operational check is never presented
as a research result.

<img src="../assets/research-results.svg" alt="Accretion v0.2 experiment map showing the immutable ACR-ARCH control, positive P5 dynamic-workflow uplift, bounded P6 quality-versus-compute improvement, and passing P7 verified-experience transfer gates, followed by separate live-provider and release checks" width="100%" />

## Result at a glance

| Experiment | Frozen design | Primary result | Classification |
|---|---|---|---|
| [ACR-ARCH v0.1](acr-arch-v0.1.md) | 30 tasks, 68 balanced Claude/Codex replay scenarios | Immutable static architecture-selection control with raw quality, cost, latency, risk, and human-burden dimensions | CONTROL |
| [P5 dynamic workflows](p5/benchmark.md) | 12 tasks, 24 paired static/dynamic traces | Heterogeneous/uncertain utility uplift `+0.224631`; success `9/12 → 12/12`; predictable uplift `+0.009195`; static fallback and safety gates pass | PASS · POSITIVE |
| [P6 bounded search](p6/acceptance.md#frozen-benchmark-result) | 12 held-out tasks at N=1, 2, and 4 | Verified accepts `8/12 → 12/12`; mean quality `0.472500 → 0.768333`; explicit null-gain result preserved | PASS |
| [P7 verified experience](p7/acceptance.md#frozen-benchmark-result) | 20 tasks, 50 sources, 80 traces, four treatments | Replay quality uplift `+0.070500`; 20% fewer tool calls; 95% stale rejection; 3.33% negative transfer; false accepts do not increase | PASS |

The P5–P7 values above are deterministic properties of versioned fixtures. They
do not claim that a currently hosted model will reproduce those exact values.
Negative and null results remain in the corpora and are part of each gate.

## Evidence classes

| Evidence class | What it answers | Mutability | Source |
|---|---|---|---|
| Frozen replay | Does the implemented policy reproduce the preregistered result and safety gate? | Fixtures and hashes are immutable for the release | `evals/` plus the reports linked above |
| Signed-in calibration | Can the installed Codex and Claude adapters complete isolated, normalized, independently verified calls? | Environment-specific and rerunnable; never rewrites frozen metrics | [v0.2 release audit](../releases/v0.2/audit.md) |
| Release engineering | Does the exact candidate pass tests, migrations, generated contracts, dependency audits, CI, browser checks, and protected promotion? | Repeated for every release candidate | [v0.2 release audit](../releases/v0.2/audit.md) |

The latest signed-in calibration completed all 3 live-runtime cases and all
10/10 balanced ACR-ARCH sample assignments. The redacted sample used Codex CLI
`0.148.0`, Claude Code `2.1.241`, and the explicit Claude `sonnet` model; its
SHA-256 is
`f378db0cd06fc1e95cfe5527496ea98e12c216a9ebb2d1d59bebdb389c2fe76c`.
This remains calibration evidence, not a frozen benchmark replacement.

The v0.2.0 release used a documented maintainer exception because no supported
browser instance was connected. No rendered browser or accessibility PASS is
part of the research evidence; [issue #52](https://github.com/santapong/Accretion/issues/52)
tracks that separate post-release validation.

## Reproduce the experiments

Run commands from the repository root after `uv sync --all-groups` and
`npm ci`.

```bash
uv run --no-sync python scripts/generate_acr_arch_fixtures.py
uv run --no-sync pytest tests/test_acr_arch.py

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest \
  -p pytest_asyncio.plugin \
  tests/test_p5_dynamic_benchmark.py \
  tests/test_p6_search_benchmark.py \
  tests/test_p7_experience_benchmark.py
```

For signed-in calibration, opt in explicitly and keep the output outside Git:

```bash
ACCRETION_LIVE_PROVIDERS=1 ACCRETION_CLAUDE_LIVE_MODEL=sonnet \
  uv run --no-sync python scripts/run_acr_arch_live_sample.py \
  --output artifacts/release/v0.2.0/acr-arch-live-sample.json
```

## Read the detailed evidence

- P5: [benchmark](p5/benchmark.md), [acceptance](p5/acceptance.md), and
  [decisions](p5/decisions.md).
- P6: [acceptance and results](p6/acceptance.md), [operator showcase](p6/showcase.md),
  and [decisions](p6/decisions.md).
- P7: [acceptance and results](p7/acceptance.md), [operator showcase](p7/showcase.md),
  and [decisions](p7/decisions.md).
- Operations: [P5](../runbooks/p5-dynamic-workflows.md),
  [P6](../runbooks/p6-candidate-search.md), and
  [P7](../runbooks/p7-verified-experience.md) runbooks.

When an experiment changes, follow the evidence-update checklist in the
[documentation guide](../governance/documentation.md#experiment-and-result-updates).
