# v0.3.0 browser and accessibility evidence

> [!IMPORTANT]
> **Superseded as the sole evidence, and now re-measured automatically.** This document
> records a hand-driven Chromium session on 2026-09-01 and remains the record of what was
> true at the `v0.3.0` tag. Since M9 PR2 the same procedure runs in CI on every push —
> `apps/ui/e2e/a11y.spec.ts`, the `browser` job — against the production build rather than
> the dev server, with axe-core 4.13.0 in place of 4.10.2. See
> [the frontend guide](../../guides/frontend.md) for how to run it.
>
> The contrast sweep below covered 16 routes and 1,421 text nodes; the gate covers all 17.
> Its node count is **not** a fixed figure and is deliberately not asserted as one:
> `examples/showcase.py` is additive, so every seeding adds a run to the dashboard and
> history lists and with it more text nodes. Two consecutive local runs measured 1,979 and
> 2,155. What the gate asserts is what actually matters — zero failures and, just as
> importantly, **zero skipped** nodes, since a node whose background could not be resolved
> is unmeasured rather than passing.

Closes the four inherited findings F1–F4 carried from the
[v0.2 post-release validation](../v0.2/browser-a11y-evidence.md) (issue #52 /
PR #55) and recorded under "M8 inherited UI findings" in
[backlog.md](backlog.md).

jsdom has no layout engine and `apps/ui/src/test/setup.ts` fakes
`getBoundingClientRect`, so F1 (overflow) and F2 (contrast) cannot be decided by
the vitest suite — an assertion there would be measuring the fake. They are
measured here in a real browser. F3 and F4 are additionally locked in by
`apps/ui/src/accessibility.test.tsx`, which runs on every pull request.

## Environment

| Item | Value |
|---|---|
| Date | 2026-09-01 |
| Working checkout | `feat/v03-m8` @ `5668639` plus the F1–F4 fixes in this PR |
| Browser | Chromium 151 (X11, Linux x86_64), driven via Claude in Chrome |
| Accessibility engine | axe-core 4.10.2, default ruleset, `resultTypes: ["violations"]` |
| API | `uvicorn accretion.api.main:app` on `:8000`, `/healthz` → `{"status":"ok"}` |
| UI | Vite dev server at `http://localhost:5173` |
| Deterministic data | `examples/showcase.py --repository $PWD` → run `run_01M1DFJKWS70DY6121R6V7DA2K`, `SUCCEEDED`, verifiers `output-contract` PASS + `trajectory-policy` PASS |
| Narrow viewport | 390 px reproduced by rendering each route in a 390 px iframe, as in the v0.2 evidence: the tiling window manager pins the browser window, and CSS media queries and layout respond to the iframe width |

Screenshot: [run detail, desktop](evidence/run-detail-desktop.jpg).

## axe-core: 17 routes, zero violations

Every route was visited through the SPA router with axe re-run after each
navigation.

| Route | Violations | h1 |
|---|---|---|
| `/` | 0 | 1 |
| `/tasks/new` | 0 | 1 |
| `/runtimes` | 0 | 1 |
| `/history` | 0 | 1 |
| `/approvals` | 0 | 1 |
| `/capabilities` | 0 | 1 |
| `/benchmarks/acr-arch` | 0 | 1 |
| `/benchmarks/dynamic` | 0 | 1 |
| `/benchmarks/search` | 0 | 1 |
| `/benchmarks/experience` | 0 | 1 |
| `/runs/run_01M1…7DA2K` | 0 | 1 |
| `/admin/connections` | 0 | 1 |
| `/admin/plugins` | 0 | 1 |
| `/admin/mcp` | 0 | 1 |
| `/admin/capabilities/inspect` | 0 | 1 |
| `/admin/identity` | 0 | 1 |
| `*` (404) | 0 | 1 |

**Total: 0 violations across 17 routes.**

## F1 — `/runs/:id` horizontal overflow at 390 px — FIXED

Measured as `documentElement.scrollWidth > clientWidth`, plus an enumeration of
every element whose right edge exceeds the viewport and which has no scrollable
ancestor (an element clipped by its own scroll container is not an overflow).

Before: `scrollWidth 405` vs `clientWidth 375`, 46 unclipped offenders.
After: `scrollWidth 375` vs `clientWidth 375`, **0 unclipped offenders**.

The backlog attributed F1 to the "Materialize run experience" button and the
status chips. Both are involved, but neither was the root cause on current code:

1. `.inspector-stack` sets `min-width: 0`, but that does not propagate to its
   items. A grid item defaults to `min-width: auto` and so can never render
   narrower than its own min-content, which on this route is set by the React
   Flow projection. `.section-heading` and `.execution-panel` were therefore
   397 px inside a 359 px shell. Fixed by `.inspector-stack > *, .page-stack > *
   { min-width: 0 }` and clipping `.projection-flow`.
2. `.experience-capture` is `grid-template-columns: 1fr auto` at every width,
   and `.secondary-button` is `white-space: nowrap`, giving the `auto` track an
   unshrinkable 226 px floor. Fixed by collapsing to one column below 620 px and
   letting the label wrap.

All 17 routes were then re-measured at 390 px; none scrolls horizontally.

## New finding, fixed here: the M6 admin pages overflowed at 390 px

Not part of F1–F4 — these pages did not exist when the v0.2 evidence was taken,
and no narrow-viewport check has covered them until now.

| Route | scrollWidth before | after |
|---|---|---|
| `/admin/connections` | 816 px | 390 px |
| `/admin/plugins` | 679 px | 390 px |
| `/admin/mcp` | 471 px | 390 px |
| `/admin/identity` | 459 px | 390 px |

Cause: the registry tables render at their natural width with no scroll
container, so the table pushed the document sideways. A wide table belongs in
its own horizontal scroll region; `.registry-card { overflow-x: auto }`.

## F2 — WCAG AA contrast — FIXED, and the finding was partly misdiagnosed

Every text-bearing leaf element on every route was measured from **computed**
styles: foreground `color` against the nearest opaque ancestor background,
WCAG 2.x relative luminance, 4.5:1 required for normal text and 3:1 for large
text (≥24 px, or ≥18.66 px bold).

**Result: 0 failures out of 1,421 measured text nodes across 16 routes.**

Two corrections to the finding as written:

- **The status-pill palette was never the problem.** Measured on their own
  backgrounds the pills pass comfortably: `pill-succeeded` `#8fe1a6` on
  `#183120` = **9.00:1**, `pill-failed` = 8.40:1, `pill-running` = 10.48:1,
  `pill-degraded` = 8.81:1.
- **The pills did nonetheless render below AA**, for a different reason. A pill
  placed directly in a `.section-heading` or `.panel-header` is a `<span>`, and
  `.section-heading > span` / `.panel-header > span` are more specific than
  `.pill-succeeded`, so the pill lost its own colour and rendered as dim
  metadata ink on the pill's coloured background — 3.83:1 and 4.40:1. Fixed by
  excluding pills from those metadata rules with `:not(.pill)`.

The dim monospace half of the finding was real and widespread: 12 distinct ink
values failed, worst `#566058` on the panel gradient at **2.70:1**
(`.event time`) and `#5c675f` at 2.99:1 (`.sequence`). The stylesheet had no
custom properties at all — every colour was a repeated literal — so the fix
introduces three tokens and points the 40 failing text declarations at them:

| Token | Value | Worst ratio on any app surface |
|---|---|---|
| `--ink-dim` | `#828e86` | 4.97:1 |
| `--ink-muted` | `#8d998f` | 5.72:1 |
| `--ink-amber` | `#c2a468` | 6.13:1 |

Two tiers rather than one so the visual hierarchy the failing values expressed
is preserved. Borders were left alone: they are not text, and WCAG's 3:1
non-text requirement applies to the parts of a control that convey state, which
here is the pill's own label and background.

## F3 — missing `h1` — FIXED

The finding says "missing `h1` on every route except the dashboard". That was
true when written; the five `/admin/*` pages added in M6 already ship an `h1`.
The real gap was the eleven route components defined inline in `App.tsx` plus
the 404, all of which titled themselves with `h2`.

All now render exactly one `h1` (verified per route in the table above).
Promoting the titles left `h1 → h3` gaps on six routes, which axe reported as
`heading-order`; the affected section headings were promoted to `h2` and their
metrics pinned so the change is structural rather than visual. `.section-heading
h2` and `.panel-header h2` are element selectors, so both were widened to match
`h1` as well — without that the headings would have fallen back to UA styling.

## F4 — `event-list` not keyboard-focusable — FIXED

`/runs/:id`, measured in the browser rather than jsdom:

| Property | Value |
|---|---|
| `role` | `log` |
| `tabindex` | `0` |
| `aria-label` | `Normalized event trace` |
| `aria-live` | `polite` |
| Reachable by Tab | yes — tab stop 21 of 22 |
| `document.activeElement` after `.focus()` | the region itself |
| Scrollable | yes (`scrollHeight` 1349 > `clientHeight` 560) |
| Scrolls from the keyboard once focused | yes |
| Focus indicator | `.event-list:focus-visible` → 2 px `#75db91`, offset 2 px |

`role="log"` implies a polite live region, but `aria-live` is left explicit so
the behaviour does not depend on assistive-technology defaults.

## What is covered by the test suite, and what is not

`apps/ui/src/accessibility.test.tsx` (15 tests) locks in F3 and F4: one `h1` per
route with the expected name, exactly one `h1` per route, the 404's heading,
that section headings stay at `h2`, and the trace region's role, tabindex,
label and live setting.

It deliberately does **not** assert that focus lands on the region: jsdom does
not move focus onto a `tabindex`-bearing `div` the way a browser does, so a
passing assertion there would be an artefact of the fake environment. That, F1
and F2 are covered by this document instead, and re-measuring them means
re-running the procedure above against a real browser.
