# Router benchmark v0.4 — pre-registration (PENDING FREEZE)

**Status: none of the fifteen fields below is frozen.** Every one carries `Status: TBD` and a
proposal. Test-set access does not begin, and no result from the evaluation half may be quoted
as a claim, until a maintainer replaces each `TBD` with `FROZEN` and fills the pilot table at
the end of this page by hand.

[Section 21 of the research protocol](../../sdd/future/v0.4-v1.0/03_RESEARCH/Accretion_v0.4_Research_Protocol.md)
lists the fields and requires a development-only pilot before they are frozen. This page is
the draft that pilot exists to inform. It is written before the numbers are entered on
purpose: a pre-registration assembled after its measurements are known is a description, not a
registration.

## How to run the pilot

```bash
# the shipped corpus: 12 projects, 36 tasks, 6 configurations, 2 trials per cell
PYTHONPATH=src uv run --no-sync python scripts/router_pilot.py

# the same seeded world at nine trials per cell, written outside the repository
PYTHONPATH=src uv run --no-sync python scripts/router_pilot.py \
  --regenerate --trials 9 --out /tmp/router-pilot-9 --json
```

The command replays every §8.1 comparator over both halves of the split and prints, per
policy: mean regret, the within- and between-project variance components and their intraclass
correlation, verified-success rate with a Clopper–Pearson interval, false-acceptance count
with a Clopper–Pearson upper bound, the trial-to-trial σ of utility and of the pass rate, the
outage rate, the power table and `pass@k` / `pass^k`. It writes nothing under `evals/` and
decides nothing.

Two trials per cell give one degree of freedom per cell, so the shipped corpus's σ is reported
with a thinness flag. The `--regenerate --trials 9` form exists to show what that flag costs:
same seed, same designed world, only the repeat count moves.

## The fifteen fields

### 1. Exact project count and trial repetitions

- **Status: TBD**
- **Proposal.** Size the trial repetitions from `power_sample_size(delta, sigma)` evaluated at
  the **trial-to-trial** σ the pilot measures, not at the pooled spread across cells, and take
  the resulting n as the floor rather than the target. Independently of that arithmetic, hold
  at least **9 trials per cell** (the OQ-409 interim rule, [SDD §21 open questions](../../sdd/Accretion_SDD_v0.4.md))
  and at least **6 projects per half of the split**, which is what the shipped corpus already
  carries and the minimum at which a project-clustered bootstrap has anything to resample.
- **Note for the freeze.** The pilot's power table and the ≥ 9 floor are expected to disagree,
  and by a lot: an effect of two percentage points against the corpus's observed σ needs far
  more than nine repeats. Freezing item 1 means choosing which of the two governs, and saying
  so — either the locked test is sized for δ = 0.02 and is expensive, or δ_min is raised in
  item 4 to something the budget can detect.

### 2. Primary baseline identity

- **Status: TBD**
- **Proposal.** The primary baseline is the **best fixed configuration**, chosen by
  `accretion.routing.stats.select_best_fixed` over the `selection_project_ids` of
  [`evals/router/config.v1.json`](../../../evals/router/config.v1.json) and scored only on the
  `evaluation_project_ids`. The primary comparison is **M9 (the full v0.4 guarded router)
  against that baseline**, one comparison, declared before any evaluation-half read.
- **Why.** The argmax is itself a comparison, and a baseline chosen on the data it is then
  scored on is biased upward by the winner's curse. The two halves are disjoint at the level
  of *lineage* and not merely of project id, which `RouterBenchmarkCorpus.load` proves at load
  time: a fork and its upstream are not two independent projects.

### 3. Utility normalization

- **Status: TBD**
- **Proposal.** `accretion.routing.regret.utility`: `quality − w_cost·cost − w_latency·latency`,
  with quality as the numeraire (`weights.quality` is *not* applied as a fourth multiplier),
  the **corpus weights 1.0 / 0.3 / 0.15** and a latency budget of **60000 ms** per node, both
  read from `config.v1.json`.
- **The discrepancy this field must settle.** The M2 objective minter
  (`accretion.routing.selector.DEFAULT_UTILITY_WEIGHTS`, and `routing/freeze.py`) uses
  **1.0 / 0.25 / 0.15**. The two vectors are close and are not the same, and a benchmark quoted
  under one while the shipped selector optimises the other is a benchmark of a policy nobody
  runs. This item names the **corpus** weights, because the benchmark's numbers must be
  recomputable from the committed corpus alone. Reconciling the selector's default to match is
  a separate decision with its own migration, and is deliberately not smuggled in here.

### 4. δ_min, Δ_NI, α and power

- **Status: TBD**
- **Proposal.** `delta_min = 0.02`, `Delta_NI = −0.02`, `alpha = 0.05`, `power = 0.8` — the
  values already registered in [`evals/router/promotion.v1.json`](../../../evals/router/promotion.v1.json)
  for the promotion gate, adopted unchanged so that the number a promotion is gated on and the
  number the benchmark is powered for are one number.
- **Note for the freeze.** See item 1: if the pilot's sizing at δ = 0.02 is unaffordable, this
  is the field that moves, and it must move *before* the evaluation half is read.

### 5. Confidence interval / bootstrap procedure

- **Status: TBD**
- **Proposal.** Continuous endpoints: `accretion.routing.stats.hierarchical_bootstrap` with
  **B = 2000** replicates, resampling **projects** and then units within each resampled
  project, seeded from `config.v1.json`'s `seed`. The primary analysis is the paired form,
  `paired_regret_ci`, on within-pair differences. Binary endpoints (verified success, false
  acceptance): **Clopper–Pearson** exact intervals, and differences of two of them taken
  conservatively as `(lo_a − hi_b, hi_a − lo_b)`.
- **Why two procedures.** Nodes within a project share an objective, a repository and a policy
  set; an interval that treated them as independent draws would be too narrow by roughly the
  square root of the cluster size. Success counts, meanwhile, are small enough that a normal
  approximation puts interval limits outside [0, 1].

### 6. Invalid-action penalty

- **Status: TBD**
- **Proposal.** **1.0** — one whole unit of quality, so an invalid selection scores below every
  valid one — applied identically whether the configuration was never eligible or was executed
  and flagged invalid by the trace. A policy is not better off for having had its invalid
  choice run.

### 7. Candidate subset used for oracle construction

- **Status: TBD**
- **Proposal.** The five configurations registered in `config.v1.json` as
  `oracle_candidate_subset`:

  | Configuration | Why it is in the subset |
  |---|---|
  | `cnf-claude-opus-full` | executed under matched conditions on every task |
  | `cnf-claude-sonnet-lean` | executed under matched conditions on every task |
  | `cnf-codex-standard` | executed under matched conditions on every task |
  | `cnf-codex-mini` | executed under matched conditions on every task |
  | `cnf-deterministic-scripted` | executed under matched conditions on every task |

  `cnf-opencode-local` is deliberately **outside** it: a real, admissible, selectable
  configuration whose environment was never matched, so it may be chosen but may not define
  the post-hoc bound. A subset that happened to be everything would make §8.3 a sentence
  rather than a constraint.

### 8. Provider / model / tool version windows

- **Status: TBD**
- **Proposal.** Two levels, both already carried by the schema. Coarse: the corpus's
  `labels.provider_era` on each project in `evals/router/projects.v1.json`, which is what the
  era-aware split reads. Fine: `accretion.contracts.routing.ServingWindow` — provider, runtime
  version, model, and the `serving_labels` map carrying quantization, temperature and sampling
  seed (OQ-415). An outcome recorded without a serving window is a measurement of an unknown
  and is not evidence about a configuration.

### 9. Cache and outage handling

- **Status: TBD**
- **Proposal.** *Replay:* none. The corpus grid is complete by construction and its loader
  refuses a hole, so the outage rate on a replay run is **0** and the pilot reports it as a
  measured quantity rather than asserting it. *Live:* register a missingness rule before the
  first live run — which failures are excluded (provider outage, quota exhaustion, harness
  crash) and which are not. **Method-caused failures count against the method.** A router that
  selects a configuration which then times out has selected badly, and excluding that as an
  "outage" would let a policy improve its score by choosing fragile configurations.

### 10. Critical cohort definitions and thresholds

- **Status: TBD**
- **Proposal.** The five cohorts registered in `promotion.v1.json`: `correctness`,
  `high_risk`, `policy`, `secrets`, `verifier_conflict`. Each is a **hard** non-regression
  gate at `Delta_NI`: a cohort whose paired lower bound falls below the margin blocks, and a
  cohort that cannot be compared at all also blocks rather than passing by default. The list is
  a registered list and not a hard-coded set, so a deployment may add cohorts; it may not
  remove one after seeing the rows.

### 11. Calibration metric and maximum threshold

- **Status: TBD**
- **Proposal.** Expected calibration error over **10 equal-width bins**, ceiling **0.05**
  (`calibration_max_ece` in `promotion.v1.json`), computed on the project-disjoint holdout and
  reported beside the Brier score and the conformal quantile so that a ceiling met by a
  degenerate predictor is visible as one.

### 12. Human adjudication rubric

- **Status: TBD**
- **Proposal.** *Replay:* no human adjudication. The corpus's verification verdicts are the
  ground truth, they are committed, and re-adjudicating them per run would make the benchmark
  irreproducible. *Live:* a **blinded** rubric — the adjudicator sees the node, the artefact
  and the verifier output, and does not see which configuration produced it or which arm it
  came from — with the rubric text and the inter-rater agreement statistic registered before
  the first adjudicated run.

### 13. Multiplicity handling

- **Status: TBD**
- **Proposal.** `accretion.routing.stats.bonferroni` at `alpha / (K + L)` with **K = 6** (the
  configurations that competed for the baseline slot) and **L = 3** (the policies reported
  against it: oracle, signal-restricted, learned), giving 9 comparisons and an adjusted level
  of α/9. Every interval, **including the baseline's own**, is reported at the adjusted level,
  because the baseline was selected by a comparison and a family that counts the reports but
  not the selection is under-corrected. Exactly **one primary comparison** is declared (item 2);
  everything else is secondary and labelled as such.

### 14. OPE estimators and clipping

- **Status: TBD**
- **Proposal.** Primary: **Logarithmic Smoothing** (`accretion.routing.ope.ls_estimate`) with
  `lambda = 1/sqrt(n)` fixed by rule and never tuned, where `n` is the unit count and not the
  cluster count. Reported beside it as diagnostics and never as the gate: **SNIPS**,
  **doubly-robust**, **effective sample size** and **clipped mass** at the positivity
  threshold. The gate reads the pessimistic estimator; the diagnostics exist so a reader can
  price that pessimism against the positivity violation behind it.

### 15. Promotion shadow duration / evidence requirement

- **Status: TBD**
- **Proposal.** At least **9 paired runs per configuration per node class** — OQ-409's interim
  rule, `shadow_min_paired_runs` in `promotion.v1.json` — plus a non-inferiority pass at
  `Delta_NI` on the paired difference `U(SHADOW) − U(CONTROL)`. Freezing this field is what
  closes OQ-409; until then the number is an interim rule and is labelled as one wherever it
  is quoted.

## Pilot numbers

Filled by hand from `scripts/router_pilot.py` at the freeze, on the corpus and split named in
the caption. Left empty here on purpose: a table pre-filled by the same change that proposes
the fields would be a measurement chosen to fit them.

Corpus: _______________  Split: _______________  Trials per cell: _______  Date: ___________

| Quantity | Value | Interval or note |
|---|---|---|
| Trial-to-trial σ (utility) | | |
| Trial-to-trial σ (pass rate) | | |
| Degrees of freedom behind σ | | |
| Within-project variance (primary policy) | | |
| Between-project variance (primary policy) | | |
| Intraclass correlation | | |
| Verified-success base rate | | |
| False-acceptance count | | |
| Outage rate | | |
| Runs per configuration at δ = 0.02 | | |
| Runs per configuration at δ = 0.01 | | |
| `pass@2` / `pass^2` | | |

## What this page does not do

It flips no acceptance criterion, changes no threshold in `evals/`, and quotes no result from
the evaluation half as a claim. The router benchmark's own numbers are produced by
`GET /api/v2/benchmarks/router` and by `tests/test_v04_m10_router_benchmark.py`; this page only
says what will count as evidence once the fields above are frozen.
