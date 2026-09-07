# Router benchmark v0.4 — amendment 1: pooling the gates as rates (FROZEN 2026-09-07)

**Status: FROZEN 2026-09-07**, confirmed by the maintainer after the v0.4.1 hardening window. This
page's sha256 is pinned in every corpus's `config.v1.json` as `amendment_1_sha256`, beside
`preregistration_sha256` and never in place of it; the locked-test runner checks both before a
read starts. The locked and drift corpora register `pooling: {verified: rate, false_accept: rate}`
in the same commit as the pin, and the one re-read this amendment authorises is recorded in
[`access-log.jsonl`](access-log.jsonl) and reported under "Amendment 1" in [`results.md`](results.md).
The mechanism ([`PoolingRule`](../../../src/accretion/router_benchmark.py),
[`tests/test_v04_m10_pooling.py`](../../../tests/test_v04_m10_pooling.py)) landed inert in #163; this
page is what turns it on, and only for the two corpora that pin it.

The [pre-registration](preregistration.md) is frozen and this page does not edit it. A protocol
amendment is a **new** document with its own digest, registered beside the original; the
pre-registration says so itself ("A later change is a documented protocol amendment with a new
pin, never an edit").

## Why

[`ADR4-M10-005`](../../releases/v0.4/backlog.md) records what the locked read found: every policy
fails both safety gates on both corpora, for a reason that is a property of the corpus's pooling
rule rather than of any router. A cell holds eighteen trials.
[`RouterBenchmarkCorpus.pooled_cells`](../../../src/accretion/router_benchmark.py) reduces them
conservatively — a configuration has *verified* a node only if it verified it on **every** trial,
and a false acceptance on **any** trial is a false acceptance — and those two rules were chosen
when a cell held two trials.

At eighteen they are close to degenerate, and the two readings of the same rows sit an order of
magnitude apart. From [the gates section of `results.md`](results.md#the-gates-apart-from-utility):

| reading | verified success | false acceptance |
|---|---|---|
| per trial | 0.5121 | 0.0390 |
| pooled (`all` / `any`) | 0.0870 | 0.4348 |

The registered floor (0.70) and ceiling (0.05) were set on the per-trial scale — the pilot table
in [pre-registration item 1](preregistration.md#1-exact-project-count-and-trial-repetitions)
sizes and reports base rates per trial — and they are being read on the pooled one. The gate is
therefore answering a question nobody registered: not "does this method verify often enough" but
"does it verify on all eighteen of eighteen attempts", and not "does it accept wrongly too often"
but "did it ever accept wrongly at all in eighteen attempts".

## What changes

One thing. The two safety gates are read against the **per-trial rate** of a cell instead of
against the conjunction and the disjunction over its trials:

- `verified_success_rate` becomes the mean over the reported selections of each selected cell's
  fraction of verified trials, read against the same floor.
- `false_acceptance_rate` becomes the mean over the reported selections of each selected cell's
  fraction of falsely accepted trials, read against the same ceiling.

Mechanically this is `pooling: {"verified": "rate", "false_accept": "rate"}` in each corpus's
`config.v1.json` — a registered field of the corpus, diffable in review, and named in every
`GateReport` it produces so a report always says which reading made it.

## What does not change

- **The thresholds.** Floor 0.70, ceiling 0.05, exactly as registered. An amendment that moved a
  threshold after seeing the rows would be the post-hoc change the registration exists to
  prevent, and the expected outcome below is stated precisely so that this cannot be done
  quietly later.
- **Every other §21 field.** All fifteen stay frozen at their 2026-09-06 values: the trial count
  and project split (item 1), the primary baseline (2), the utility normalisation (3), δ_min /
  Δ_NI / α / power (4), the bootstrap (5), the invalid-action penalty (6), the oracle subset (7),
  the version windows (8), cache and outage handling (9), the cohorts (10), calibration (11), the
  adjudication rubric (12), multiplicity (13), the OPE estimators (14) and the shadow
  requirement (15).
- **The corpora.** No seed, no trace, no task, no configuration and no digest moves.
  `evals/router/locked` and `evals/router/drift` stay byte-identical; the amendment adds a field
  to `config.v1.json` and nothing else, so it changes the corpus digest and the run id — that is
  the point of deriving a run id from the files, and the re-read is reported under its own id
  rather than overwriting one.
- **The utility column, the estimands and the ablations.** They never read the pooling rule.
  The paired regret contrast that v0.4.0 reports is untouched by this amendment.
- **The claim.** `results.md` keeps its `PASS · PARTIAL (paired only)` classification. This
  amendment cannot promote it: the estimand that fell short is `g_learn`, and it is not a gate.

## Expected outcome, written before the re-read

Stated here, before anything is run, so that the re-read can disagree with it and be seen to:

1. **The false-acceptance gate is expected to pass**, at roughly **0.04** against the 0.05
   ceiling, for most policies. The corpus-wide per-trial rate `results.md` already reports is
   0.0390, and it sits close enough to the ceiling that policies which select worse-behaved
   cells may still exceed it. A per-policy row over the ceiling is an expected outcome, not a
   failure of this amendment.
2. **The verified-success gate is expected to still fail**, at roughly **0.51** against the 0.70
   floor. This is the honest half and the reason to write the expectation down: the amendment
   changes a reading, not a result, and it is not a way to clear a floor. If the verified-success
   gate passed under this reading, that would be evidence the reading had been chosen to make it
   pass.
3. **No policy is expected to flip both gates**, so the headline classification is expected to
   stay `PASS · PARTIAL (paired only)`.
4. **The drift holdout is expected to behave like the locked corpus** on both gates, since the
   degeneracy is a property of trial count and not of provider era.

An outcome outside these expectations is reported as a surprise in the "Amendment 1" section of
`results.md`, with the expectation quoted beside it.

## Procedure for the re-read

Performed on 2026-09-07, in the order below:

1. **Freeze this page** — change its status line to `FROZEN <date>`, take its sha256, and pin it
   in every corpus's `config.v1.json` as `amendment_1_sha256`, **beside**
   `preregistration_sha256` and never in place of it. Both digests are then checked before a
   locked read starts: an amended protocol is two documents, and a runner that verified only one
   of them would run under half a registration.
2. **Register the rule** — add `pooling` to `evals/router/locked/config.v1.json` and
   `evals/router/drift/config.v1.json` in the same commit as the pin, and regenerate nothing
   else. The corpus digests and run ids move because a config file moved; the tables from the
   v0.4.0 read stay on the page under their own run ids.
3. **Read once** — one locked read and one drift read, through
   `scripts/router_locked_test.py` with `ACCRETION_ROUTER_LOCKED_TEST=1`, appending exactly two
   rows to [`access-log.jsonl`](access-log.jsonl) with a reason naming this amendment. Two rows,
   not three: a rehearsal read is a read.
4. **Append, do not overwrite** — add an `## Amendment 1` section to
   [`results.md`](results.md) holding the regenerated gate blocks for both corpora and the
   comparison against the expectations above. The v0.4.0 tables stay exactly where they are, so
   the page shows both readings of the same rows and a reader can see which one each number came
   from.
5. **Record the decision** — an `ADR4.1-00k` row in [the v0.4 backlog](../../releases/v0.4/backlog.md)
   saying what the re-read found, and the [research index](../README.md) row moves from
   `amendment 1 PROPOSED` to the outcome.

## What this page does not do

This page changes no threshold, edits no frozen document, and quotes no new number; the numbers
the re-read produced live in `results.md`, never here. Every figure above is either a constant from the pre-registration or a number
`results.md` already published on 2026-09-06.
