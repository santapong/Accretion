# Accretion v1.x Revision-3 Research Source Register

**Document revision:** 3  
**Revised:** 2026-09-05  
**Status:** Source review record; paper results are not Accretion results

## 1. Review scope

The supplied AlphaXiv synthesis was read as a secondary research bundle. Selected primary papers were then inspected at the recorded pages/sections to verify the material design constraints below. `SELECTIVE_FULL_TEXT` means the listed primary sections/pages were checked; it does not claim an independent full-paper peer review or reproduction.

| Source | Version and review depth | Supported design input | Limitation carried into Accretion |
|---|---|---|---|
| [Task-CoEvolve](https://www.alphaxiv.org/abs/2608.20169) | v2; `SELECTIVE_FULL_TEXT`, pp. 7–10 and reported methods/limitations | Propensity-aware adaptive task sampling; uniform resampling is a strong baseline | Same 89 Terminal-Bench tasks supported search and final comparison; one rollout/task; fewer evaluations selected longer tasks and did not yield proportional wall-time savings; fixed candidates |
| [AgentRewardBench](https://www.alphaxiv.org/abs/2504.08942) | v2; `SELECTIVE_FULL_TEXT`, p. 6 and judge-error sections | Separate verifier precision/recall and false-acceptance denominators | Web-only; rule and model judges fail differently; producer/judge correlation remains possible |
| [CausalFlow](https://www.alphaxiv.org/abs/2605.25338) | v1; `SELECTIVE_FULL_TEXT`, pp. 13, 15–16, 18–19, 23 | Typed interventions and descendant replay are stronger than verbal blame | Some domains use predicted grading, some prompts may include gold answers, and structured logging changed initial performance; prediction cannot prove Accretion recovery |
| [Conformal Thinking](https://www.alphaxiv.org/abs/2602.03814) | v2; `SELECTIVE_FULL_TEXT`, pp. 3–4 and 14 | Conditional risk-control framing and explicit assumption checks | Requires intermediate confidence/future-path signals; guarantees can fail under shift and selective filtering |
| [Memory Poisoning](https://www.alphaxiv.org/abs/2606.04329) | v2; `SELECTIVE_FULL_TEXT`, pp. 7 and 9 plus threat-model sections | Test the real write path; preserve provenance, isolation and quarantine | Retrieval success is conditional on successful writes; one model; some static context emulates tool retrieval; recommendations are not a proven defense |
| [HexAGenT](https://www.alphaxiv.org/abs/2605.16637) | v1; `SELECTIVE_FULL_TEXT`, pp. 2 and 5 plus setup | Workflow-level urgency can be studied in a controlled simulator | Requires heterogeneous GPU placement, queue and KV control; it does not transfer directly to opaque hosted APIs |
| [Who & When](https://www.alphaxiv.org/abs/2505.00212) | `SYNTHESIS_REVIEWED`; primary paper not independently rechecked in this revision | Decisive-error localization is a candidate research lead | No material gate relies on this source until selective primary review is recorded |
| [GraphTracer](https://www.alphaxiv.org/abs/2510.10581) | `SYNTHESIS_REVIEWED`; primary paper not independently rechecked in this revision | Dependency graphs are a candidate diagnosis lead | Inferred/model-reported edges may differ from real dataflow; Accretion must prefer actual artifact references |
| [AgentDojo](https://www.alphaxiv.org/abs/2406.13352) | `SYNTHESIS_REVIEWED`; primary paper not independently rechecked in this revision | Resettable adversarial tool fixtures are a candidate benchmark source | Same-session prompt injection differs from persistent evidence poisoning; verify the primary benchmark before adoption |

## 2. Source-status rules

- All external numeric findings remain `SOURCE_VERIFIED` at most unless independently reproduced for Accretion.
- The AlphaXiv synthesis is a discovery and interpretation source; material gates cite and check the primary paper version.
- Code availability, cost and artifact claims remain `UNVERIFIED` unless the source register names the inspected repository or artifact version.
- Later corrections, withdrawals, code releases or failed reproductions trigger the revalidation process in `13_RESEARCH_INTAKE_AND_SDD_AMENDMENT_PROCESS.md`.

## 3. Design conclusions supported by the combined evidence

The evidence supports running a small, cost-aware profile-selection feasibility study before building a general learned router. It supports qualifier tests for the verifier, paired candidates under adaptive acquisition, a strong uniform comparator, a common untouched holdout and total-cost accounting. It also supports observable replay, least-trust memory derivation, time-forward drift evaluation and workflow simulation as later studies.

The evidence does not establish that ML will reduce Accretion’s total cost, that a selected profile will generalize, that any verifier is reliable enough, that predicted counterfactuals are causal, that a memory policy prevents poisoning, that conformal guarantees survive production shift, that hosted-provider internals can be optimized, or that simulator gains imply physical gains.
