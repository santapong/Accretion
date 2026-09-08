# Development provider pilot protocol

Study ID: `ACR-ROUTER-DEV-20260908`. Document revision: 0.1.
State: **DRAFT, NOT FROZEN**. Live execution: **NOT_AUTHORIZED**.
Prepared on 2026-09-08 against
`8ded8bab151267ec8e06182f52543dabed6eaca7`.

## Decision and claim boundary

The pilot will decide whether this instrumentation and eligible task population
can support a separately registered routing study at a justified cost. It must
measure attributable outcomes, observed configuration fidelity, verification
errors, latency, usage, actual cost availability and missingness. It does not
claim hosted routing superiority or unlock learned routing from its completion.

The first implemented step is **fake instrumentation only**, mechanically
restricted by the [manifest](fake-instrumentation-manifest.json). It makes no
provider calls, runs no learned/shadow/exploration stage, and uses the real
baseline routing and independent-verification owners where the data join needs
them. Its fixture successes are not model success rates.

## Data roles and isolation

| Role | Eligible data | Allowed decisions | Exclusions |
|---|---|---|---|
| FAKE_INSTRUMENTATION | Newly authored deterministic artifacts in disposable local repositories | Debug routing → execution → verification → cost export, denial and missing-data behavior | Never training, verifier-qualification evidence, powered sample or final outcome |
| VERIFIER_QUALIFICATION | Independently labeled correct, incorrect, incomplete, corrupt and adversarial development outputs, with provenance and permission | Qualify a frozen verifier and adjudication rubric | Never candidate tuning and final evaluation on the same examples |
| PILOT_DEVELOPMENT | Fresh permitted digital tasks, immutable inputs and project-lineage IDs, complete declared candidate coverage | Estimate variance, clustering, outages, execution fidelity, cost quality and feasible sample size | No old locked/drift rows; no final claim or post-hoc omission of method failures |
| CANDIDATE_TRAINING | Separately assigned eligible lineages, only if the future treatment learns | Fit candidate artifacts; record search/training cost | No qualification, calibration or final lineages |
| BASELINE_SELECTION | Separately assigned development/validation lineages | Choose strongest eligible fixed baseline by the frozen success/utility rule | Never select a baseline on its final test performance |
| CALIBRATION | Independent lineages reserved before selection, only if the method needs calibration | Fit the declared calibration artifact and threshold-selection rule | No reuse as training or final evidence |
| FINAL_EVALUATION | New sequestered project lineages under a later study's freeze and access log | One final registered comparison | Not collected, assigned, opened or authorized in this preparation |

Group forks, repository versions, paper extensions and generated task variants
by lineage before allocating any role. Preserve all role assignments and
exclusions in an inventory manifest. Record counts by lineage, task family,
candidate, verifier, terminal state, missing field and cost provenance. An
unobserved candidate/task cell remains missing; never impute verified success.
No eligible live task inventory currently exists for this study.

Each execution starts from a digest-bound input revision in an isolated
worktree. A paired comparison uses the same ObjectiveContract, NodeContract,
capability envelope, budget, verification specification and declared serving
window. Randomize execution order within blocks, balance provider time windows,
and register cache policy. Prevent histories, generated artifacts and evaluator
feedback leaking between candidate arms or data roles.

## Candidate eligibility

The initial fake path allows only Provider.FAKE, BASELINE_ONLY, deterministic EXPLOIT/FALLBACK
decisions, LOW_DIGITAL and empty selected tools for AGENT nodes. It denies AUTO,
SHADOW, EXPLORE, native/provider tools, selected AGENT tool configurations and
any provider registry entry other than the in-process fake. It does not call
M6 budget, M7 admission/settlement or M8 promotion. Native file writes in its
fixture hook are fixed local test actions, not model-selected tools.

For a future development pilot, propose a fixed baseline and candidate only
after the task inventory and runtime capability audit. Record immutable
runtime/model/configuration identities, compatibility receipts and requested,
accepted and observed effective settings. A model name in a request is not
evidence that the runtime honored it. Missing or conflicting bindings block
admission; unsupported selected AGENT tools remain refused.

A learned candidate needs its own training provenance, project-disjoint holdout
and calibration evidence and the existing runtime promotion gates. A fixed
configuration comparison may qualify measurement, but cannot be called a
learned-router evaluation. AUTO remains excluded until the accounting concern's
C1/C2 disposition and existing learned-routing gates are satisfied. Provider
and candidate identities are **unresolved**, not defaults inferred from old
synthetic configuration names.

## Independent outcomes and adjudication

Persist or export an attributable IndependentVerificationResult joined to its
execution instance and frozen VerificationSpec. The evaluator is separate from
the producer session. A deterministic verifier explicitly has no model session;
an absent, undeclared identity is not independence. Use existing claim coverage,
session independence and material-conflict logic. The M6 quality score and its
`observed.verified = false` are not interchangeable with this record.

Preserve PASS, FAIL, INCONCLUSIVE and ERROR distinctly. Missing coverage,
unresolved material disagreement or missing measurements cannot turn into PASS
by removing a required field. When human adjudication is required, hide method,
provider and router explanation until artifact judgment is recorded; retain the
rubric version, rater identity, agreement measure and resolution policy. The
protocol owner and independent evaluator have not been assigned.

Verifier qualification must freeze a separately labeled scope, false-accept
and false-reject endpoints, confidence level, dependence assumptions, sample
size and upper-bound gate before outcomes are inspected. Model agreement alone
is diagnostic. Any change informed by qualification results creates a new
verifier version and fresh qualification evidence.

## Quantities and analysis

Report exact denominators and units; a rounded display count is not a raw event
count. Define three distinct error rates:

1. Per-trial false acceptance: independently confirmed erroneous PASS among
   allocated eligible executions under the frozen missingness rule.
2. Verifier false acceptance: erroneous PASS among independently labeled
   incorrect qualification outputs.
3. Error among accepted outputs: confirmed erroneous PASS among all accepted
   outputs, with unresolved accepted labels retained as a possible-error range.

The applicable error ceiling, its unit, the success floor, non-inferiority
margin and confidence procedure are **unresolved for this new study**. Preserve
the old `0.05` / `0.70` registrations; do not adjust them to rescue old results.
For a new qualification gate require its upper error bound to meet its justified
ceiling. Sparse evidence is INCONCLUSIVE. Repeated nodes within a project are
not independent Bernoulli samples; declare a dependence-aware interval method.

A later routing contrast needs paired task/trial differences and project-aware
uncertainty, one predeclared primary comparison, raw quality/cost/latency, exact
success counts, critical cohorts and multiplicity handling. The present fake
stage reports none of those scientific estimands. Do not treat a deterministic
fixture panel or the synthetic v0.4 variance as live power evidence.

OQ-409 has different existing scopes: the current shadow report has a default
floor of **30 complete pairs** ([shadow.py](../../../../src/accretion/routing/shadow.py),
lines 115–120 and 727–733); the old registration and promotion config carry a
**nine-pair** rule ([pre-registration](../preregistration.md), lines 213–219;
[promotion config](../../../../evals/router/promotion.v1.json)). Neither supplies
a hosted-model power calculation or automatically proves per-configuration,
per-node-class support. Freeze the intended aggregation, support rule and
sample-size rationale before a new promotion study. No rule is changed here.

## Usage, prices and total cost

Keep currency distinct from normalized internal cost. Every collected record
joins configuration → receipt → execution → independent outcome → accounting.
Record provider/model/runtime/version window, requested and observed settings,
provider request ID when available, exact usage source, cached/input/output
tokens, measured elapsed time, provider/quota wait, local compute, verifier calls,
human time, retries, failed and rejected calls. Allocation and execution counts
are both required so denial does not silently remove an arm.

A billed amount needs measured provider accounting or a clearly labeled
estimate from recorded usage and a dated official price snapshot, including
currency, units, region/tier and cache treatment. Missing prices are UNKNOWN,
never zero. Record reconciliation uncertainty. Fake records may show zero
provider calls and zero provider charge with basis `NO_PROVIDER_CALLS`; they
must still retain local time and state that they are fake instrumentation.

Before approval, display all-stage arithmetic:

`planned execution calls = sum(tasks × candidates × repeats by stage) + reserved retries`

Add qualification, verification/adjudication, training/calibration and local
overheads in their actual units. Include failed, cached and rejected billed
requests. Reserve retries before launch; unused reserve remains unused. Specify
total money, per-run and total calls, timeouts and wall-time limits separately
from existing normalized ledger caps. No monetary amount, currency, account or
price has been chosen or looked up in this preparation.

## Freeze, authorization and stop rules

The draft may be refined using development information. Live preflight remains
closed until the readiness manifest has actual owners, eligible task inventory,
candidate identities, verified settings, qualified verifier, denominators,
cohorts, sample/repetition counts, missingness/cache/randomization rules, price
provenance, absolute spending/call/time ceilings, code/config hashes and an
explicit decision covering the exact provider accounts and allowed side effects.
User approval of the completion plan is not that spending decision.

Freeze the completed protocol and inventory together before the live pilot;
keep a new study-local execution/access log. Any later confirmatory evaluation
has a separate freeze, data and authorization. Old v0.4 protocols, corpus
metadata, traces and committed access log remain untouched.

Stop immediately for authority/policy/isolation violation, configuration-binding
mismatch, critical verifier failure, unexplained spend, exhausted limit or
uncertain dispatch. Preserve terminal state, observed costs and evidence.
Reconcile uncertain execution before retry; never promise exactly-once provider
effects. Method timeouts/crashes count against the method. Handle externally
caused outages only by the frozen rule, retain sensitivity analysis and never
exclude unfavorable results after inspection. Missing cost/verification data
blocks a complete priced-result claim even when the task artifact passed.

Classify the eventual development pilot as READY_FOR_NEW_STUDY,
NEEDS_REPAIR, INCONCLUSIVE or NO_GO with evidence. Completion never auto-promotes
a router, increases the sample, changes a threshold or authorizes another call.

## Separation from v1.1

The [v1.1 profile-only draft](../../../sdd/future/v1.1-v1.8/package/Accretion_v1x_Technical_SDD_Revision_4/24_V1_1_PILOT_READINESS_AND_PROTOCOL.md)
selects one fixed compute profile and studies acquisition cost/capped TTVS.
This preparation concerns attribution and measurement for conditional routing.
Share provenance discipline, not treatment counts, readiness, denominators or
claims. Neither study's design checks prove the other's implementation.
