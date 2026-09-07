# Accretion through v1.8: review, capabilities and diagrams

**Reviewed:** 6 September 2026 · **Design basis:** Revision 4 · **Status:** Forward design, with implementation and research gates outstanding.

Accretion is a workspace for turning a research or development goal into a controlled, verifiable workflow. It coordinates AI agents and tools, checks what they produce, records the evidence and cost, and evaluates improvements before allowing them into future work. The person using it retains authority over objectives, consequential actions, promotion and exact physical trials.

The intended v1.8 product combines software engineering, AI research and simulation with carefully governed physical research. Its practical value is the ability to answer **what ran, why it ran, what it cost, what passed, and what evidence supports the conclusion**.

## Open the diagrams

| View | What it explains | Open / download |
|---|---|---|
| User journey | Goal, scope, workflow review, execution, independent checks, recovery and outputs | [Open](user-journey.html) · [PNG](user-journey.png) · [SVG](user-journey.svg) |
| Cumulative architecture | Control, planning, routing, execution, verification, state, offline improvement, simulation and physical authority | [Interactive architecture](architecture.html) · [PNG](architecture.png) |
| Physical-trial detail | The v1.8 advisory boundary and the inherited preflight, freeze, exact approval, arming, execution and outcome chain | [Open](physical-trial.html) · [PNG](physical-trial.png) · [SVG](physical-trial.svg) |

The architecture is a logical component map. Its boxes do not prescribe twelve separately deployed services. Registry, identity and tool-policy responsibilities are expanded below; observation and read interfaces are summarized rather than drawn from every producer to every consumer.

## 1. What a user could do

| User intention | Intended Accretion behavior | Reviewable output |
|---|---|---|
| Build or fix software | Define acceptance checks, choose compatible agents and tools, run in isolated workspaces and verify the candidate | Code changes, tests, diffs, execution history and a verdict |
| Reproduce or extend a research paper | Turn the question into a bounded protocol, organize implementation and experiments, retain source and result provenance | Research brief, experiment configurations, results, limitations and a reproducibility bundle |
| Compare ways of running AI work | Evaluate compute profiles and harnesses under a shared success standard and complete cost accounting | A comparison showing success, time to verified success, cost and uncertainty |
| Recover from a digital failure | Classify the failure, choose an allowed repair or escalation and reserve the remaining budget | A new bounded attempt with a reason and outcome; unresolved cases pause |
| Reuse prior work | Retrieve relevant, eligible evidence and retain contradictions, source trust and freshness | Traceable context and reusable experience without turning notes into permissions |
| Improve a recurring workflow | Propose harness changes offline, independently evaluate them and request human promotion | Candidate differences, held-out results, promotion decision and rollback target |
| Test transfer to another simulated task | Apply hard compatibility rules, use a weak source prior and require target-specific evidence | A transfer result or target-only fallback, including negative transfer |
| Prepare a supported physical trial | Recommend permitted digital preparation choices before a complete preflight and configuration freeze | An advisory, exact configuration, preflight evidence and a human approval request |

These are cumulative target capabilities. A report or draft manuscript is an artifact for review; its existence does not establish scientific validity or readiness for publication. Experiments and physical trials still require their applicable authorization.

**Example future use:** “Reproduce this robotics method at reduced scale, compare it with the current baseline in simulation, and report whether it improves precision within the agreed budget.” Accretion would structure the protocol, coordinate the work, preserve failed and successful runs, independently evaluate the outputs and prepare the comparison. A later real-arm trial would enter the separate physical approval path. This is an illustrative workflow, not a claim that the project has already reproduced such a method.

## 2. What each release contributes

A **compute profile** is a pinned choice of model/runtime and permitted settings. A **harness** is the surrounding prompts, context handling, tools and bounded execution rules.

| Release | Capability in plain language | Governing limitation |
|---|---|---|
| Through v1.0 | Integrate projects, objective contracts, agents, tools, graphs, independent verification, evidence, simulation and approved physical trials | Each predecessor must earn its own release gates |
| v1.1 | Spend compute where it earns verified results | Compatible choices only; the first study selects a fixed profile and does not prove per-task routing gains |
| v1.2 | Choose a tested harness suited to the task | Immutable promoted bundles; evaluate the complete profile-plus-harness combination |
| v1.3 | Recover more efficiently from eligible digital failures | Existing owners control repair and replan; uncertainty pauses; physical retry is excluded |
| v1.4 | Keep useful, traceable beliefs, progress and experience | The backend validates writes and freshness; learned state cannot grant authority |
| v1.5 | Improve harnesses from historical failures and successes | Offline proposals, independent evaluation, human promotion and rollback |
| v1.6 | Optionally adapt an open-weight model or adapter for repeated digital work | Separate research lab; success is optional; no live weight updates or physical training |
| v1.7 | Test whether efficiency knowledge transfers to a compatible simulated target | Target verification is mandatory; source evidence is a weak prior; negative transfer triggers fallback |
| v1.8 | Reduce the burden of preparing a supported physical trial | Advice ends before freeze; v0.6 retains approval, arming, execution and safety authority |

The v1.6 lab may close with a justified negative, inconclusive or deferred research outcome. That does not promote an adapter or waive operational gates. v1.7 does not require a successful adapted model. At v1.8, optional learned artifacts may be absent; a governed static or target-only baseline remains valid.

## 3. Architecture: responsibilities and boundaries

| Component or plane | Responsibilities and interfaces | Owner / release |
|---|---|---|
| Experiment Studio and chat control | Project setup, objective/workflow review, live progress, approvals, candidate comparisons and evidence inspection. UI commands enter the authoritative backend; snapshots/events return status | v1.0 integration; additional views in each v1.x SDD |
| Project control | Versioned objectives, run/graph/node state, command validation, budgets, risk, tenancy and approval state | Existing control-plane owners |
| Workflow planning and coordination | Choose direct, loop, graph or hybrid execution; propose governed graph changes and coordinate node decisions | v0.8 / v0.9 |
| Node routing | Filter compatibility before ranking; assemble the selected configuration and recheck it before one canonical dispatch | v0.4, extended by v1.1 |
| Harness and artifact registries | Pin signed/promoted compute, harness, model/adapter and policy versions; expose only compatible choices with lineage and rollback | v1.1 / v1.2; inherited promotion governance |
| Runtime and capability integration | Replaceable agent adapters, isolated workspaces/compute/simulator sessions, scoped tools, MCP/API connectors and final execution checks | Existing execution and integration owners |
| Identity and policy | Identity/role checks, separate external connections, capability policy and token brokerage. Installation requests capability; it does not grant permission. Raw credentials stay outside model-visible state | v1.0 capability, integration and identity plane |
| Independent verification | Apply the predefined acceptance specification to immutable outputs; prefer executable evidence; preserve contradictions and unresolved judgments | Existing verifier and review owners |
| Evidence and observability | Append-only events and outcomes; artifacts, costs, environment snapshots, provenance, incidents, reproducible exports and authoritative read projections | Existing evidence/storage owners |
| Guarded state | Materialize scoped belief/progress/experience views; deterministically validate proposed writes; serialize source eligibility with consumption and revocation | v1.4 extends existing state owners |
| Bounded recovery | Resolve failure ownership, rank compatible interventions and request a budgeted attempt from the existing controller; structural changes return to the planner | v1.3 |
| Offline improvement | Eligible traces feed bounded harness search; optional adapter experiments run separately. Independent holdouts, human promotion, canary and rollback precede use | v1.5 / optional v1.6 under v0.10 governance |
| Simulation and transfer | Embodiment/environment signatures, simulator snapshots, hard transfer compatibility, target evaluation and conservative fallback | v0.5 / v0.7 plus v1.7 |
| Physical preparation | Eligibility, finite candidate construction, advisory ranking, shadow/simulation evaluation, independent preflight, freeze binding and outcome/drift recording | v1.8 |
| Physical execution and safety | Exact approval checks, atomic consumption/arming, bound leases, controller/driver, watchdog, safety supervisor, independent stop and incident response | Sole v0.6 authority |

The observed repository foundation uses a React operator interface, a FastAPI backend, PostgreSQL state, resumable server-sent events and replaceable agent adapters. These technologies are recorded in the current repository documentation; the diagrams do not assert a deployed v1.8 topology or infer a particular cloud or GPU fleet.

**The central runtime loop:** approved objective → governed graph → compatible node configuration → isolated execution → independent verification → evidence and outcome. An eligible digital failure may request bounded recovery through the existing owner. Evidence supports status views and later offline improvement; it cannot authorize its own execution or promotion.

**The improvement loop:** eligible traces → bounded proposal/training → independent evaluation → human promotion → immutable registry → later compatible selection. Training and proposal processes cannot approve their own artifacts. The current candidate and hidden final evaluation data stay separate.

**The physical chain:** configuration manifest → exact trial contract/task parameters → independent preflight → immutable freeze audit receipt → existing human approval of trial and preflight hashes → existing gateway consumption/arming → deterministic execution → separate task and safety outcomes. Material changes require a new chain. The audit receipt itself grants no authority.

## 4. Review findings

**Assessment:** Revision 4 is a coherent forward design with explicit ownership and evidence boundaries. It is suitable as a design reference for staged engineering and research. It is not an implementation-ready v1.8 release or proof that the proposed optimizers work. The items below are entry and integration gaps; they are not observed production failures.

| Priority | Finding and implication | What closes it |
|---|---|---|
| Before any v1.x claim | The real project remains on the v0.4 development line. The v1.0 foundation and v1.1–v1.8 release gates have not been established by this review | Reconcile each predecessor against actual runtime and release evidence; retain the roadmap as planned |
| Before the first v1.1 study | The readiness document explicitly lacks an eligible trace inventory, three pinned profiles, effective-setting observations, qualified verifier, frozen numerical protocol and complete budget | Build the read-only readiness inventory, then freeze a concrete study with accountable people and independent evaluation |
| Before runtime integration | The 24 schemas are targeted projections. Valid synthetic references do not demonstrate real artifact resolution, signatures, scopes, canonical hashes or distributed atomicity | Implement owner adapters and meaningful integration/race tests. In particular, prove v1.4 consumption versus revocation and v1.8 invalidation versus arming at the existing transaction owner |
| Before physical integration | v1.8 depends on a supported task schema carrying the configuration-manifest binding. Section 10.4 explicitly blocks P5/P7 if that mapping cannot be validated | Review the exact task schema, digest mapping, loaded-configuration comparison, gateway transaction and changed-input rejection tests |
| Before efficiency or safety claims | Success floors, uncertainty scope, population, sample size and cost basis still require actual data. Marginal prediction coverage cannot establish per-scenario safety or an unsupported conditional success bound | Qualify the verifier; preregister estimands, data roles and analysis; freeze selection before calibration/final evaluation; report uncertainty and failures truthfully |
| Before physical experiments | The first robot/cell/task profile is proposed, not qualified. A six-degree-of-freedom arm and webcam are not sufficient evidence of readiness | Select and qualify one exact cell, sensors, frames, units, calibration, controller, stop path, safety envelope and named roles; then authorize each exact trial |
| Planning quality | The roadmap contains several research programs whose benefit may not materialize | Begin with the fixed-profile study, retain simple baselines, and use evidence triggers before adding optimizer or adaptation complexity |

The design's strongest choices should be preserved: hard compatibility before learned ranking; independent acceptance; complete cost and non-success accounting; immutable provenance; separated selection/calibration/final data; optional open-weight adaptation; target-specific transfer evidence; and a physical advisory that cannot control or retry a trial.

The current repository backlog also contains stale status text: it says M7 is “not started,” while the inspected HEAD commit records M7 closure. This review uses the newer local commit as the checkpoint and does not turn that commit message into an independent release audit. The Revision-4 README's older commit references remain historical snapshots.

## 5. What was verified in this review

- Read the Revision-4 release purposes, constraints, shared integration decisions, contract reference, pilot readiness and detailed v1.8 lifecycle, contracts, rules, acceptance and open questions; read the predecessor v1.0 product/plane architecture.
- Inspected the repository read-only at clean `develop@84c3eede99c171e1f13fa37a19d15fa978e0830d`, equal to its locally recorded origin reference at the checkpoint. No fetch or complete release audit was performed.
- Re-ran the current document validator: **PASS, document-design conformance only** — 24 targeted schemas, 183 synthetic cases, 101 event names, 174 acceptance rows, 8 SDDs and 16 checked local links.
- Kept all implementation/research acceptance evidence pending. No application tests, paid calls, training, deployment or physical trials were run. The frozen SDD package was not amended.

This is an assistant design review of the cited material, not the independent methods, architecture, security or physical-safety approval required by the SDD. Synthetic conformance, this review, runtime acceptance and scientific results are separate evidence classes.

## 6. Recommended next steps

1. **Reconcile the current implementation.** Update the working status inventory from current commits and existing milestone evidence. Keep v1.x design work separate from released capability claims.
2. **Produce the v1.1 readiness inventory.** Count eligible traces and missing fields; identify the three genuinely supported profiles; inspect independent verifier quality, effective settings, timing and cost visibility. This can begin read-only.
3. **Freeze one reviewable protocol.** Choose the population, holdouts, success floor, horizon, sample/budget justification, baselines, analysis and accountable reviewers. The initial fixed-profile selection study does not establish conditional routing benefits.
4. **Review the owner integrations before implementation.** Resolve exact schemas, authority checks and the state/dispatch transaction boundaries needed for that bounded slice. Request separate authority for new execution only after the study is concrete.
5. **Advance on evidence.** Study harness, recovery and state improvements before optional adaptation and transfer. Treat v1.8 physical preparation as a later gated program with its own qualified cell and trial approvals.

## 7. Source map

All capability statements above describe the supplied design or inspected local documentation. No fresh literature search or paper reproduction was performed for this review.

| Claim or review area | Primary local source |
|---|---|
| Product purpose and six architectural planes | [v1.0 SDD, §§1–8](../company/apps/Accretion/docs/sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v1.0.md) |
| Current implementation and stack | [Repository README](../company/apps/Accretion/README.md) and local git checkpoint |
| Current roadmap-status drift | [v0.4 backlog](../company/apps/Accretion/docs/releases/v0.4/backlog.md) |
| Revision-4 status and release map | [Package entry document](../Accretion_v1x_Technical_SDD_Revision_4/00_READ_ME_FIRST.md) |
| Cross-release authority | [Contract registry](../Accretion_v1x_Technical_SDD_Revision_4/02_CROSS_RELEASE_CONTRACT_REGISTRY_v1.x.md) |
| v1.1–v1.4 runtime efficiency | [v1.1](../Accretion_v1x_Technical_SDD_Revision_4/04_Accretion_SDD_v1.1.0.md) · [v1.2](../Accretion_v1x_Technical_SDD_Revision_4/05_Accretion_SDD_v1.2.0.md) · [v1.3](../Accretion_v1x_Technical_SDD_Revision_4/06_Accretion_SDD_v1.3.0.md) · [v1.4](../Accretion_v1x_Technical_SDD_Revision_4/07_Accretion_SDD_v1.4.0.md) |
| v1.5–v1.7 improvement and transfer | [v1.5](../Accretion_v1x_Technical_SDD_Revision_4/08_Accretion_SDD_v1.5.0.md) · [v1.6](../Accretion_v1x_Technical_SDD_Revision_4/09_Accretion_SDD_v1.6.0.md) · [v1.7](../Accretion_v1x_Technical_SDD_Revision_4/10_Accretion_SDD_v1.7.0.md) |
| Physical authority and unresolved binding | [v1.8 SDD, §§3, 7–10, 18–22](../Accretion_v1x_Technical_SDD_Revision_4/11_Accretion_SDD_v1.8.0.md) |
| Readiness and first study | [v1.1 protocol, §§1–9](../Accretion_v1x_Technical_SDD_Revision_4/24_V1_1_PILOT_READINESS_AND_PROTOCOL.md) |
| Data separation, uncertainty and atomicity | [Shared protocol](../Accretion_v1x_Technical_SDD_Revision_4/03_SHARED_RESEARCH_EVALUATION_PROTOCOL.md) · [Revision-4 ADRs](../Accretion_v1x_Technical_SDD_Revision_4/28_REVISION_4_INTEGRATION_AND_ADRS.md) |
| Validation limits and projected schemas | [Validation report](../Accretion_v1x_Technical_SDD_Revision_4/30_REVISION_4_VALIDATION_REPORT.md) · [Contract reference](../Accretion_v1x_Technical_SDD_Revision_4/32_REVISION_4_CONTRACT_REFERENCE.md) |

See [diagram-validation.md](diagram-validation.md) for artifact hashes, browser/perceptual checks and the disclosed workflow-renderer fallback. Source links point to the original local documents; the review ZIP keeps the diagrams self-contained and does not duplicate the full SDD package or repository.
