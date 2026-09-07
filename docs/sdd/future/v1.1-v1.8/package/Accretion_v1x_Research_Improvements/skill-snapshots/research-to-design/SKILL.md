---
name: research-to-design
description: "Critically read research papers, map their methods into a project's architecture and experiments, and write evidence-backed research briefs or manuscript sections. Use for paper-to-project adaptation, SDD improvements, and research synthesis; use alphaXiv research for retrieval and library operations."
---

# Research to Design

Turn a paper into a bounded, testable design decision. Keep three things distinct: what the authors measured, what the project already does, and what the proposed adaptation still needs to prove.

## Establish the real baseline

Read the actual files before proposing changes. For shared conversations or archives, inventory attachments, identify the latest applicable document revision, inspect the README/manifest and relevant documents, and record what remains inaccessible. An attachment name or a previous chat summary does not establish its contents. Extract archives into an isolated directory without executing their contents.

For repository work, record the current commit, branch, dirty state, local instructions, relevant design sections and implementation owners. A future SDD, a design validator PASS, an implementation test and a measured research result are different evidence classes. Keep dated project state in the research report, outside this reusable skill.

Define the decisions the research must resolve. For each release/component, record its existing mechanism, remaining question, scope and constraints. Mark ideas already covered before searching for additions. Do not turn more literature into more product scope by default.

## Find and read evidence

Use the available alphaXiv connector and its current tool schemas; the `alphaxiv-research` skill handles discovery, source provenance and library filing. Inspect live capabilities instead of inheriting a stale availability claim. Respect connector search limits, batch related discovery questions, and send known titles/IDs directly to the paper reader. Fall back to primary papers and official repositories when needed.

Read [reading-map.md](references/reading-map.md) when selecting sections or auditing a claim. Discovery summaries and AI-generated intermediate reports locate evidence; material design claims require the raw primary method, evaluation and relevant appendix. Record selective reading honestly instead of calling it a full-paper review.

When equations, tables or algorithms drive a decision, check their definitions, edge cases and implementation. If extracted text conflicts, is truncated, or would justify a serious criticism, corroborate it against the versioned primary PDF/HTML. Unavailable code stays unverified; a repository README is not a code audit.

For each load-bearing source, capture version, inspected pages/sections, mechanism, assumptions, baseline strength, evaluation unit, splits, sample/repeat counts, verifier, uncertainty, total cost and limitations. Separate source existence, source support, implementation inspection and independent reproduction. Missing details remain unknown.

## Translate rather than copy

Use [adaptation-template.md](references/adaptation-template.md) for the evidence-to-design map. Each material proposal needs:

1. An observed project gap and its file/section or implementation owner.
2. A source-supported mechanism, including required inputs and observability.
3. The smallest project-specific change, with inherited behavior and rejected parts named.
4. A falsifiable comparison, an independent outcome measure, negative controls and total-cost accounting.
5. A decision: retain, clarify, propose amendment, defer with a trigger, or reject.

Separate confirmation of an existing design from a new addition. Do not duplicate routers, planners, verifiers, evidence stores or authority owners. For unreleased designs, distinguish a document revision from a product patch version. An amendment proposal does not claim that all SDDs, schemas, event catalogs or traceability files have already been updated.

Prefer the cheapest sufficient intervention: a deterministic rule, clearer data contract or better benchmark may resolve the gap before a learned model. Compare a mature current baseline; weak seeds can exaggerate apparent gains. Adaptation across tasks, runtimes or embodiments is a hypothesis until evaluated in the target setting.

## Write the research output

Organize synthesis by decision or mechanism, not by a procession of paper summaries. Explain agreements, conflicts, rejected methods and remaining uncertainties. Cite primary versions near claims and identify project adaptations as proposals. Keep an evidence register and concrete next experiment alongside a concise user-facing brief.

When the user wants a manuscript or paper section, read [research-writing.md](references/research-writing.md). If local experiments do not exist, write a proposal, related-work synthesis or methods plan; never invent results, statistical significance, novelty, authors, affiliations or completed evaluations.

Stop broad discovery when each material recommendation has primary support and a bounded test, or when the remaining gap requires project data rather than another paper. Record unread leads separately. Do not repeatedly search to make an inconclusive direction appear conclusive.

## Check the deliverable

- Can a reader trace each proposed change to both primary evidence and the actual baseline?
- Does each central result retain its denominator, comparator, uncertainty and domain limits?
- Did method, appendix and inspected code agree? Preserve conflicts and their consequences.
- Are train/search, model selection, calibration and final evaluation roles explicit where the method needs them?
- Are adaptive sampling, repeated selection, delayed labels and tiny-sample limits handled by the chosen analysis?
- Does the recommendation count rejected candidates, verification, training, serving, human work and realistic amortization?
- Could the smallest comparison falsify the proposal, and would a negative outcome stop or narrow it?

Validate local links and artifact identities. A structure check establishes consistency, not scientific truth. Research and skill creation confer no new permission for paid experiments, publishing, deployment, credential changes or physical-device control.
