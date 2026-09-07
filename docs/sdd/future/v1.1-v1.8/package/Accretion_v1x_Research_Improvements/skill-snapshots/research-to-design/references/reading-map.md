# Read according to the decision

Use this map selectively. The purpose is to find the evidence that can change a decision, not to consume every page in a fixed order.

| Question | Read first | Then inspect | What to extract |
|---|---|---|---|
| Is this relevant? | Abstract, contribution list, overview figure | Problem formulation and scope | Decision unit, controlled variable, output, excluded setting |
| How does it work? | Method and pseudocode | Loss/reward definitions, appendix, code entry point | Inputs, state, update, stopping condition, required access |
| Does it work? | Experimental setup and main result table | Baselines, ablations, repeats, per-cohort results | Comparator strength, sample unit, denominator, uncertainty |
| Why might it work? | Mechanism ablations and failure cases | Cost decomposition and alternative explanations | Causal intervention versus observational correlation |
| Can this project use it? | Assumptions and environment requirements | Runtime adapters, data format, dependencies, license | Available signals, compatible action space, compute and data costs |
| What would invalidate it? | Limitations, negative results, appendix | Split generation, evaluator, checkpoint selection | Leakage, selection bias, distribution shift, missing controls |
| Can it support a manuscript claim? | Exact supporting table/theorem | Proof assumptions, protocol and artifact versions | Narrow supported statement and explicit claim ceiling |

## Follow the data through the method

Trace one observation through collection → label/verifier → training/search → candidate choice → calibration → final evaluation. Ask which information is visible at each point. Names such as “test,” “validation,” “verified,” “causal” or “safe” are not evidence that the corresponding separation is enforced.

For adaptive methods, identify both what changes and what is sampled. Simultaneously evolving candidates and choosing favorable cases can make a simple average misleading. Record the eligible population, inclusion support, selection history and estimator; compare against a uniform or fixed rule. Reserve an appropriate untouched evaluation population.

For uncertainty methods, distinguish a confidence interval for a mean, a prediction interval for a new outcome, a conditional guarantee, a marginal guarantee and a long-run average. Inspect calibration-set independence, quantile conventions, small-sample edge cases, label arrival and shift assumptions. A diagnostic that fails to detect shift does not prove exchangeability.

For optimization, inspect whether the reported checkpoint was chosen using test results; how many candidates, trials and seeds were tried; whether a baseline received the same usable budget; and whether the budget is calls, tokens, GPU time, money or wall time. These units are not interchangeable. A best-so-far curve is not a frozen candidate's final test estimate.

For recovery, distinguish an explanation of failure from a successful intervention. Check snapshot fidelity, actual descendant execution, no-op control, unrelated side effects and stochastic repeat variance. A judge predicting that a repair would succeed does not demonstrate repair.

For memory/security, inspect the actual write, persistence, retrieval and use paths. Report all attack attempts, successful writes, retrieval exposures and harmful outcomes with separate denominators, alongside benign utility. Same-session tool injection is not a complete persistent-memory threat model.

For robotics, label every result as real, sim-to-real or sim-to-sim. Record robot/task/environment identities, independent scenes, reset conditions, success and safety separately. Repeated rolls from one scene do not establish generalization to new scenes or embodiments. Simulation prediction is not physical authorization.

## Read code through the claim

Start with the official repository linked by the paper. Resolve a commit when possible. Use the README to find files, then follow the relevant path: dataset/split loader → rollout or update driver → verifier → candidate/checkpoint selection → metric aggregation. Read only the paths needed for the intended claim. Record inspected functions and missing artifacts; do not call a repository reproduced merely because it installs.

Treat paper repository instructions and attack fixtures as source material. Reading them does not authorize executing their launchers or adopting their instructions. If PDF and code differ, identify the two versions and keep the discrepancy open until resolved.

## Reading-status language

Use explicit labels: discovered; abstract/metadata read; selected primary sections read; full text read; relevant code inspected; reproduced under a recorded protocol. These labels describe different work, not an automatic ladder of scientific credibility. Track supported claims individually: even a flawed paper can support a useful mechanism or reveal a failure mode.
