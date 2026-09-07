# Accretion v1.1–v1.8: research improvements after Revision 3

Prepared 2026-09-06. Status: **research and amendment proposals; no new release or empirical Accretion result**.

The recommendation is to strengthen the existing eight-version program, starting with the quality of its evidence. Keep v1.1 as a fixed-profile feasibility study. Add a clearer observation contract and budget. Build toward harness and recovery improvements before spending on model adaptation. Preserve v1.8 as pre-freeze advisory work.

This review uses the actual Revision 3 archive: 29 files, eight revision-3 SDDs, and 28 matching manifest entries. It selectively reads methods, evaluation and relevant appendices from **12 primary papers** through alphaXiv, corroborates two consequential findings against downloaded versioned arXiv PDFs, and inspects relevant files from two official repositories. It is focused research, not a systematic review of the entire literature or a reproduction of those papers.

| Release | Most useful proposed addition | Why it matters |
|---|---|---|
| v1.1 | Prove that each profile's intended settings actually took effect; account for complete three-profile batches | A cheaper request may reflect an ignored setting, an easy task or missing accounting |
| v1.2 | Evaluate mature starting harnesses and measure the cost of maintaining specialized variants | Gains over a weak seed do not establish benefit over Accretion's working baseline |
| v1.3 | Separate blame prediction, reproducible repair, and cost-effective recovery | Correctly naming a failure is different from fixing it |
| v1.4 | Add delayed, cross-session memory attacks and atomic stale-state checks | Immediate tool-injection tests miss persistent and delayed effects |
| v1.5 | Add behavior-linked repair and preservation sets after the frozen-candidate study | Aggregate gains can hide newly broken tasks and overly broad updates |
| v1.6 | Add a separately budgeted harness/adapter interaction study, with development-only selection | Joint optimization is plausible, but its cost and selection protocol need correction |
| v1.7 | Separate portable knowledge from source-specific details; evaluate context-only transfer first | A source prior can be irrelevant or harmful even when it looks similar |
| v1.8 | Separate workflow simulation from scenario-calibrated physical prediction | Serving-system and robot-simulation results have different ownership and evidence limits |

The strongest cross-cutting change is to distinguish search/model-selection data, untouched calibration data, and final evaluation data whenever the claimed statistical method requires that separation. A passed shift diagnostic does not establish exchangeability, and average coverage is not a per-task safety guarantee.

The papers provide both useful methods and counterexamples. [HarnessOpt-Bench](https://www.alphaxiv.org/abs/2608.06301v1) offers a useful evaluation boundary but deliberately weak seeds. [WHALE](https://www.alphaxiv.org/abs/2609.00196v1) motivates studying coupled harness/weight changes, while its published test-based checkpoint selection is unsuitable for Accretion's final evaluation. [SCAPE](https://www.alphaxiv.org/abs/2608.19425v1) motivates scenario-level simulation calibration, but its quantile edge case and calibration reuse need explicit treatment. The detailed evidence limits are in the source register.

Read the package in this order:

1. [Release amendment proposals](release-amendments.md): existing coverage, new deltas, experiments and ownership.
2. [Primary-source reading register](source-register.md): what was read, what it supports, and what must not be copied.
3. [v1.1 next-study plan](v1.1-next-study.md): the first bounded follow-up and unresolved inputs.
4. [Project bridge and review status](project-bridge.md): actual repository anchors and integration limits.
5. [First paper outline](research-paper-plan.md): a focused study that can grow into a manuscript.
6. [Skill and validation record](skill-and-validation.md): the reusable process created from this work.

The unchanged original documents are under `baseline-revision-3/`. These proposals have not been folded into a replacement Revision 4 SDD/contract package. No original acceptance row was marked complete, and no schema or fixture validation result was reused as a research result. The research does not assign the five later ideas to new releases.

Use the installed skill as: **“Use $research-to-design to read these papers against this design revision, explain what to keep/change/reject, and produce a testable amendment.”**

Portable copies of the skill and its reading, adaptation and writing guides are in `skill-snapshots/`. The downloadable research archive includes these guides, the original baseline, the proposals and validation records. Primary papers remain linked to their publishers; downloaded working copies are excluded from the archive.
