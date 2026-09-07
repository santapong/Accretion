# Write around a defensible contribution

Choose the artifact that the evidence can support: literature synthesis, research proposal, methods protocol, results report or manuscript. The user's request determines the deliverable; available evidence determines the strength of its claims.

## Build the argument before prose

Write one sentence for each: the practical problem, the evidence-backed gap, the proposed contribution, how it is evaluated, what the results establish and what remains unresolved. If results are absent, the last two become planned evaluation and success/failure criteria. Do not imply a completed scientific contribution by polishing a proposal into past tense.

For every contribution claim, maintain a small ledger linking it to a primary source or local experiment artifact. Mark it as established prior work, proposed method, measured local result or interpretation. Audit this ledger before drafting the abstract.

## Where evidence belongs

| Section | Job | Evidence needed |
|---|---|---|
| Introduction | Explain the problem, gap and bounded contribution | Actual project need and directly relevant literature |
| Related work | Compare mechanisms, assumptions and evaluation settings | Primary sources; organize by question rather than one paragraph per paper |
| Method | Make the adaptation reconstructable | Inputs, state, algorithm, constraints, interfaces and inherited components |
| Experimental setup | Make the comparison interpretable | Population, splits, repeats, baselines, budgets, verifier and preregistered analysis |
| Results | Report what happened | Preserved local outputs, denominators, uncertainty and all declared endpoints |
| Ablations/analysis | Test why it happened | Controlled interventions and alternative explanations |
| Limitations | Define where the claim stops | Data/compute bounds, negative results, missing controls and transfer assumptions |
| Reproducibility appendix | Let others reconstruct the study | Versions, split manifests, configurations, seeds, scripts, costs and artifact locations |

Use these functions without forcing every deliverable into a conference-paper template. Follow an actual venue template when the user supplies one.

## Claim discipline

- Use causal language only for an appropriate intervention and analysis. Correlation or model-written blame supports a diagnostic hypothesis.
- Distinguish matched tasks from matched compute; repeated rollouts from independent environments; observed zero failures from a low upper risk bound.
- Report changes in percentage points versus relative percent correctly. Include the baseline and denominator.
- A best checkpoint chosen after inspecting final tests requires new untouched evaluation for a confirmatory claim.
- Do not infer conditional safety from marginal calibration, real-world success from simulation, or a general efficiency gain from one favorable workload.
- Cite the exact version supporting the sentence. Metadata, a secondary synthesis and a checked appendix have different evidential roles.
- When sources conflict, explain the differing population, procedure or versions; do not average them into consensus.
- Keep paper findings separate from project results and from the author's suggested future work.

Write the abstract last. It should report the actual artifact and evidence, not hoped-for outcomes. Avoid invented authors, affiliations, venues, novelty claims, results tables, p-values, citations or “state of the art” language. Where a conclusion remains open, say which observation would resolve it.
