# Accretion: five future directions and the next decision

Prepared for Santapong · 6 September 2026 · Status: advisory decision brief

## Recommendation

Investigate **Governed Evaluation Foundry** first. Keep **Semantic Drift Sentinel** as a smaller extension of existing revalidation controls. Hold **Adaptive Assurance Scheduler** until trustworthy labels and measured verification costs justify it. Remove **Research Portfolio Advisor** and **Federated Verified Experience Network** from the immediate shortlist.

This selects a research question, not a release commitment. Keep future version numbers unassigned until the existing v1.x package has been reconciled and feasibility evidence is available. The immediate engineering priority remains completing the current v0.4 program.

The strongest question is: **Can Accretion turn confirmed failures into reusable, independently qualified evaluations more efficiently than manual fixture authoring, while preserving acceptance semantics?**

## Basis and limits

I read the visible conversation in [Accretion Project Ideas](https://chatgpt.com/share/6a9d1fd3-679c-83ec-8bc7-1e993abfc176), including its final five candidates. The attached v1.x package was listed on the page but its files were not downloaded or audited. Consequently, novelty against v1.1–v1.8 remains provisional.

The local repository was clean on `develop` at `71893d467cda70cadf3c4b81b1544475c74b5be7`, matching the locally recorded `origin/develop`. No remote refresh or runtime tests were performed for this documentation task. Recent commits record M3, M6 and M8 closure; the acceptance ledger retains twelve deferred criteria across M7, M9 and M10. The release README still contains stale M2 review wording.

Three repository contracts shape this decision:

- [v0.4 SDD](/mnt/data/company/apps/Accretion/docs/sdd/Accretion_SDD_v0.4.md), §§8, 15.3 and 21: routing binds independent verifier implementations; unvalidated provider drift disables exploration; behaviorally material changes require revalidation.
- [Forward v0.10 SDD](/mnt/data/company/apps/Accretion/docs/sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.10.md), §11: verifier implementations may evolve under frozen semantics. Revising an inadequate `VerificationSpec` requires separate human governance and re-baselining. This is a design contract, not a shipped v0.10 capability.
- [Runtime runbook](/mnt/data/company/apps/Accretion/docs/runbooks/p0-runtime.md): adapters have different containment mechanisms. Their policies must not be assumed equivalent.

The brief uses focused primary-source checks, not a systematic literature review. Numerical results from papers are not Accretion results.

## Candidate comparison

These qualitative scores are engineering judgments. “Value” means plausible benefit, not measured savings. “Readiness” describes the evidence currently established in this review; no production dataset inventory was conducted.

| Candidate | Architectural gap | Potential time/cost value | Data readiness | Main acceptance risk | Sandbox feasibility | Measurable research value | Decision |
|---|---|---|---|---|---|---|---|
| Evaluation Foundry | Strong against audited contracts; v1.x overlap unverified | High if failures recur | Unknown incident-pair inventory | Admitting an incorrect oracle | High for bounded software fixtures | High: yield, validity, labor and reuse | First feasibility candidate |
| Drift Sentinel | Partial; revalidation already has an owner | Medium–high if changes are frequent | Existing fixtures may seed probes | Selective probes miss regressions | High for software replay | High: detection versus replay cost | Retain as a narrow extension |
| Assurance Scheduler | Partial; distinguish scheduling from routing | High only if optional verification is costly | No qualified scheduler dataset established | Skipping evidence needed for acceptance | High offline; online decisions need qualification | High: cost versus missed failures | Reserve pending data |
| Portfolio Advisor | Broader future product scope | Unproven | Longitudinal experiment outcomes not established | Biased ranking shapes research choices | Advisory workflow is testable | Harder: delayed and confounded outcomes | Park |
| Federated Experience | Possible multi-installation gap | Unproven | No participating cohort established | Poisoning, leakage and incompatible evidence | Partial; local isolation cannot prove federation privacy | Potentially high, operationally expensive | Park |

Do not force a weighted total: the missing dataset, usage and v1.x overlap evidence would make numerical rankings look more certain than they are. Foundry loses first place if incidents cannot be reproduced; Sentinel could then become the smaller viable study.

## What to build evidence for

**1. Evaluation Foundry.** Start with a local, reviewable pipeline: confirmed incident → minimal reproduction → candidate fixture → independent known-bad/known-good validation → admission proposal. Each fixture records the incident, code and environment versions, expected behavior, oracle rationale, ownership and results. Distinguish a new test of an existing requirement from a proposed change to the requirement itself. Preserve historical evidence and version all revisions.

Use exact matching and simple rules as the first deduplication baseline. Introduce ML clustering only if it improves measured triage effort. Use an LLM for genuinely novel fixture synthesis only after ordinary transformations prove insufficient. Neither the synthesizer nor its own generated tests can be the sole acceptance authority.

**2. Drift Sentinel.** Start with configuration-change detection and a fixed replay suite. Compare identical task inputs across recorded old/new configurations, using repeated trials where outputs are stochastic. Record unavailable historical provider versions as a limitation rather than claiming reproducibility. Add ML probe selection only after a fixed or stratified subset baseline. Keep mandatory critical probes; record evidence applicability changes without rewriting historical results. Connect recommendations to existing revalidation and activation governance.

**3. Assurance Scheduler.** Measure mandatory versus supplementary verification costs first. Test a deterministic ordering heuristic before training a risk model. Scheduling must preserve every mandatory check and frozen acceptance rule. Offline evaluation needs complete verifier outcomes to avoid learning only from checks that an earlier scheduler happened to run. Foundry is one possible source of labels; independently audited existing data could also qualify.

**4–5. Deferred ideas.** Revisit the Portfolio Advisor when there is a real backlog with recorded costs, outcomes and human choices. Revisit federation when several installations demonstrate a shared need that curated fixture exchange cannot satisfy. Neither is required to establish the first three ideas.

## What the cited research actually supports

| Primary source and inspection depth | Useful evidence | Limit on the inference |
|---|---|---|
| [Continuous Benchmark Generation, 2511.10049v1](https://arxiv.org/abs/2511.10049v1), full text | Connects developer requirements to migration commits. Its evaluation uses 137 knowledge-base documents across four services; Table 2 reports precision 1.0 and recall 0.25–0.667. | Supports traceable benchmark construction. Patch alignment is not proof that a generated executable oracle is valid. The limited recall also argues for measuring missed cases. |
| [Real-Time Detection and Repair, 2608.02464v1](https://arxiv.org/abs/2608.02464v1), abstract | Reports telemetry monitoring across 2,823 episodes and substantial loss without deployment recalibration. | Supports testing cheap triage and deterministic checks. Does not establish safe verifier omission or Accretion cost savings; full protocol review remains necessary. |
| [Sell Me This Stock, 2603.12564v8](https://arxiv.org/abs/2603.12564v8), abstract | Paired clean/manipulated tool-data conversations across eight models expose violations missed by ordinary relevance metrics. | This is manipulated-input drift in a financial task, not direct validation of provider-upgrade detection or selective replay. |
| [AI Research Agents Narrow Scientific Exploration, 2605.27905v2](https://arxiv.org/abs/2605.27905v2), abstract | Examines 219,655 generated ideas across five frameworks and five models, finding concentration near starting literature. | Motivates diversity checks; does not prove that the proposed advisor improves scientific outcomes. |
| [Fed-SE, 2512.08870](https://arxiv.org/abs/2512.08870), inspected full-text method sections | Uses successful trajectories for local LoRA training and aggregates adapters between environments. | This differs from sharing signed fixtures or risk metadata. Keeping raw trajectories local alone does not establish privacy or poisoning resistance for Accretion. |

## Next steps and decision gates

1. **Reconcile ownership.** Retrieve the existing v1.x package and map these five candidates to its contracts before drafting an ADR. Deliver one overlap table: existing owner, proposed delta, dependencies and unresolved questions. Avoid a new SDD that restates an existing release.

2. **Inventory evidence.** Sample up to twelve independently confirmed software incidents across at least three failure classes, if available. Record reproducible bad/fixed states, provenance, environment availability, recurrence, verifier cost and manual review effort. Keep missing and rejected cases in the denominator. This is a proposed small feasibility sample, not a statistically sufficient safety study.

3. **Qualify containment before executing candidates.** Define an Accretion-controlled disposable environment with no production secrets or host control socket, denied outbound network, least privilege, bounded writes and resource limits. Test host-write, egress and resource-bound enforcement using benign canaries. Separate hidden evaluation material from the proposer; merely mounting it read-only does not hide it. Git worktrees provide workspace separation, not security containment. Record results per runtime/environment combination. This review did not run containment tests.

4. **Run a bounded Foundry comparison after scope and resource budget are set.** Compare manual authoring with rules/templates on the same incident families; add LLM synthesis only as a separately costed arm. Keep evaluation families separate from development examples. Record authoring and review minutes, compute/API cost, reproduction yield, invalid-oracle rejection, flakiness and mutation detection. An unrelated crash or timeout must not count as reproducing the target defect. Re-run both bad and fixed states in fresh environments.

5. **Make a go/no-go decision.** Continue to an ADR only when incidents reproduce, independent review admits useful fixtures, and total authoring plus review cost shows a credible path to payback. Stop or narrow scope if fixtures mostly encode irrelevant failures, valid alternate solutions fail, reproduction is unstable, or there is any unresolved critical containment/authority failure. Zero observed errors in a small pilot is not proof of a low population error rate. Set release-level statistical requirements separately before promotion.

The initial economic test is:

`net benefit = avoided future verification/rework cost − authoring − review − execution − maintenance cost`

Keep engineering minutes and currency separate unless an explicit labor rate is chosen. With no observed future reuse, report scenarios and a break-even reuse count rather than claiming realized savings.

## Concrete decision to take forward

Proceed with ownership reconciliation and an incident inventory for Evaluation Foundry. Retain Drift Sentinel as the alternative if configuration churn is better evidenced than recurring incidents. Defer a full SDD, version assignment and learned scheduling until these gates answer whether the feature is useful and distinct.

This preparation created only this standalone brief. No repository code, release contracts, deployments, paid experiments or hardware state were changed.
