# v0.4 research closure and next-study readiness audit

Date: 2026-09-08 (Asia/Bangkok). Inspected baseline:
`8ded8bab151267ec8e06182f52543dabed6eaca7`, clean worktree on
`docs/v04-research-audit`. This is a planning audit using committed reports,
protocols, access-log metadata and source inspection. No locked/drift trace rows
were opened, no evaluation was run, and no provider was called. Line references
below refer to that baseline.

The v0.4 engineering release and v0.4.1 hardening are already recorded as passing
their release gates. The routing research remains **PARTIAL (paired only)**.
Closing that research honestly requires an explicit limited-scope decision; it
does not require a favorable rerun. A priced provider study is an optional new
research program with unmet readiness gates, not unfinished execution secretly
required by the released benchmark.

## What the evidence establishes

| Evidence | Supported finding | Limit and source |
|---|---|---|
| v0.4.0 frozen synthetic replay | M9 improves the project-clustered paired regret contrast against the selection-valid best fixed baseline: locked `0.406954 [0.009603, 0.939517]`; drift `0.168669 [0.039887, 0.320070]`, at the registered adjusted level | This is a result in a designed synthetic world. Each corpus has 12 projects, six per lineage-disjoint half, 36 tasks, six configurations and 18 trials per cell; the reported evaluation contrast covers 18 tasks. [Results](../../../research/v0.4/results.md), lines 16–45, 49–77 and 173–208 |
| Binary endpoint and opportunity | `g_learn` spans zero on both corpora. No recovered fraction is quoted because the opportunity gap's lower limit is not positive | No demonstrated binary superiority, hosted-model benefit or recovered opportunity percentage. [Results](../../../research/v0.4/results.md), lines 62–77 and 173–208 |
| Original safety reading | All policies fail both gates under `all` verified / `any` false-accept trial pooling | These remain historical v0.4.0 tables. Increasing a cell from two to eighteen trials changed the practical meaning of the old reduction. [Results](../../../research/v0.4/results.md), lines 79–118 |
| Amendment 1 | The authorized re-read uses mean selected-cell per-trial rates, with floor `0.70` and ceiling `0.05` unchanged. M9 locked: verified `0.768519`, false acceptance `0.067901`, both-gates FAIL. M9 drift: `0.706790`, `0.049383`, both-gates PASS | The drift pass is marginal; the locked false-acceptance gate fails. The amendment does not establish the full claim or improve `g_learn`. [Results](../../../research/v0.4/results.md), lines 248–289, 315–328 and 379–393; [amendment](../../../research/v0.4/amendment-1.md), lines 43–77 |
| Ablations | Results expose which components change replay utility; removing guarded exploration increases replay utility by `0.050375` | Replay charges exploration but cannot measure the value of subsequently using its new information. This is not grounds to remove the guard. [Results](../../../research/v0.4/results.md), lines 137–165 |
| Engineering release evidence | The v0.4.1 audit records acceptance `167 / 159 / 0`, five release conditions passing, and clean-database migration reversal | These are previously recorded release checks, not tests rerun in this audit and not proof of research superiority. [Release audit](../audit.md), lines 223–239 |
| Real providers | The priced routing study was **not run** | All reported v0.4 research numbers are synthetic replay. Earlier signed-in adapter calibration does not fill this gap. [Results](../../../research/v0.4/results.md), lines 210–216; [research evidence classes](../../../research/README.md), lines 38–62 |

The paired locked interval excludes zero but its lower limit `0.009603` is below
the registered `delta_min = 0.02`. Do not silently translate "excludes zero" into
"the full minimum-effect and safety claim passed." The protocol requires the
minimum meaningful effect and all safety conditions as well as directional
improvement; safety already supplies a decisive limitation here.
[Pre-registration](../../../research/v0.4/preregistration.md), lines 92–110;
[protocol](../../../sdd/future/v0.4-v1.0/03_RESEARCH/Accretion_v0.4_Research_Protocol.md),
lines 368–385 and 563–578.

## Preserve the freeze and correct current navigation

Metadata inspection found four committed access-log rows: the original locked
and drift reads at `2026-09-06T15:54:50.698865+00:00`, and amendment-1 reads at
`2026-09-07T02:34:03.259163+00:00`. This audit adds none.
[Access log](../../../research/v0.4/access-log.jsonl), lines 1–4;
[amendment procedure](../../../research/v0.4/amendment-1.md), lines 101–125.

| Frozen artifact | SHA-256 observed in this audit |
|---|---|
| `docs/research/v0.4/preregistration.md` | `6b45998b3a9847298ba878a6414211ce38d64c775e5addd587509fdd70cf04ea` |
| `docs/research/v0.4/amendment-1.md` | `139f60692de5e9faa545d3e169fcf92e8aab3e45ac41a7ec1b0891cdae4e3914` |
| `docs/research/v0.4/access-log.jsonl` | `976f3357bc6b4850a9183a5d497158a8c1dafaf284546bc5dfbd2d553d04ca9e` |

All three `evals/router` configuration files pin both protocol digests. Only
the locked and drift configs declare `rate` / `rate`; the development config
retains the absent-rule default. No trace hash was recomputed and no corpus
loader was called. These are configuration observations, not independent
reproduction of the measured results.

The current [research index](../../../research/README.md), lines 18 and 25–31,
still says two access rows and "read exactly once" while its amendment paragraph
and the audit addendum describe the later read. The closure documentation should
say **one original read plus one authorized amended read per corpus, four rows
total**, link both readings, and preserve `PARTIAL`. Label original gate failures
as the v0.4.0 reading and summarize the amended outcomes beside them. Historical
milestone/ADR text may remain historical; it should not masquerade as current
status. Do not edit either frozen protocol to fix narrative drift, overwrite
old result blocks, modify the access log, or run table-regeneration tests merely
to validate a documentation change.

## v0.5 entry decision

[v0.5 SDD §2.4](../../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md),
lines 62–70, requires the pre-registered v0.4 claim **or a documented no-go**,
along with four other entry conditions. The
[v0.4 protocol §20](../../../sdd/future/v0.4-v1.0/03_RESEARCH/Accretion_v0.4_Research_Protocol.md),
lines 563–578, explicitly classifies improved regret with a failed safety gate
as research evidence, not a successful full v0.4 release claim.

The inspected release/research reports and forward-governance documents disclose
the limitation, but do not explicitly record a decision satisfying v0.5 §2.4.
`PASS · PARTIAL`, the amendment outcome in
[ADR4.1-004](../backlog.md), lines 732–744, and a passing engineering release gate
should not be substituted for that decision.

The closure plan should have the maintainer record the following decision in a
current release closure/handoff document, linked from the release index:

> NO-GO on asserting the full pre-registered routing-benefit claim from the
> v0.4/v0.4.1 evidence. Retain the synthetic paired-regret result, its binary
> uncertainty and the amended safety failures. Accept the existing engineering
> release as a bounded control-plane foundation. Carry learned-routing benefit
> and real-provider generalization as unproved; retain default-off,
> BASELINE_ONLY behavior and existing promotion gates. Further research requires
> a new scoped protocol and evidence.

This audit proposes that decision; it does not claim the maintainer has adopted
it or that v0.5 implementation is authorized. After adoption, verify the other
§2.4 items separately: v0.1–v0.3 release gates, migration stability on the chosen
candidate, capability denials, and independent PASS/FAIL/INCONCLUSIVE
verification. A no-go for the broader claim is not proof of no benefit and does
not authorize robot simulation or physical work.

## Readiness of a new priced routing study

The existing research surfaces are useful instruments, but none is the missing
priced study runner:

- [Router benchmark API](../../../../src/accretion/api/benchmarks_router.py),
  lines 1–16 and 367–388, rejects non-REPLAY at 422; the
  [runner](../../../../src/accretion/router_benchmark.py), lines 983–1013,
  independently refuses it. Preserve this boundary.
- [Development pilot CLI](../../../../scripts/router_pilot.py), lines 1–29,
  replays synthetic data to estimate variance and repetitions. It cannot provide
  hosted-model variance, prices or verifier qualification.
- [M6 paired forks](../m6-plan.md), lines 19–32 and 52–79, provide matched fresh
  worktrees and bounded LOW_DIGITAL execution. However, their quality grading
  persists no IndependentVerificationResult and `observed.verified` stays false.
  The recorded cost is a normalized attempt/tool proxy. The
  [cost implementation](../../../../src/accretion/routing/rollout.py), lines
  903–914, explicitly says these are not monetary units.
- [Release limitations](../notes.md), lines 159–168, disclose the in-memory M7
  ledger, exact-tool-pin refusal at the routed AGENT boundary, and uncertain
  execution after a dispatch claim. A study cannot bypass these limitations to
  obtain more samples. Runtime audit findings determine the eligible treatment
  set before registration.

### Preparation work and dependencies

| Work package | Concrete deliverable and completion gate | Dependency / authority |
|---|---|---|
| R0 — Close the existing evidence | Current research-index wording plus the scoped claim decision above; cite both readings and the engineering baseline | Documentation work can proceed now. The claim decision needs a named owner; no new locked read is needed |
| R1 — Inventory eligible development tasks | Metadata manifest with project lineage, task/cohort, immutable input revision, capability/risk, terminal states, verifier identity, observed setting coverage, timing and cost completeness; document excluded or missing cells | Use permitted development data only. Existing locked/drift corpora stay closed and cannot become a fresh confirmation set |
| R2 — Qualify the measurement path | Fake/local fault-injection evidence for isolated paired starts, receipt-to-runtime binding, independent verification records, measured usage fields, external monetary cap, cancellation and uncertain-dispatch reconciliation | Complete before paid calls. A proxy may be reported as a proxy but cannot satisfy the priced-study question |
| R3 — Qualify the verifier | Frozen labeled correct/incorrect/incomplete/corrupt/adversarial development outputs; executable checks; blinded adjudication rubric; disagreement, false-accept and false-reject reports | Producer and evaluator must be independent. Register confidence bounds and sampling units before inspecting qualification outcomes |
| R4 — Freeze the bounded development pilot | Named providers/configurations, actual observable settings, eligible cohort, primary comparator rule, randomized matched order, trial count, budget, missingness and stop rules; signed or otherwise attributable study decision | New paid calls require explicit authority for the concrete protocol and spending ceiling. The current planning request is not that approval |
| R5 — Run and classify the development pilot | Redacted immutable artifacts and complete cost/denominator ledger; estimates of variance, clustering, base rates and failure/missingness; READY / NEEDS REPAIR / INCONCLUSIVE / NO-GO decision | Only after R1–R4. A pilot measures feasibility and sizes the next study; it does not establish the confirmatory claim |
| R6 — Freeze and evaluate a new study, if warranted | New project-lineage split, held-out selection/calibration roles, analysis code and protocol digests; independent final evaluation opened once after freeze; separate report and access log | Needs a second reviewed budget and freeze. Do not repin old corpora or expand a stopped sample after seeing its outcome |

The minimum useful live preparation is a matched pair of an eligible fixed
baseline and a frozen candidate on repeatable LOW_DIGITAL tasks. Development
projects select the strongest eligible fixed baseline by the registered rule;
final data cannot choose it. Keep the deterministic production baseline as a
declared control. Training, candidate selection, calibration and final
evaluation have disjoint project-lineage roles where the method needs them.
If the candidate lacks a trained, calibrated, holdout-qualified artifact, the
pilot can qualify instrumentation or compare fixed configurations; it must not
be labeled a learned-router evaluation. Reuse existing router/verification
owners instead of implementing a second router or an evaluator inside the
benchmark HTTP route.

### Define the safety quantities before choosing a ceiling

The unresolved question is the meaning and justification of the ceiling, not
which number makes M9 pass. Keep the existing `0.05` and `0.70` registrations
unchanged. Any new study must separately declare:

1. **Per-trial false acceptance:** independently confirmed erroneous PASS events
   divided by all eligible allocated executions, with method failures and
   ambiguous labels handled by the frozen rule.
2. **Verifier false acceptance conditional on incorrect output:** erroneous
   PASS among independently labeled incorrect outputs. This qualifies the
   verifier and has a different denominator from item 1.
3. **Errors among accepted outputs:** confirmed erroneous PASS among all accepted
   outputs, retaining unresolved accepted labels as a possible-error range.
4. **Critical-event handling:** critical authority/policy/isolation violations
   stop the study immediately, even when an aggregate error fraction remains
   below its registered limit.

State whether the statistical gate reads a point estimate or a one-sided upper
confidence bound, its confidence level, multiplicity family and independent
sampling unit. For a new verifier-qualification gate, require the upper bound
to meet the declared ceiling. For clustered/repeated task observations, use a
registered analysis that respects dependence; a binomial bound on every tool
call as if independent is not adequate. Zero observed errors with insufficient
support is INCONCLUSIVE. These are proposals for a new study, not amended
interpretations of frozen rows.

The current [GateReport calculation](../../../../src/accretion/router_benchmark.py),
lines 1228–1253, compares point rates and rounds sums of cell fractions into its
integer display counts. Under the amended reading, the printed integer is not
an exact observed trial-event numerator. New priced-study reports must export
exact trial denominators/numerators, per-cell repetition counts, the pooling
rule and uncertainty; they must not construct a binomial safety proof from
those rounded display counts. The v0.4 protocol already requires false-accept
counts and upper bounds, not inference from absence of events alone
([§12.3](../../../sdd/future/v0.4-v1.0/03_RESEARCH/Accretion_v0.4_Research_Protocol.md),
lines 383–385).

### Size, cost and stopping rules

Use the existing protocol's matched contracts, project-aware paired analysis,
randomized order and cache controls
([§§7–10](../../../sdd/future/v0.4-v1.0/03_RESEARCH/Accretion_v0.4_Research_Protocol.md),
lines 202–225 and 260–321). The original registration keeps a minimum of nine
trials per cell for live work following that protocol
([item 1](../../../research/v0.4/preregistration.md), lines 40–58). This is a floor,
not proof of power. Do not transplant the synthetic utility sigma or its
approximately 6,000-unit binary sizing as a hosted-model estimate. A differently
scoped small instrumentation check must say that it is not the registered
comparative study.

For a new study, show arithmetic before asking for execution authority:

`planned calls = sum(stage tasks × configurations × repeats) + qualification + reserved retries`

Count development, baseline selection, training/calibration where applicable,
both matched arms, final evaluation and verifier invocations. Bound monetary
spend separately from tokens, calls, attempts, wall time, provider/quota waits,
local compute and human adjudication. Record real provider/model/runtime
versions and a dated price basis; mark unavailable prices unknown instead of
zero. Include billed failures and unused reserved budget in the account.
Price discovery is a later preparation task; no price was fetched or assumed
in this audit.

Before launch, the owner must fix the cohort and projects, provider/configuration
permissions, evaluators, exact ceilings and margins with their rationale,
project/trial counts, absolute money/call/time ceilings, missingness rule and
stop authority. Preflight refuses missing values. Stop on critical verifier,
policy or isolation failure, a binding mismatch, exhausted budget, unaccounted
spend or uncertain dispatch; preserve the evidence and reconcile before any
retry. Timeouts and method-caused failures remain outcomes. No live promotion
occurs merely because a pilot completes, and no new provider calls are implied
by an inconclusive result.

## Keep v1.1 separate

The imported [v1.1 readiness protocol](../../../sdd/future/v1.1-v1.8/package/Accretion_v1x_Technical_SDD_Revision_4/24_V1_1_PILOT_READINESS_AND_PROTOCOL.md),
lines 1–28 and 30–41, remains `NOT_READY_TO_EXECUTE`. It studies acquisition of
evidence to select **one fixed compute profile**, with common-holdout success,
capped TTVS and total acquisition/evaluation cost. It does not estimate
conditional node-routing benefit. Its dated statement that the v0.4 fields were
TBD is historical to 2026-09-06, not the current freeze state.

Reusable discipline can transfer: lineage splits, immutable settings, verifier
qualification, cost accounting and stop rules. Trial budgets, treatment arms,
success denominators, estimands and readiness do not transfer automatically.
The ZIP's structural checks, paper summaries and skill snapshots are design
assets, not additional evidence that closes v0.4 or starts v1.1.

## Audit verification and handoff

This report changes no protocol, experiment, corpus, access record or runtime.
Validation for integration is limited to relative-link resolution, documentation
checks and whitespace checks. Confirm the three frozen-file hashes above and
the four access-log rows remain unchanged; do not execute locked-test or priced
provider commands as part of this planning audit. The parent closure plan should
link this report from the documentation hub in accordance with the
[documentation guide](../../../governance/documentation.md), lines 19–29.

Checks run in this worktree: all 29 relative report links resolve; the three
frozen hashes and both pins in all three configs match; the access log remains
four rows; `python scripts/check_docs.py` passes for 222 Markdown files and 18
SVGs, including integrity of all 187 imported future-package files;
`git diff --cached --check` passes. These checks establish documentation and
artifact integrity only.
