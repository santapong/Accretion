---
name: alphaxiv-research
description: Find, read, verify, and file academic papers for robotics and embodied-AI research. Prefer the alphaXiv connector when it is available; otherwise use primary paper sources such as arXiv and official project repositories. Use for literature reviews, paper provenance, benchmark or SOTA claims, researchers, labs, or code behind a paper.
---

# alphaxiv-research — verified paper research

## Codex routing

Inspect the live tool list and schemas, then prefer the connected alphaXiv tools when available. A dated setup note does not establish present connector availability. If unavailable, continue with primary arXiv papers and official project repositories, and distinguish unavailable library filing from available paper research. Never invent tool names or use secondary summaries as primary evidence.

Use the connection provided to the current session. Do not inspect, copy, or transplant another application's OAuth grants or credentials.

For paper-to-project adaptation, SDD improvements, or a structured research manuscript, also use the `research-to-design` skill. It adds baseline inspection, section-level reading, evidence-to-design mapping, and falsifiable experiment plans; this skill remains the owner of discovery and library operations. An AI-generated intermediate report is a navigation aid. Verify material claims against raw primary methods, evaluation sections and relevant appendices.

## Reach for it first

alphaXiv indexes ~2.5M arXiv papers: computer science, maths, physics, statistics,
quantitative biology and finance, electrical engineering. **It does not cover biomedical,
clinical or life-science literature** from PubMed, Cell or Nature — those still go to
`WebSearch`.

Searching only secondary web summaries for an arXiv paper is the failure mode this skill exists to prevent.
Answering a research question from memory alone is the other one. Both produce confident
citations to papers that do not exist.

## Tool routing

| Need | Tool |
|---|---|
| find papers on a topic | `discover_papers` |
| read a paper you have an id for | `get_paper_content` |
| interrogate a specific PDF | `answer_pdf_queries` |
| who works on X / a lab's roster / where someone is now | `find_researchers`, `get_researcher`, `get_researcher_papers` |
| the code behind a paper | `read_files_from_github_repository` |
| the user's own collections | `list_library`, `save_papers_to_folder`, `create_folder` |

In Claude these are prefixed `mcp__claude_ai_alphaXiv__`; in Codex, discover the connected
tool names instead of assuming that prefix.

**Addressing:** results come back as `[ID=<id>]` and `[SLUG=<slug>]`. Pass those values
back to the tools, but **never show the bracketed handle to the user** — render papers as
`https://www.alphaxiv.org/abs/<id>` and people as `https://www.alphaxiv.org/@<slug>`.

## Library conventions

The library lives in the alphaXiv account (nothing on disk, nothing on the Pi) and the web
view is **https://www.alphaxiv.org/bookmarks**. Folders do not have verified deep links; a
path like `/bookmarks/<folder_id>` returns 404, so send the user to `/bookmarks` and name the
folder rather than inventing a URL.

Topic folders mirror the notes in `/mnt/data/company/research/xembodiment-lit/notes/`, so a
saved paper always has a note it belongs to:

| Folder | Note it feeds |
|---|---|
| Evaluation & failure | `evaluation-and-failure.md` |
| Cross-embodiment transfer | `cross-embodiment-transfer.md` |
| VLA frontier | `frontier-2025-26.md` |
| Fine-tune own arm | `finetune-own-arm.md` |
| Surveys & field maps | `survey.md` §4b |
| World models | `world-models.md` |
| Humanoid whole-body control | `humanoid-whole-body.md` |
| Data & simulation | `finetune-own-arm.md` §6b |
| Reactivity & action chunking | thesis thread, no note yet |
| Safety & refusal | thesis thread, no note yet |

A note may carry papers that were filed but **not read**. Mark those sections "not verified" and
never cite them as sourced; that discipline is already in force across this workspace.

`Want to read`, `Reading`, `Completed` and `My publications` are the defaults, and they are
**folders, not exclusive states** — a paper sits in a topic folder and a status folder at
once. Do not nest deeper than this.

Save on read, not on sight. A paper enters the library once it has been used to answer
something; a folder of unread titles is worth less than the three papers that settled a
question. Ask before a bulk save of more than about five papers.

## Read through the evaluation lens

The user's research edge is **evaluation rigor and failure measurement**, not model training.
Their thesis candidates are failure detection and recovery, reactivity versus action chunking
(mid-chunk abort), and safety and refusal.

So when a paper reports results, the interesting part is usually **how it was measured**, not
the headline number. Report the protocol: how many trials, on what hardware, who scored
success, whether the eval was real or simulated, and what the paper declines to measure. A
success rate with no protocol is not a result. Papers that measure honestly and papers that
measure badly are both useful here, for opposite reasons.

The hardware constraint bounds what is worth reading closely: a laptop with 8 GB and no
discrete GPU, fine-tuning only on rented hours. Methods needing a cluster are context, not a
plan.

## Scouting supervisors

MSc applications to JEMARO and XJTLU are live, so people matter as much as papers.
`get_researcher_papers` shows what a prospective supervisor has actually published lately, as
opposed to what their lab page claims. Flag the gap when it is large: a lab advertising a
direction nobody there has published in for three years is a real finding.

## Where the findings go

- **The literature workspace** is `/mnt/data/company/research/xembodiment-lit`. Anything that
  belongs to the standing field map lands in a note there, not in a chat summary. Notes are
  terse, every number carries an arXiv id, and each ends with Limitations and Practical Notes.
  New entries go in `paper/references.bib` as arXiv `@misc` keys like `kim2024openvla...`.
- **Multi-source claims that need adversarial checking** require a cited synthesis from
  independent primary sources. Use the deep-research skill only when the user explicitly asks
  for Deep research; otherwise perform the focused verification in this workflow.

## Reporting

Cite every non-obvious claim with the paper it came from. When the literature disagrees,
say so and name both sides rather than averaging them into a bland consensus. When a search
finds nothing, report that as a result — an empty result on a well-formed query is real
evidence about the field, and is more useful than a plausible guess.
