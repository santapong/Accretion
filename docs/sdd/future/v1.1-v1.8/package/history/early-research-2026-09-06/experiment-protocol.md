# Accretion: proposed experiments and decision gates

6 September 2026 · Draft for future implementation · No research run authorized or launched by this document

## What was actually checked

Source inspection used `develop@71893d467cda70cadf3c4b81b1544475c74b5be7`. Two existing local tests passed in 5.34 seconds:

- [Delayed retry idempotence](/mnt/data/company/apps/Accretion/tests/test_v04_m3_api.py:203): submit the same verification result, advance the clock, submit it again; require one stored record with the original timestamp and identity.
- [Concurrent promotion during rollback](/mnt/data/company/apps/Accretion/tests/test_v04_m8_promotion.py:820): stage a promotion during the rollback drill; require a conflict and preservation of the new ledger head.

These are existing regression checks, not generated fixtures or validation of a new ML system. Both are now visible development examples and must not be used as blind holdout cases in the proposed study.

## A. Fixture quality and economics

**Question:** Does structured diagnosis plus independent variant qualification produce useful fixtures more efficiently than manual authoring or simple templates?

**Development:** inventory up to twelve confirmed incidents across at least three failure mechanisms. Record all attempted intake cases, including those lacking a reproducible environment or trustworthy fixed revision. Begin with software-only cases; hardware and live production data are outside this pilot.

**Confirmation:** aim for at least thirty additional incident families if the inventory supports them. Freeze inclusion rules, methods and budgets before inspecting confirmation outcomes. Thirty is a feasibility target, not a safety qualification sample. If only Accretion incidents are available, the conclusion is within-project feasibility; do not claim generalization across projects.

**Comparison arms:** manual authoring; rules/templates; structured diagnosis plus bounded LLM generation; the same generated candidates with independent mutation/control qualification. Keep the generator's context and candidate budget matched across the last two arms so that added generation is not mistaken for better qualification. Record human review labor in every arm.

Proposed starting generation cap: one candidate plus one repair attempt per incident. Start with a fixed small set of reviewed defect mechanisms—boundary error, missing guard, incomplete state update and omitted downstream behavior—then report which are applicable. These are design choices to freeze with the actual corpus and budget, not paper-derived optima.

**Qualification matrix:** target failure on bad revision; pass on accepted fixed revision; relevant faulty-variant rejection; pass on behavior-preserving and unrelated healthy controls; repeat in three fresh environments. Preserve infrastructure errors and excluded/equivalent variants as separate outcomes. Repeated runs of one incident help estimate flakiness, not independent incident count.

Keep the admission evaluator's hidden cases out of generation. Candidate and evaluator contexts should be separate, and an independent reviewer must approve the semantic classification of both faulty and preserving variants. Human-gold fixes are evidence, not an infallible complete specification. Disputed oracle semantics produce an unresolved case.

**Primary measures:** independently admitted useful fixtures divided by all eligible incidents; active authoring/review minutes per admitted fixture; total execution/model cost; incident reproduction yield. Secondary measures: false rejection on valid controls, missed reviewed faulty variants, infrastructure failure rate and disagreement rate. Report denominators by family and failure class.

**Economic condition:**

`benefit over horizon = avoided verification/rework cost − generation − review − execution − maintenance`

Measure currency, machine time and human minutes separately. Report scenario-based break-even reuse where later reuse has not happened. Count new fixture execution cost; a regression suite can become more expensive even when authoring becomes cheaper.

**Go/no-go:** proceed to a design ADR if useful fixture yield and labor savings justify the pipeline, with no unresolved critical containment or authority issue. Reject an arm whose apparent advantage disappears after oracle review. If simple templates perform as well, retain the simple implementation. Stop synthesis loops at the frozen budget and report incomplete cases.

### Example fixture proposal, based on Accretion

For delayed retry idempotence, the behavioral requirement is that retrying the same verification submission later preserves one original verdict. A frozen-clock test is weak because content-addressed storage can absorb identical bytes even if the API's retry lookup is broken.

The development proposal should therefore include a later clock value, a faulty variant that bypasses the existing-record lookup, and healthy controls for a genuinely new submission and an unauthorized workspace. The test must distinguish those behaviors without asserting the internal lookup implementation. A harmless refactor preserving that behavior should pass. The current regression test already demonstrates the crucial time perturbation; the experiment asks whether a pipeline can generate comparably discriminating tests on unseen incidents.

## B. Behavioral drift detection

Use a known baseline configuration, benign changes and independently adjudicated regressions. Compare a full fixed suite, fixed critical probes plus stratified sampling, and learned risk selection at an equal probe budget. Freeze the candidate fingerprint and change exactly one factor where possible.

Measure detection per regression family, false alarms per benign change, time to useful warning, test cost and critical misses. Include dependency/tool-schema changes as well as model changes. Distinguish rescoring fixed traces from fresh execution: a different agent policy needs a fresh control/candidate run, consistent with Accretion's existing branched-rollout design.

Do not pool a missing old provider snapshot into a controlled paired estimate. Record it as an observational comparison. Keep scheduled broad audits and critical probes mandatory so selective probing cannot hide failures.

## C. Cheap monitoring and assurance scheduling

Start with deterministic completion/schema/repetition checks, then a memoryless anomaly baseline, then the temporal monitor. Use only observable features and explicit missingness flags. Calibrate separately for supported runtime/configuration cohorts. Keep synthetic, injected and naturally occurring failures in separate result rows.

Fit on training lineages, calibrate on separate lineages, and evaluate on untouched lineages and a genuinely later/provider-changed cohort. If data are insufficient, use a within-project exploratory split and narrow the claim. Do not call a hash-assigned split a temporal holdout without checking dates and provider eras.

For scheduling, collect the full check-outcome matrix first. Compare fixed order, measured yield/cost order and a frozen learned ranking. Reordering alone should be evaluated for time-to-first-actionable-failure; cost savings require a separately permitted stopping or supplementary-selection policy. Include collector overhead and full-stack audits in costs.

### Statistical qualification is a separate gate

[Learn then Test](https://www.alphaxiv.org/abs/2110.01052v5) suggests evaluating a frozen finite set of policies with valid risk tests and family-wise error control, then choosing among the qualified set. It does not remove sampling or labeling requirements.

For illustration, with **zero misses on n independent, representative known-bad cases**, an exact one-sided 95% binomial upper bound on the miss probability is `1 − 0.05^(1/n)`:

| Independent known-bad cases | Upper bound with zero misses |
|---:|---:|
| 12 | 22.09% |
| 30 | 9.50% |
| 300 | 0.994% |

These are arithmetic illustrations, not observed Accretion performance. With ten pre-specified policies and Bonferroni correction, zero misses would require at least 528 such cases to put each one-sided bound below 1%. Correlated reruns, biased sampling and extra critical-cohort comparisons change the analysis. These bounds concern misses among known-bad cases, not all-run false acceptance or real-world incident frequency. No numerical tolerance in this document is an approved product safety threshold.

## Required experiment record

Each study should preserve the question, frozen candidate set, inclusion/exclusion log, case lineage, corpus digest, split/access records, source/model/tool/environment versions, resource budget, outcomes including failures, independent labels, review decisions, metric denominators and limitations. Track a claim-to-artifact link for every reported improvement.

Implementation begins with a contract and corpus review. Paid model runs, new exposure, deployment and physical execution remain outside this research preparation.
