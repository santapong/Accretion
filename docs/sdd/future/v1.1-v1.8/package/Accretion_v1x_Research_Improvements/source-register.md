# Primary-source reading register

Accessed 2026-09-06 through alphaXiv. All entries are **selective primary-text review**, not independent reproduction. Page numbers are PDF page numbers. Numeric findings below are author-reported unless explicitly called a local mathematical check. Code inspection and source verification are separate statuses.

## P01 — Prompt-Induced Waste in Coding Agents

[Primary paper, 2608.01347v5](https://www.alphaxiv.org/abs/2608.01347v5). Read pp. 1–2, 6–7, 10–11: setup, interaction model, measurement requirements and limitations.

The controlled core reports 24 deterministic coding tasks, 18 prompt variants, 4,644 valid runs and an eight-task holdout. A separate hard-task campaign should not be pooled with that denominator. Prompt, effort and harness interact; activation and provider accounting matter. Small tasks, success ceilings and missing local-compute/per-call timing constrain transfer. The proposed adaptive controller is not itself an evaluated result.

**Use:** v1.1 activation receipts, controlled prompt/harness/effort factors and difficult-task coverage. **Do not import:** reported savings as an Accretion forecast. **Code status:** not inspected.

## P02 — HarnessOpt-Bench

[Primary paper, 2608.06301v1](https://www.alphaxiv.org/abs/2608.06301v1). Read pp. 2–3, 5–6, 11, 27: execution boundary, algorithm, setup, limitations and reproducibility.

The study reports 111 optimizer runs across four downstream tasks. Development reveals traces, validation reveals aggregate scores, and final tests remain inaccessible during search. Seeds are deliberately untuned; GAIA begins as a stub. Test candidates are scored three times per case. Optimizer inference is metered but uncapped in the reported protocol. Native harnesses do not consistently dominate, and effects depend on model/task.

**Use:** v1.2 mature-baseline controls and v1.5 evaluator isolation. **Do not import:** weak-seed gains, uncapped proposer cost or universal harness rankings. **Code status:** paper-described artifacts only; repository not inspected.

## P03 — Which Agent Causes Task Failures and When?

[Primary paper, 2505.00212v3](https://www.alphaxiv.org/abs/2505.00212v3). Read pp. 1, 4–6, 8: Who & When annotations and attribution evaluation.

The dataset covers failure logs from 127 multi-agent systems. Three experts annotate responsible agents and steps through discussion; initial uncertainty and disagreements are material. Evaluation scores attribution predictions, with and without access to final answers. Agent-level and exact-step accuracy behave differently; the best method for one is not best for the other. A hybrid improves localization while increasing token cost.

**Use:** v1.3 diagnostic baselines, abstention and human-label uncertainty. **Do not import:** annotation agreement as proof that an intervention repairs the execution. **Code status:** no relevant evaluation implementation audited.

## P04 — CausalFlow

[Primary paper, 2605.25338v1](https://www.alphaxiv.org/abs/2605.25338v1). Read pp. 4, 8, 13, 16: intervention definition, ablations, validation and runtime details.

The method changes a step, propagates descendants and scores repairs; execution-backed and predicted validation coexist. Appendix A.5 describes deterministic validation for GSM8K/MBPP, while A.10 says GSM8K used LLM prediction and MBPP execution. Gold-answer access also affects some repair results. Preserve this source inconsistency instead of treating the strongest description as universal.

**Use:** v1.3 typed intervention hypotheses with actual independent replay. **Do not import:** predictive repair outcomes as causal evidence or gold-assisted gains into a gold-blind deployment claim. **Code status:** not inspected; discrepancy remains open.

## P05 — AgentDojo

[Primary paper, 2406.13352v3](https://www.alphaxiv.org/abs/2406.13352v3). Read pp. 6–9, 16, 20: metrics, tests, defenses, interfaces and limitations.

The paper evaluates 97 user tasks and 629 security cases, separating benign utility, utility under attack and targeted attack success. Tool filtering helps for some access patterns but cannot cover attacks using legitimate tools or delayed activation after a future task. Low attack success without utility can reflect an incapable agent rather than a strong defense.

**Use:** v1.4 paired clean/attack cases and explicit outcome denominators. Extend the persistent-memory scenario beyond this paper's tested setting. **Code inspected:** official `TaskSuite` at commit `089ed468cf3ed0322acc66b0211f26d9d90dbf60`; environment injection and versioned task selection are visible. No provider benchmark was run.

## P06 — Verify Smarter, Evolve Further / HarnessLens

[Primary paper, 2608.27311v1](https://www.alphaxiv.org/abs/2608.27311v1). Read §4–6 on pp. 4–8 and appendix control tables on p. 13.

Candidate-specific paired batches include relevant failures and regression probes; a new controller-selected batch confirms promising changes. Four benchmarks use 30 training tasks each and disjoint final tests; one model family drives the roles. Budget units combine model sessions and trials, while headline baseline maxima differ. “No observed regression” is not a zero-risk guarantee, and model-written attribution remains fallible.

**Use:** a later v1.5 behavior-linked development screen. **Do not import:** adaptive selected batches as unbiased final evaluation, cross-unit cost ratios as dollar savings, or autonomous promotion. **Code status:** not inspected.

## P07 — AutoSaddler

[Primary paper, 2608.23041v1](https://www.alphaxiv.org/abs/2608.23041v1). Read pp. 4–5, 10, 17: patch space, algorithm, regression analysis and selection order.

The method diagnoses and patches prompts/tools/middleware on training mini-batches, uses development evaluation for selection, preserves history, and tests the selected candidate once. Its ablation discusses broad hooks that recover cases while breaking others. Reflection and development filtering interact, so their joint ablation should not be interpreted as the isolated benefit of reflection. The setting assumes largely independent, stateless tasks.

**Use:** v1.5 fix/regression accounting and bounded patch hypotheses. **Do not import:** the EvoDAG as a competing authoritative memory store or stateless results as proof of persistent-memory reliability. **Unverified:** complete run counts, compute matching and implementation; no numerical gain is used here.

## P08 — WHALE

[Primary paper, 2609.00196v1](https://www.alphaxiv.org/abs/2609.00196v1). Read pp. 3–6, 9, 15; checked original PDF Algorithms 1/2 at pp. 5/14.

WHALE alternates rejection-sampling SFT and executable harness search with a fixed terminal verifier. Experiments use 2B/4B models in search QA, math and chess; mean@8 is not pass@8. Both algorithms return the pair with highest test accuracy, so reported best-so-far curves do not establish an untouched final selection result. Component-matched budgets do not by themselves show equal total resource cost.

**Use:** an optional v1.6 interaction study with development-only selection. **Code inspected:** commit `fbe125eb7abea7f760c99ab9acc1a6261e708fc6`, `run/lib/alternate.sh` and `docs/reproducing.md`. Docs state eight H200s for paper runs; released cleaned launchers were stub-tested, without end-to-end GPU reproduction. Do not treat the launcher inspection as result reproduction.

## P09 — CoAdapt-GUI

[Primary paper, 2608.11588v1](https://www.alphaxiv.org/abs/2608.11588v1). Read pp. 3, 5–7, 16: transfer schema, matched updates, evaluation and provenance.

App-bound state is separated from transferable workflow entries; target context and policy freeze before evaluation. Novel categories can receive an empty prior. Setting 1 uses 48 held-out task instances, with some baseline numbers imported from prior work; setting 2 uses 105 episodes with disjoint task templates. Context-only and joint arms have comparable interaction limits, but update counts are not identical compute. Some target groups show negative transfer.

**Use:** v1.7 source/target separation and context-only controls. **Do not import:** GUI success rates into robot or engineering forecasts, or adapter updates into v1.7 authority. **Code status:** not inspected.

## P10 — Adaptive Conformal Inference Under Distribution Shift

[Primary paper, 2106.00170v3](https://www.alphaxiv.org/abs/2106.00170v3). Read pp. 3–4, 6–7, 10: update, finite-time bound, stronger assumptions and open problems.

The recursion adapts miscoverage after outcomes arrive. Proposition 4.1 controls long-run empirical coverage with conservative extreme sets; stronger local concentration results add assumptions. This is different from guaranteed conditional coverage for each incoming task. Section 7 explicitly identifies delayed or batched responses as an open extension in this work.

**Use:** shared uncertainty taxonomy and v1.7 forward-monitoring baselines. **Do not import:** time-average coverage as per-decision safety, or an immediate-label theorem into selectively verified delayed agent outcomes. **Code status:** not inspected; mathematical assumptions were selectively reviewed, not all proofs reproduced.

## P11 — A Workflow-Aware Serving Layer / Dyserve

[Primary paper, 2607.02942v1](https://www.alphaxiv.org/abs/2607.02942v1). Read pp. 2, 4, 6, 9: ownership, policy axes, profiling and experimental setup.

The system allocates model/optional quality-policy/backend combinations on a materialized graph and adapts its uncommitted suffix. It uses a self-hosted H200 fleet and throughput/queue information. Four workloads contain 55, 35, 100 and 40 instances. The Sherlock-style comparator is an identified approximation. These conditions differ from hosted APIs and Accretion's mandatory independent verification.

**Use:** v1.8's command-disabled deterministic allocation comparator and latency sensitivity. **Reject direct transfer:** optional-verifier substitution, provider fleet control and physical post-freeze reconfiguration. **Code status:** not inspected.

## P12 — SCAPE

[Primary paper, 2608.19425v1](https://www.alphaxiv.org/abs/2608.19425v1). Read pp. 3–6, 13–15; corroborated consequential appendix details in the original PDF.

Paired target/simulator data correct surrogate labels before scenario prediction. Driving is sim-to-sim; quadruped work includes physical Go2 observations. Split-conformal coverage is marginal, with constant-width intervals. Appendix A.3 reuses quadruped validation as calibration; this complicates the independence claim if that validation chose the predictor. Algorithm A.1 clamps a quantile rank to sample size, requiring scrutiny for tiny calibration sets. The 95 physical paired observations span only five scenes; random pose splits do not establish unseen-scene generalization.

**Use:** a later v1.8 scenario-calibration hypothesis. **Change:** separate selection/calibration, valid quantile edge handling and appropriate scene separation. **Code status:** not inspected. The included 15-point rank example diagnoses an algorithmic edge case; it is not a reproduction of SCAPE's empirical results.

## Consequential findings independently checked

The WHALE test-selection statement and SCAPE appendix details were checked against downloaded versioned arXiv PDFs, not only alphaXiv's extracted pages. Files and SHA-256 values are recorded locally under `evidence/primary-checks/`; official code snapshots and commits are under `evidence/code/manifest.json`. Those full source files are working evidence, not part of the redistributable summary ZIP.

The baseline source register already listed Who & When and AgentDojo as synthesis-only. This review adds selective primary inspection for those sources. GraphTracer, TTPO and the other original inspirations were not comprehensively re-reviewed here; retain their earlier reading status. Newly discovered but unread leads—such as HarnessCompass, Prime Agent and Living-Harness—are not used as evidence.

Two broad alphaXiv discovery calls covered the eight release questions. The second returned several tangential workflow papers; those were excluded. Known primary papers were then read directly to answer specific gaps. This is a decision-focused sample, not an exhaustive or publication-ranked literature census.
