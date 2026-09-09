# v0.5 acceptance, benchmark and release plan

Prepared 9 September 2026. **Planning only; no v0.5 implementation, simulator
execution, evaluator run or research outcome is established by this audit.**
Inspected base: `01e2268b3b602a12eeaf81f55fb4670a1fe7636f` on
`docs/v05-evidence-plan-20260909` in its own worktree. The user requested a
multi-agent completion plan; this report is the acceptance and evidence lane.

## Scope and source authority

The [forward v0.5 SDD](../../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
defines a simulation-only Robotics and Embodiment Foundation. Its SHA-256 at
this base is
`90d8ca2d909288f76d619f6db908e0fc87d8d0b286af9e1900361d3a0a3b2e02`.
All 30 checkboxes in §22 are mapped below, in source order: six groups of five.
The IDs are proposals, not IDs already present in the frozen source. No imported
package file was edited.

The [v0.4 handoff](../../v0.4/research-handoff-2026-09-08.md) records the adopted
full-routing-claim **NO-GO** and the five satisfied inherited technical entry
conditions. The [closure execution](../../v0.4/closure-execution-2026-09-08.md)
records subsequent integration and validation. Those are dated v0.4 evidence,
not simulation conformance, positive routing-benefit evidence or permission to
run this future study. Preserve the original v0.4 protocol, amendment, results
and four-row access log. This audit read the handoff and closure records rather
than reopening their locked data or rerunning an evaluator.

Use M0–M9 for the ten numbered implementation milestones in SDD §21:

| Milestone | SDD §21 item | Evidence responsibility |
| --- | --- | --- |
| M0 | 1. Contract and simulator/ROS freeze | Active normative mapping, ADRs, protocol and gate definitions |
| M1 | 2. Registry and adapter SDK | Schema/observation validation, manifest authority, conformance harness |
| M2 | 3. Gateway and leases | Simulator-only endpoints, immutable environment pins, lease isolation |
| M3 | 4. Deterministic safety | Every-intent admission and denial witnesses |
| M4 | 5. Episodes and recorder | Durable lifecycle, limits, uncertainty, complete capture and events |
| M5 | 6. Independent verification and replay | Independent acceptance, contradiction, experience and replay rules |
| M6 | 7. Flagship adapter | First real simulator-backed adapter and task fixtures |
| M7 | 8. Independent second adapter | Second real conformance report and portable experiment binding |
| M8 | 9. Experiment Studio | Backend-derived user journey and browser evidence |
| M9 | 10. Benchmark and release audit | Paired study, claim decision, combined release evidence |

## Complete §22 witness matrix

Keep `AC5-001` through `AC5-030` stable when ownership changes. Store milestone
ownership separately; do not embed the current owner in the criterion ID.
In the future active SDD, each checkbox becomes one required criterion with
its original text and a source-section pointer. A criterion is complete only
when **every** required witness component has evidence.

Witness classes: **A** is an automated contract, integration or fault test;
**S** is actual simulator execution/conformance, with automated checking of its
recorded output; **U** is frontend plus production-browser evidence; **B** is a
registered scientific benchmark and decision. A manifest, stub, screenshot or
passing report-parser test cannot stand in for S or B. None of the proposed
witnesses below has been executed in this planning lane.

### §22.1 Contracts and authority

| ID | Exact criterion | Owner | Required witness |
| --- | --- | --- | --- |
| AC5-001 | All contracts have immutable IDs, versions, canonical hashes, and schema tests. | M0 | A: all ten §8 contract types round-trip canonical JSON, preserve compatible optional fields, reject hash tampering and mutation; immutable registry writes and generated schema fixtures agree. |
| AC5-002 | No v0.5 capability can resolve to a physical endpoint. | M2 | A: enumerate all six simulation capabilities and resolver routes; physical namespace, hardware transport, endpoint alias and lease substitution attempts are denied before invocation. Include a positive simulated execution so an empty registry cannot satisfy the test. |
| AC5-003 | Adapter manifests cannot grant capabilities. | M1 | A: register a manifest requesting an ungranted action; registration does not authorize it, invocation is denied and transport sees zero calls. Cover runtime and plugin invocation paths. |
| AC5-004 | Unknown major contract versions fail closed. | M0 | A: unknown majors are rejected at registration, API ingestion, event/artifact loading and replay; compatible minor forwarding retains optional fields and hashes correctly. |
| AC5-005 | Active episodes remain pinned to admitted contract hashes. | M4 | A: while an episode runs, change aliases, descriptor, envelope, verifier and adapter versions; original immutable pins persist, changed inputs cannot mutate the episode, and restart preserves the same identities. |

### §22.2 Adapter and simulator

| ID | Exact criterion | Owner | Required witness |
| --- | --- | --- | --- |
| AC5-006 | At least two adapter implementations or materially distinct versions pass conformance. | M7 | A+S: two independently identified artifacts run the full §5.8 suite against their declared simulator environments, producing digest-bound reports. M6 supplies the first report; M7 supplies the second and a reviewed material-distinction record. Two labels around one fake implementation do not count. |
| AC5-007 | Reset, timeout, duplicate, out-of-order, and crash tests pass. | M7 | A+S: the five named cases, plus unknown command, heartbeat, deadline, monotonic clock and artifact/replay cases from §5.8, run for both adapters. Perturb actual host/transport state; prove no duplicate effect and deterministic shutdown. |
| AC5-008 | Simulator images and worlds use immutable digests. | M2 | A+S: preflight resolves and records image, world, model, controller and adapter digests; mutable tags and substituted artifacts are refused. An executed episode records the exact installed environment. |
| AC5-009 | Seed and randomization capture is complete. | M4 | A+S: each executed trial records all generator seeds, distribution version, sampled values and simulator settings; missing randomization data blocks acceptance/replay rather than receiving invented defaults. |
| AC5-010 | Observation unit, frame, shape, and time validation is enforced. | M1 | A+S: inject invalid units, frames, dimensions, non-finite values, skew and clock regression through each adapter. Valid observations pass; incompatible observations cannot reach task acceptance. |

### §22.3 Safety and execution

| ID | Exact criterion | Owner | Required witness |
| --- | --- | --- | --- |
| AC5-011 | Every action intent is checked before adapter invocation. | M3 | A+S: count proposed, evaluated, admitted and invoked intents; each actual command has one prior valid decision and denied intents have zero effect. Check bypass paths, reconnects and per-action envelope pins. |
| AC5-012 | Joint, velocity, acceleration, workspace, and episode caps fail closed. | M3 | A+S: valid boundary actions succeed; each named cap, cumulative motion, forbidden volume, collision, effort and tool/payload rule independently refuses or terminates. Include invalid numeric values and a trajectory whose endpoint is legal but path is forbidden. |
| AC5-013 | Uncertain acknowledgement aborts instead of replaying a command. | M4 | A+S: lose acknowledgement after simulated application, crash the host/orchestrator and restart; retain uncertainty, abort the old episode, never resend into it, and require reset plus a new episode ID for recovery. |
| AC5-014 | Hard caps terminate every automatic loop. | M4 | A+S: inventory action, perception/planning retry, no-action wait, repair, replay and recovery loops; force non-progress and prove each ends within its admitted count/time/resource limits with a durable terminal reason. |
| AC5-015 | Safety evaluator denial cannot be overridden by a runtime or plugin. | M3 | A: direct gateway calls, forged receipts, unsafe manifest requests, stale approvals and model/plugin requests cannot replace a denial; assert zero downstream invocation and an attributable refusal. |

### §22.4 Verification and evidence

| ID | Exact criterion | Owner | Required witness |
| --- | --- | --- | --- |
| AC5-016 | Every accepted episode passes independent task, safety, and completeness checks. | M5 | A+S: verifier process/identity differs from producer; require all three results bound to frozen evidence. Remove, corrupt or contradict each required result; no case becomes ACCEPTED. Qualify task truth with positive and adversarial negative episodes. |
| AC5-017 | `INCONCLUSIVE` reaches human review when unresolved. | M5 | A+U: material model disagreement and insufficient deterministic evidence request bounded additional evidence then enter HUMAN_REVIEW. Restart/replay retains it; the UI shows the reason and cannot self-approve the producer's output. |
| AC5-018 | Contradictory evidence is preserved and dependents are discoverable. | M5 | A: append contradiction, retain the original accepted record, quarantine its experience and find dependent episodes/training snapshots. Deny reuse until recorded resolution; review rollback and owner-notification paths without sending external messages in tests. |
| AC5-019 | Replay class is declared and verified. | M5 | A+S+B: missing class/tolerance is refused; run actual replay at the admitted class and compare frozen evidence. Exact, tolerant and statistical paths have distinct validators and reports; study replay is assessed at its registered class without post-hoc tolerance changes. |
| AC5-020 | Simulation evidence cannot be exported as physical evidence. | M5 | A+U: preserve mandatory SIMULATION labels through store, replay, API, UI and export. Forged physical labels or untyped imports fail; exported manifests and visible download summaries retain provenance. |

### §22.5 Experience and benchmark

| ID | Exact criterion | Owner | Required witness |
| --- | --- | --- | --- |
| AC5-021 | Only verified, complete, contradiction-free episodes become eligible experience. | M5 | A: independently remove PASS, required artifacts/hashes, verifier versions, resolved-contradiction status or retention permission; eligibility and retrieval deny each case. Quarantine invalidates dependent retrieval after restart. |
| AC5-022 | Different-embodiment experience is a weak prior only. | M5 | A: retrieve a compatible weak prior with provenance, but changing that prior cannot change live action selection. No direct trajectory/action reuse, safety change or transfer claim follows; compare actual admitted actions, not just a display label. |
| AC5-023 | Benchmark splits, seeds, metrics, and analyses are pre-registered. | M0 | A+B: complete separate protocol, study ID, role-separated inventory, frozen hashes and attributable adoption before confirmatory data access; M9 checks timestamps/access records and exact protocol-to-result identity. A protocol-schema test alone proves only preparation. |
| AC5-024 | False acceptance and safety non-regression gates pass. | M9 | B: registered paired analysis includes false acceptance, safety and the verified-success non-regression condition in §20.4, for each required adapter/task scope. Compute actual denominators and uncertainty; incomplete, failed or underpowered gates cannot be waived into success. |
| AC5-025 | Ablations isolate routing and experience effects. | M9 | A+B: demonstrate non-inert static, routed-without-experience and routed-with-compatible-experience treatments with the same eligible configuration set, resources, verifier and trial matrix; execute and report the pre-registered contrasts against the simulator-specific baseline. |

### §22.6 Product and operations

| ID | Exact criterion | Owner | Required witness |
| --- | --- | --- | --- |
| AC5-026 | Experiment Studio renders backend-derived episode state. | M8 | A+U: generate fixtures from real backend responses; browser-create/preflight/run/reject/review/replay/export episodes and observe durable state after reconnect. Canvas interactions never issue unauthorized topology, envelope or capability changes. Include visible populated states so an empty panel cannot pass. |
| AC5-027 | APIs enforce idempotency and optimistic concurrency. | M4 | A: all eleven §15 routes have method/auth/scope tests; each applicable write rejects missing/stale version headers and conflicting idempotency payloads. Concurrent identical starts create one episode; store parity and separate-client PostgreSQL contention prove durable behavior. |
| AC5-028 | Event replay reconstructs episode state. | M4 | A+U: rebuild the episode from its persisted ordered events, compare authoritative state, and exercise duplicates, gaps, stale snapshots and terminal replay. All twelve §16 event types use the inherited envelope; sensor payloads stay in digest-addressed artifacts. |
| AC5-029 | Adapter and simulator failures produce actionable typed errors. | M4 | A+S+U: actual crash, heartbeat loss, clock regression, skew, unavailable artifact store and uncertain acknowledgement yield stable code, scope, terminal outcome and safe recovery guidance, without secrets. UI shows these errors without claiming completion. |
| AC5-030 | Security tests cover adapter escape, payload limits, and endpoint confusion. | M2 | A+S: hostile adapter fixture attempts host filesystem/process/network escape; reject oversized sensor/decompression payloads, schema smuggling and endpoint/lease substitution at the real boundary. Require an allowed positive path and verified isolation controls, not only a manifest declaration. |

These 30 rows cover release acceptance, but do not replace the SDD's other
mandatory behavior. M0 must attach the ten preflight checks (§11), complete
conformance suite (§5.8), ten core contracts (§8), six capabilities (§9), eleven
API routes (§15), twelve event types (§16), persistence constraints (§17) and
security/failure inventory (§18) to their owning witness groups. The example
`tests_total: 48` in §8.10 is an illustrative manifest value, not an independently
established required suite size or evidence that 48 tests already exist.

## Acceptance and release tooling gaps

The current [acceptance loader](../../../../src/accretion/acceptance.py)
names only v0.1–v0.4 in `SDDS`. Its row pattern accepts V01/V02/AC3/AC4 table
IDs, not the v0.5 checkboxes or proposed AC5 IDs. `stage_of()` knows no v0.5
stage. The [policy](../../../acceptance/criteria.toml) likewise contains no
v0.5 disposition. Consequently, today's green acceptance gate makes **no v0.5
coverage claim**. Merely appending the future SDD path would still load zero
v0.5 rows.

M0 should perform one reviewed active-SDD promotion and gate change, while
leaving the imported 187-file package byte-frozen:

1. Create the active v0.5 baseline through the SDD governance process, preserve
   §22 text/source order, assign the 30 proposed stable IDs and explicit MUST
   priority, and add a separate release-prefixed owner stage such as `v0.5-M3`.
   Do not parse checkbox ordinal numbers as permanently implicit identity.
2. Extend the loader's release and ID support, explicit owner mapping and
   source tracking. Reject duplicate IDs instead of silently overwriting a
   dictionary entry; reject malformed expected rows, unknown owners, absent
   acceptance sections, zero-release coverage and omitted source criteria.
   Assert the exact original 30-ID/text mapping and all six category counts;
   any later approved addition changes the versioned mapping deliberately.
3. Keep milestone deferrals visible with named owners and reasons while work is
   pending. A release-candidate mode must reject remaining `not_yet_due` rows
   for v0.5 and cannot count them as completed. The existing scoped CLI rejects
   an empty selected stage; add explicit release-wide/nonempty evidence checks
   rather than relying on that narrower guard.
4. Represent composite witnesses explicitly. Keep Python marker claims and
   machine-checked frontend anchors, but add simulator/browser/benchmark result
   provenance or a dedicated v0.5 evidence manifest and release auditor. Do not
   classify a parser unit test as proof of an actual simulator study. A missing,
   skipped, stale, wrong-digest, empty or failed required component fails its
   corresponding gate. Pin candidate tree, environment, command exit and
   evidence hash; require both adapters and every predeclared trial cell.
5. Harden external/manual evidence validation for new v0.5 obligations: current
   `manual` policy only requires nonempty evidence/date fields and date freshness;
   it does not verify that the file exists or proves the claimed execution.
   New records need an existing artifact, scope/digest match, actual result and
   valid non-future timestamp. An updated date cannot renew unchanged invalid
   evidence. Do not change old historical results while adding these checks.
6. Preserve existing anti-vacuity behavior: claimed setup/teardown failure,
   absent test outcome and skip-only proof cannot pass; a failing claiming test
   outranks a manual record or waiver. Preserve the standalone full pytest CI
   step, because the acceptance harness does not fail on every unclaimed test.

The current [release gate](../../../../scripts/release_gate.py) still evaluates
the inherited five §24.8 conditions: acceptance, secret exposure, capability
bypass, connection isolation and v0.1/v0.2 regression. Its existing guard against
empty suite lists is valuable, but these five conditions cannot certify a
simulation release. Keep them and add explicit v0.5 conditions for actual
two-adapter conformance, simulator-only isolation, independent verification,
replay/evidence integrity, scientific claim decision and full inherited
v0.1–v0.4 nonregression. Do not silently reinterpret the five old labels.

The [CI workflow](../../../../.github/workflows/ci.yml) currently has backend,
frontend, clean-checkout and production-browser jobs. Extend it with a pinned,
headless simulator conformance job or an equivalently required verified artifact
gate. Hosted-runner suitability, images, network isolation and CPU/storage quotas
must be resolved before making this required. Offline mocks remain fast tests;
they cannot be the sole release evidence for an installed simulator adapter.
Confirmatory science should validate frozen result artifacts in ordinary CI;
re-running a locked study on every pull request would create uncontrolled reads
and is not the proposed workflow.

## Bounded §20 benchmark proposal

Create a distinct v0.5 protocol and access history. Do not reuse the v0.4 locked
corpus, rename synthetic provider evidence as simulation evidence, or inherit
its numerical false-acceptance ceiling as a v0.5 standard. All numbers and
statistical choices below are **proposals for M0 registration**, not approved
thresholds, power guarantees or execution allowances.

### Treatments, task matrix and independence

| Treatment | Controlled difference | Required evidence |
| --- | --- | --- |
| B0 | Direct simulator-specific script | Same target, seed/randomization, controller, resources and independent external evaluator; record its own behavior, including failures. Use a corresponding direct baseline for each adapter/environment stratum. |
| B1 | Accretion, one selected adapter, static workflow | Same prescribed solution/configuration as B0 where possible, routed through Accretion's contracts, gateway and evidence path. |
| B2 | Accretion routing, retrieved experience disabled | Same eligible configurations and budgets as B1/B3, with the registered router selecting configurations; prove actual receipt/execution identity and a non-inert selection opportunity. |
| B3 | Accretion routing plus compatible verified experience | Same B2 policy/configuration universe, with a frozen eligible development-only experience snapshot. Attribute every retrieval and exclude contradictions. |

Each treatment runs all three task families: target-pose reach; pick and place
one rigid object; and recovery from one **predeclared** perception or planning
failure. Pair on task instance, adapter/embodiment stratum, world, controller,
seed, randomization sample and failure injection. Randomize/counterbalance
treatment order, reset between episodes and prevent prior-run simulator state
from becoming an unrecorded treatment. A recovery task needs the injected-failure
time/type, allowed response and independent success/termination definition; a
silent extra retry is not successful recovery.

Use two actual conforming adapters. §1 asks for binding one high-level experiment
to more than one embodiment, whereas §20.4/§22.2 permit adapter implementations
or materially distinct versions. Resolve this in M0: the stronger proposal is
an independent adapter for a second materially distinct simulated embodiment,
with shared task semantics and immutable descriptor differences. If a same-robot
second adapter is selected, record exactly which narrower claim it supports and
resolve the §1 discrepancy before coding. Neither option establishes learned
cross-embodiment policy transfer, which is excluded in v0.5.

The baseline and producing runtime must not set their own acceptance labels.
Pin separately authored task, safety, completeness and replay verifiers to a
separate process/identity with read-only frozen episode evidence. Qualify them
on obvious success, obvious failure, near-threshold, tampered, missing-sensor,
wrong-frame and reward-hacking cases before final evaluation. Use simulator
ground truth for task/safety metrics where available; disagreement becomes
INCONCLUSIVE and counts in reporting. Optional model judgment remains secondary
and cannot turn deterministic failure into PASS.

### Splits, budgets and treatment qualification

Development, calibration/qualification and locked evaluation must have disjoint
task-instance identities and seed sets, frozen with their randomization
distributions. Qualify code, adapters, verifiers and the analysis on the first
two splits. Build B3 experience only from eligible development episodes and
freeze it before evaluation. Do not update it between held-out trials. Data
roles and access logging must prevent evaluated cases or target labels leaking
back into the router, producer, tolerance tuning or experience store.

A concrete initial capacity proposal is three development seeds, five
qualification seeds and a ceiling of thirty locked seeds per task family.
With three task families, two adapter strata and four treatments, this is
72 + 120 + 720 = **912 primary episode attempts**, plus at most one separately
recorded replay per attempt, for a global ceiling of **1,824 episode executions**.
This is a sizing envelope, **not** a sample-size justification. Final locked N
must follow a paired power/precision analysis using only the qualification
split and the registered success/safety margins. If adequate precision cannot
fit the ceiling, revise the protocol/budget prospectively or conclude the study
cannot establish the full claim within this scope.

For review, propose at most two simulator workers, 120 simulated seconds,
180 wall-clock seconds and 300 action intents per episode, plus 48 elapsed hours
and 96 measured CPU hours for the campaign, with a separately frozen sensor
storage/output ceiling. The first exhausted bound stops execution and preserves
the incomplete disposition; the outer budget is not a promise that the full
matrix will finish. These per-episode example limits require task feasibility
checks during development and explicit freezing; their appearance in SDD
examples does not already approve them. Resource ownership, host capacity and
artifact storage must be resolved before any launch.

Prefer deterministic local proposers for this foundation study, with an
external-provider call/money allowance of zero. This can test the substrate's
portability and governance while preserving the limited claim. M0 must decide
how B2/B3 obtain a meaningful eligible routing configuration set: today's
FAKE-only `BASELINE_ONLY` catalog must not be treated as a demonstrated routing
ablation. No learned AUTO activation or selected-tool AGENT support is implied.
If actual model/runtime comparison is essential, replace the zero-call scope
with a separately reviewed provider/model inventory, independent outcome path
and explicit call/money/latency caps before execution.

Count failed, denied, timed-out, aborted and inconclusive attempts in the ledger
and registered intention-to-treat denominator. Do not refill hard cases with
new seeds. Any permitted infrastructure rerun must be specified in advance,
retain both attempts, use a fresh episode/reset after uncertainty and consume
the same global budget. Scientific reruns after outcome inspection require an
amendment/new study with a fresh eligible locked set; never overwrite the first
decision.

### Metrics and decision rules to freeze

| Endpoint | Proposed analysis and required registration decision |
| --- | --- |
| Verified task success | Paired per-task/per-adapter contrasts with B0; report denominators, effect and confidence interval. Freeze the non-inferiority margin, confidence level, multiplicity handling and primary treatment before looking at locked outcomes. No approved numerical margin exists yet; an unexplained positive margin cannot mean “no regression.” |
| False acceptance | Independently adjudicated wrong episodes that the system accepted; report count, rate, denominator and interval. Proposal: zero observed false acceptances for full GO, alongside a predeclared uncertainty bound. A zero count alone does not prove zero risk; required sample size and the allowed risk bound remain M0 decisions. |
| Safety | Report commanded-limit breaches, actual simulator violations and system-denied proposals separately. A denial is not an unsafe executed action. Proposal: zero executed envelope violations plus registered paired nonregression against B0; freeze treatment of baseline violations and inconclusive safety evidence. |
| Replay | Default proposal is tolerant replay under an exact environment/seed manifest, with per-state/metric tolerances set from development/calibration and task semantics before record freeze. Exact means bitwise normalized trace equality only where demonstrated. Statistical replay needs its own frozen seed distribution, bounds and analysis; it cannot rescue a failed tolerant claim after inspection. |
| Evidence completeness | Require all mandatory manifest fields/artifacts and receipt joins for every claimed verified episode; publish incomplete attempts rather than dropping them. |
| Failure recovery | Verify the declared recovery without forbidden replay or caps bypass, and distinguish legitimate bounded repair from failure masking. |
| Efficiency and effort | Record elapsed/simulated/CPU time, episode/action counts, sensor bytes and all retry/verification overhead. Log adapter integration effort prospectively under a fixed definition; do not infer person-hours from Git timestamps. Report monetary cost as unavailable or zero external calls only when the ledger supports it. |
| Ablations | Predeclare B2–B1 and B3–B2 contrasts and whether primary, secondary or exploratory. Hold budgets, verifier, configurations and data eligibility constant. A null/negative effect is a reportable result, not an invitation to search for a winning subset. |

Record GO only when all registered joint conditions pass, both adapters remain
conformant and each required stratum is complete. Record NO-GO for a decisive
failed safety/false-acceptance/success gate, or INCONCLUSIVE for unresolved or
insufficient evidence under the registered rule. Halt on a critical escape,
physical-endpoint resolution, unauthorized invocation or incorrect acceptance;
quarantine affected evidence and dependents and retain the incident. No average
success gain compensates for a safety gate failure.

## Meaning of completion and release

| State | Required evidence | What may be said |
| --- | --- | --- |
| Engineering feature-complete | M0–M8 implementation, all A/S/U witness components, real two-adapter conformance, frozen study preparation, migration/security/replay checks and inherited regression gates pass on a clean candidate; B components are shown separately as pending where applicable | The simulation feature set is implemented and verified within its engineering scope. This is not all 30 criteria complete and not a research-qualified release. |
| Research-qualified | All 30 criteria and §20.4's registered paired claim gate pass, with independently reproducible benchmark artifacts and no unresolved critical safety/false-acceptance incident | An embodiment-neutral simulation foundation within the exact tested adapter/task/environment scope. No physical safety, physical actuation or learned transfer claim follows. |
| Released v0.5.0 | Research-qualified under the unamended SDD, plus exact candidate release audit, current required CI, version/lock/schema consistency, clean-checkout reproduction, protected promotion to `main` and observed tag/release identity | v0.5.0 is released; create `baseline.md` only after the real tag exists. A merge to `develop` alone remains integration. |

The inherited v0.4 NO-GO is an explicit permitted entry alternative in v0.5
§2.4. It is **not** permission to waive v0.5's own benchmark or §22 requirements.
If v0.5 research is NO-GO/INCONCLUSIVE, preserve the engineering work and publish
the scoped result/preview status; do not call the full v0.5 program complete or
unlock v0.6. A different release scope would require a separately attributable
normative decision that preserves the failed original claim, not a retroactive
edit to make the existing gate green. No v0.5 outcome authorizes physical work.

## Prerequisite decisions and shared-file ownership

Before implementation, M0 must settle simulator/middleware and immutable image
availability; first/second embodiments and the §1/§20 interpretation; controller,
action abstraction and units; replay classes/tolerances; sensor storage and
retention; simulator-only isolation/lease endpoints; verifier identity and
qualification; approval/cap rules; protocol splits, sample justification,
nonregression margins and joint decision rule; local compute/storage ownership;
and the frozen routing configuration set. The §23 Gazebo/ROS choices are
proposed defaults, not already selected dependencies. Verify current official
technical compatibility during that ADR work before installation or coding.

The coordinator owns active SDD promotion, acceptance ID/owner mapping,
`src/accretion/acceptance.py`, `docs/acceptance/criteria.toml`, release-gate and CI
integration. One assigned worker at a time owns each contract registry/export,
database migration chain, central store/service, route/event envelope and
dependency lockfile. These are shared integration surfaces; parallel branches
must not allocate migrations or regenerate schemas independently against
different bases. Frontend evidence anchors and generated API types update with
the exact merged backend contract, not speculative fixtures.

The benchmark/verifier owner must remain independent of adapter/producer
implementation decisions and freeze the evaluator before locked execution.
Separate worktrees require their own editable Python imports, disposable
databases, simulator ports/leases and artifact namespaces. Run migrations
serially; combine the final candidate once and record exact commits and command
exits. Integrate bounded PRs into protected `develop` before preparing a release;
follow the [branch policy](../../../governance/branch-policy.md) for promotion.

This planning lane changes only this report. The coordinator adds its entry to
the documentation hub in the combined planning change. Local validation matched
all 30 criterion texts exactly to §22, checked unique IDs and local links, and
passed `git diff --cached --check` plus `python scripts/check_docs.py`: 236
Markdown files, 18 SVGs and 187 unchanged frozen-package files. These are static
planning/documentation checks; no app tests, browser checks, simulator
conformance, scientific evaluation, spending, push or merge occurred here.
