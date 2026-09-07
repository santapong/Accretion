# Skill creation and validation record

Date: 2026-09-06. Trigger: Santapong requested further alphaXiv research to improve v1.1–v1.8 and a reusable skill for knowing where to read and how to adapt research into the project.

## Durable lesson and scope

**Observed:** the earlier discussion identified ZIP names before reading their contents. The actual downloaded archive contained eight Revision 3 SDDs and a research program with substantial existing coverage. Research based only on the conversation could duplicate that design.

**Verified:** direct archive inventory, matching manifest entries and document revision fields established the baseline. Selective primary reading then exposed useful mechanisms and limits that would be lost in abstract-only synthesis. WHALE's test-selection algorithm and SCAPE's appendix quantile/data-role details were corroborated against versioned primary PDFs.

**Lesson:** inspect the concrete project baseline, trace material claims to primary sections, map assumptions to existing owners, and require a falsifiable target-project comparison. Keep author results, design proposals and local measurements distinct.

**Destination:** a reusable research procedure, not global project-status memory or execution policy.

## Files created or changed

Installed `/home/santapong/.codex/skills/research-to-design/` with:

- `SKILL.md`: baseline inspection, primary reading, adaptation, synthesis and evidence checks.
- `references/reading-map.md`: which paper sections and code paths answer which questions.
- `references/adaptation-template.md`: source cards, keep/change/reject decisions and experiment/SDD mapping.
- `references/research-writing.md`: contribution-led report and manuscript structure, with claim discipline.
- `agents/openai.yaml`: discoverable skill metadata and an invocation example.

Updated only the routing paragraph in `/home/santapong/.codex/skills/alphaxiv-research/SKILL.md`, replacing the stale unavailable-connector assertion with live capability discovery and a pointer to the complementary adaptation skill. The original is preserved at `skill-change-records/alphaxiv-research.before.md`. Existing library conventions were retained. No global AGENTS.md or project memory was changed.

Usage: **Use $research-to-design to read these papers against this project revision and produce an evidence map, proposed changes and the smallest useful experiment.** The alphaXiv skill continues to handle discovery and library filing.

## Checks completed

Both skill folders passed the skill-creator validator. This checks structure, names, frontmatter and unfinished scaffold content; it is not proof of research quality.

The new workflow was applied to the actual Accretion package and 12 papers in this task. Its output separates already-covered requirements, new deltas, rejected transfers, exact baseline sections and follow-up experiments. This was a local forward application, not an independent subagent evaluation or evidence of broad skill generalization.

`checks/calibration_rank_check.py` enumerates possible ranks under fixed-score exchangeable distinct residuals. At 15 calibration points and 95% target coverage, clamping to the largest calibration score covers 15/16 possible ranks, or 93.75%; full-support handling covers all ranks. It also checks 6, 19 and 99 points. This is an exact mathematical edge-case check, not a simulation of robot behavior or reproduction of SCAPE.

The original manifest's 28 payload hashes matched, all eight SDDs remained at document revision 3, and the 12 source versions matched the retrieved primary evidence. Document and skill links were checked. The portable archive includes a file manifest and a packaging validation record; its contents are checked after creation. No research acceptance criterion is marked passed on the basis of these checks. No Accretion runtime tests were needed because this task changed research artifacts and skills only.

## Evidence filed

Five papers used in this review were added to **Accretion — evaluation and assurance** in the [alphaXiv library](https://www.alphaxiv.org/bookmarks): HarnessOpt-Bench, HarnessLens, WHALE, CoAdapt-GUI and SCAPE. Their inclusion means they informed the review, not that their conclusions were accepted without limits. All 12 reviewed papers have local source notes.

## Boundaries and future review

The new skill is installed with discovery metadata. Its scope is research interpretation, design adaptation and evidence-backed writing. It adds no authority for paid experiments, training, GitHub writes, deployment or physical actions. The document proposals remain unintegrated and unmeasured.

Review the skill after its next materially different project: check whether baseline discovery, paper-section choices and the adaptation cards changed a useful decision. Revise only for observed friction. The dated Accretion findings belong in this report; they are not permanent facts embedded in the skill.
