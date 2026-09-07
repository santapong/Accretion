# Diagram delivery and verification

**Date:** 6 September 2026. These checks concern diagrams and documents, not the runtime or safety of Accretion.

## Cumulative architecture

- `diagram_type`: architecture
- `output`: `/mnt/data/accretion-v1.8-review-2026-09-06/architecture.html`
- `specification_sha256`: `93b73fe04955fdf6841575be238a1dbb809c410e90f06c43c6938aa9549d43d0`
- `artifact_sha256`: `2bef0304339ce7878d61a5507e8d2d0f82ba0f1cc2a4f573575074291ee7e0c0`
- `validation`: **9/9 showcase checks, 0 errors, 0 warnings**
- `browser_evidence`: **passed**, final artifact-bound Archify `visual-check` receipt
- `visual_review`: **passed**, rendered light/dark screenshots inspected with an image reader
- `correction_rounds`: **1** focused visual correction, shortening redundant title/card text after laptop overflow

The final automated run used the available Brave Chromium executable. It measured 1440×900, 1600×1000, 1920×1080 and 2048×1320. All measured desktop dimensions fit without page overflow. Light/dark captures at both endpoint sizes are retained. The large desktop composition and laptop views were inspected for labels, paths and card fit. The viewer supports zoom and focus for detailed reading; browser measurements do not establish scientific or runtime validity.

See [deterministic validation](architecture.validation.json), [automated browser evidence](architecture.visual-check.json) and [contact sheet](architecture.visual-check.html). The PNG is a browser capture of the visible diagram; the original interactive HTML remains unchanged after delivery.

## User journey and physical-trial detail

Both are delivered as standalone HTML containing inline Graphviz-generated SVG, with separate SVG/PNG exports and editable DOT source.

- Graphviz rendering: **success**, no renderer warnings or errors.
- Browser containment and text bounds: **passed** at 1440×900, 1600×1000, 1920×1080 and 2048×1320 for each view.
- Perceptual review: **passed**, final rendered PNGs inspected for text clipping, flow and mandatory gate ordering.
- Source mapping: the user journey preserves independent acceptance, bounded digital recovery, non-success outcomes and human promotion; the physical view preserves static-fallback evaluation, preflight, freeze, exact approval, existing-owner arming, deterministic safety and separate outcomes.

The full user journey intentionally includes an optional physical branch; the recovery edge is explicitly limited to eligible digital failures. The static physical fallback still passes the simulation/preflight chain. The drawings summarize state transitions: a new physical manifest after invalidation is a new trial chain, never an automatic retry.

See [render receipts](workflow-render-receipts.json) and [browser measurements](workflow-browser-checks.json). These SVG workflows do **not** claim Archify showcase certification.

## Workflow-renderer fallback disclosure

The first Archify workflow candidates could not satisfy their branch-routing constraints. The remaining diagnostics were `workflow/explicit-pin-conflict` for `verify_result` and `workflow/route-preset-conflict` for `approve_blocked`. Those candidates were not delivered as successful HTML and were not used for browser acceptance.

The [Archify skill](/home/santapong/.agents/skills/archify/SKILL.md) instructs: “If two consecutive rounds do not improve that best count, stop and report the unresolved diagnostics truthfully.” That bounded repair loop was stopped. The user-facing workflow diagrams were then completed through Graphviz and checked separately. The [rejected user-journey diagnostic](authoring-diagnostics/user-journey.validation-rejected.json) and [rejected physical-trial diagnostic](authoring-diagnostics/physical-trial.validation-rejected.json) remain available for traceability; they are not final diagram sources.

## Review and source integrity

The final review's 18 external local-document references resolved at creation. `source-manifest.json` records their observed hashes. The untouched Revision-4 package still matched all 68 payload hashes in its own manifest. The repository was clean at `84c3eede99c171e1f13fa37a19d15fa978e0830d` at the final read-only recheck.

The review document is intentionally scrollable. Source links refer to the existing local SDD/repository documents; those external documents are not duplicated in this review ZIP. The diagrams and their internal navigation remain self-contained after extraction.
