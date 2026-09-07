# Paper evidence register

6 September 2026 · Focused technical investigation for Accretion

Two alphaXiv discovery rounds covered the broad architecture and then concrete reproduction-test techniques. Eleven papers were examined through raw PDF-page retrieval, supplemented by selected official monitoring source files. This is a targeted investigation, not an exhaustive systematic review. Retrieved versions are recorded below; a newer landing-page version is not silently substituted for the text inspected. External paper experiments were not reproduced.

## 1. Mutation-Guided LLM-based Test Generation at Meta

[2501.12862v1](https://www.alphaxiv.org/abs/2501.12862v1) · Inspected pp. 1–2 and selected evaluation sections.

**Method:** generate issue-focused mutants that survive the existing suite, then generate tests that expose them. Check buildability, repeatable passing on the original program, and newly detected mutants. Human review judges usefulness and relevance.

**Evidence:** deployment over 10,795 Android Kotlin classes in seven Meta platforms produced 571 tests. The reported 73% acceptance pertains to engineers reviewing tests in Messenger/WhatsApp test-a-thons, not an independent universal oracle-validity rate. Only 36% were judged privacy-relevant, illustrating that useful and concern-specific are different outcomes.

**Transfer:** use surviving reviewed fault variants to guide new regression fixtures; retain human semantic admission. Do not use line coverage as the sole quality target.

**Limit:** passing the current implementation can preserve existing bugs. Mutant equivalence and relevance remain difficult. Industrial infrastructure and its costs do not transfer directly to Accretion. No ACH implementation was inspected.

## 2. DPIAgent: Divide, Protocol, Isolate for Agentic Reproduction Test Generation

[2608.23341v1](https://www.alphaxiv.org/abs/2608.23341v1) · Inspected pp. 1–4, 7–8.

**Method:** separate read-only defect exploration from test editing/execution, enforce a six-part diagnosis/test-plan handoff, and restrict tools by phase. An optional selection stage generates six candidate tests and three surrogate fixes.

**Evidence:** SWT-Bench Verified evaluation across three model backbones, seven baselines and component ablations. Removing phase separation or its supporting restrictions degrades the reported results. The retrieved analysis also uses an LLM judge for an intermediate readiness measure; that measure should not be confused with executable correctness.

**Transfer:** enforce diagnosis and generation as explicit workflow stages with capability checks. Evaluate the structured single-candidate pipeline before paying for multi-candidate selection.

**Limit:** fail-to-pass scoring remains weaker than semantic completeness; surrogate agreement is not independent truth. Project link was present, but its site could not be opened by the web fetcher and its implementation was not inspected.

## 3. Beyond Fail-to-Pass: Iterative Hardening of Co-Generated Bug Reproduction Tests and Fixes

[2607.19843v1](https://www.alphaxiv.org/abs/2607.19843v1) · Inspected pp. 1–3, 6–8, 19.

**Method:** distinguish rigorous, lax and misaligned tests; challenge plausible repairs using semantic mutations. COHARDEN starts with a test, then iterates test/fix hardening using a matrix comparing current and previous tests. The previous test is a proxy reference inside the loop, not an independent oracle.

**Evidence:** on the 433-case SWE-bench/SWT-bench Verified intersection, the reported GPT-5-mini result is 69.4% resolved versus 61.4% for same-backbone ordinary cogeneration, at $0.84 versus $0.56 per instance. These are study costs, not current provider prices or Accretion estimates.

**Transfer:** test-first generation and bounded adversarial hardening. Keep independent final qualification.

**Limit:** its convergence tolerance of 0.2 and finite mutant pool are research choices, not acceptance thresholds to copy. A co-generated pair and its previous test can share an error. The linked project redirect was unavailable through the web fetcher.

## 4. Probe to Generate: Program Variant-Guided Test Augmentation for Repository-Level Repair Benchmarks

[2604.01518v2](https://www.alphaxiv.org/abs/2604.01518v2) · Inspected abstract and pp. 3, 7, 11; primary landing page checked.

**Method:** treat surviving variants of the reference repair as evidence of possible gaps. Retain new tests that pass the reference, reject a surviving variant, and tolerate behavior-preserving transformations. Implementation-specific screening also uses an LLM, so the entire admission process is not purely deterministic.

**Evidence:** 500 Python issues from twelve repositories; 385 have surviving variants. The study reports 1,014 retained tests on 211 issues and lower resolved rates when reassessing ten repair agents.

**Transfer:** require both faulty-variant rejection and valid-variant acceptance. Include end-to-end paths and state transitions.

**Limit:** surviving variants may be equivalent; the reference patch may not be the only correct implementation. The authors acknowledge possible public-benchmark training overlap and report an ambiguous valid-alternative rejection. Candidate execution checks mitigate some problems but do not establish complete semantics. No implementation was inspected.

## 5. Real-Time Detection and Repair of LLM Agent Failures

[2608.02464v1](https://www.alphaxiv.org/abs/2608.02464v1) · Inspected pp. 1–5 and official repository documentation/selected code.

**Method:** observable telemetry feeds cheap memoryless and temporal detectors. A fixed random recurrent representation with ridge readouts predicts the next step; per-channel prediction errors feed CUSUM, calibrated using healthy validation episode maxima. Deterministic checks address completion and grounding separately.

**Evidence:** the v1 inventory is 2,823 episodes, including 770 real-tool episodes. Do not describe all 2,823 as real-tool runs. The paper explicitly reports weak organic-failure transfer, only one of three organic fabrications detected, and an underpowered fabrication study. Injected failure results are a separate population.

**Transfer:** observable features, cheap baselines, per-configuration calibration, causal scoring and deterministic checks before model-based escalation.

**Limit:** plausible corrupted facts can evade internal consistency checks; agreement with received tool values does not prove those values true. Raw log probabilities may be unavailable in Accretion runtimes. CPU scoring latency excludes some surrounding collection or model costs.

## 6. Quantifying Frontier LLM Capabilities for Container Sandbox Escape

[2603.02277v3](https://www.alphaxiv.org/abs/2603.02277v3) · Inspected pp. 1, 4–5, 9–10 and task/limitation material.

**Method:** SANDBOXESCAPEBENCH places a deliberately weakened container inside an outer VM. Retrieving a flag outside the inner sandbox establishes escape from the test subject while the outer environment isolates the evaluator's host.

**Evidence:** eighteen scenarios span orchestration, runtime and kernel/control-surface weaknesses. The original study used five trials per model/task with a 2.5-million-token budget; updated cohorts use larger budgets. Results across those budgets should not be mixed into a single comparable rate.

**Transfer:** test containment as a property, protect the evaluator separately, deny unnecessary control surfaces, and track complete environment versions.

**Limit:** this motivated-adversary benchmark deliberately introduces weaknesses. It does not estimate compromise probability for a correctly defended Accretion deployment. No exploit or vulnerable environment was executed in this research.

## 7. Learn then Test: Calibrating Predictive Algorithms to Achieve Risk Control

[2110.01052v5](https://www.alphaxiv.org/abs/2110.01052v5) · Inspected §2, pp. 3–4, and relevant proof material.

**Method:** associate each fixed candidate policy/parameter with an unsafe-risk null hypothesis, compute valid p-values from calibration data, and use family-wise error control to select a set of qualified policies. A cost objective can choose among that qualified set.

**Evidence:** mathematical guarantees plus worked vision/tabular examples; this is not an agent-verifier study.

**Transfer:** qualify supplementary assurance policies on separately held-out, independently labeled cases. Distinguish probability calibration from demonstrating a risk bound.

**Limit:** the guarantee depends on valid p-values under the actual sampling and loss assumptions. Dependence across policy p-values can be handled by appropriate multiplicity procedures; dependence among observations is a separate issue. Drift, biased labels and adaptive reuse of data need their own treatment. The finite-sample theorem does not turn an ML score into acceptance authority.

## 8. Sell Me This Stock: Unsafe Recommendation Drift in LLM Agents

[2603.12564v7](https://www.alphaxiv.org/abs/2603.12564v7) · Inspected raw PDF opening/protocol description; the earlier web check returned v8.

**Method:** pair clean and manipulated tool-data conversations, measuring user-suitability violations against task constraints rather than relying only on recommendation relevance.

**Evidence:** eight models, ten users and 23-turn conversations in the financial evaluation. It demonstrates that plausible outputs and ordinary relevance metrics can conceal task-specific violations.

**Transfer:** Sentinel probes should check invariants against an independent reference, including manipulated or malformed tool-result controls.

**Limit:** this concerns adversarial tool-data manipulation in a specific advisory task. Transfer to software correctness, provider upgrades or efficient probe selection remains a hypothesis. We do not merge numerical findings from its v7 and v8 texts.

## 9. Externalizing Research Synthesis and Validation in AI Scientists through a Research Harness

[2606.18874v3](https://www.alphaxiv.org/abs/2606.18874v3) · Inspected pp. 1, 3–4, 17, 19, 32–33.

**Method:** XCIENTIST preserves literature, mechanisms, implementation plans, validation contracts and ablation results as inspectable artifacts. A stage controller checks workspace evidence before advancing or claiming completion.

**Evidence:** case studies cover agent memory, traffic forecasting and physics-informed networks. They illustrate traceable research workflows; they do not establish a general causal advantage in research-portfolio allocation.

**Transfer:** link each proposed mechanism to its implementation switch, baseline, ablation and evidence artifact. Preserve failed/partial stages and reasons for stopping.

**Limit:** architecture-level separation alone does not establish that every validator is correct or independent. Adopt the artifact discipline before considering its more elaborate search/planning machinery. No code was inspected or experiments reproduced.

## 10. AI Research Agents Narrow Scientific Exploration

[2605.27905v2](https://www.alphaxiv.org/abs/2605.27905v2) · Inspected opening design description and discussion, p. 9.

**Method:** compare the distribution of generated ideas to citation-defined research areas, starting literature and subsequent human work using breadth, distance and frontier-alignment measures.

**Evidence:** 219,655 ideas across five frameworks and five models; the study reports concentration near seed literature.

**Transfer:** evaluate a research shortlist's diversity of hypotheses and mechanisms, record human overrides, and reserve room for exploratory proposals outside the highest model score.

**Limit:** generated ideas and published human research differ in selection and maturity; landscape/embedding proxies are not direct measures of future scientific value. This motivates a criterion for a future advisor, not a proven portfolio optimization algorithm.

## 11. Fed-SE: Federated Self-Evolution for Cross-Environment Knowledge Transfer in Privacy-Constrained LLM Agents

[2512.08870v2](https://www.alphaxiv.org/abs/2512.08870v2) · Inspected method material and limitations, p. 9.

**Method:** train local LoRA adapters using successful trajectories and synchronize through parameter averaging. The compatible base/adapter parameter space is part of the method.

**Evidence:** five simulated task environments—BabyAI, WebShop, TextCraft, Maze and Wordle—with model-family comparisons. This is not physical-device or heterogeneous closed-provider federation evidence.

**Transfer:** preserve local experience boundaries and qualify imported improvements locally. For Accretion, curated fixture exchange is a simpler independent proposal than adapter aggregation.

**Limit:** the paper explicitly omits differential privacy and homomorphic encryption and acknowledges reconstruction risk. Synchronous averaging also assumes participating clients remain available. Do not infer that sharing only adapters or derived metadata guarantees privacy or resistance to poisoning.

## Official code inspection and version caveat

The [AgentTrajectorySentinel repository](https://github.com/sunnydubey1111/agent-trajectory-sentinel) was inspected through alphaXiv's repository reader. Selected material included its reproduction/data documentation, the beginning of `derail/monitor/baseline.py`, and telemetry conversion/missingness handling in `derail/telemetry/adapter.py`. Directory listings exposed additional algorithms; those were not all audited.

The current reproduction document distinguishes 4,022 episodes/35 corpora from the paper-v1 snapshot at commit `00c0673`, with 2,823/25. A future replication must pin the desired source revision and corresponding manifests. This pass did not run the repository, establish a current full commit pin, or validate its checksums.

Two porting considerations follow from inspection: missing log probabilities must remain distinguishable from high confidence; and filtering a rolling healthy baseline using the monitor's own alarms can select the calibration population. Accretion should require independent outcome qualification and retain audited rejected/alarming cases before trusting such a rolling scheme. These are adaptation judgments, not replicated study findings.

No external implementation was copied into Accretion. Literature limitations and Accretion-specific proposals remain separate throughout the package.

## Saved reading set

Five papers were saved to **Accretion — evaluation and assurance** in [alphaXiv Library](https://www.alphaxiv.org/bookmarks): ACH, DPIAgent, PROBE, Real-Time Detection and Repair, and Learn then Test. The remaining papers are cited here for context and limitations; they were not added in a larger bulk library operation.
