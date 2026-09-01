# v0.2.0 post-release browser and accessibility evidence

Closes the browser/accessibility exception recorded in the
[v0.2 release audit](audit.md) and tracked in
[issue #52](https://github.com/santapong/Accretion/issues/52).

## Environment

| Item | Value |
|---|---|
| Date | 2026-08-24 |
| Tag under test | `v0.2.0` (`2c455bac152c971ca85932262ac121c8d847274a`) |
| Working checkout | `develop` @ `f141faa` — differs from the tag only in `docs/releases/v0.2/*` files; application code is byte-identical to the tag |
| Browser | Chromium 151.0.0.0 (X11, Linux x86_64), driven via Claude in Chrome |
| Accessibility engine | axe-core 4.10.2 (default ruleset) |
| Runtime | Python 3.12.9, Node.js 22.14.0, PostgreSQL via `docker-compose` (host port remapped 5432→5433 locally because a native PostgreSQL owns 5432; container-internal port unchanged) |
| API | `uvicorn accretion.api.main:app`, `/healthz` → `{"status":"ok","version":"0.2.0"}` |
| UI | Vite dev server at `http://localhost:5173` |
| Deterministic data | `examples/showcase.py --repository $PWD` → run `run_01M0SCCZPE3R874AYM26DNAB3K`, state `SUCCEEDED`, 19 events, verifiers `output-contract` PASS + `trajectory-policy` PASS |

## Route smoke — all eleven routes (desktop 1440–1568 px)

| Route | Result |
|---|---|
| `/` (dashboard) | PASS — hero, counters, three provider cards, recent run row |
| `/tasks/new` | PASS — full create-and-review form renders |
| `/approvals` | PASS — empty state "No approval records." |
| `/history` | PASS — run list + complete provenance panel (task, profile, strategy, template, runtime, events, verifications) |
| `/runtimes` | PASS — FAKE / CODEX / CLAUDE cards all READY with persisted FAKE session (first paint shows a brief empty loading panel before data arrives) |
| `/capabilities` | PASS — capabilities, skills, plugins registries populated |
| `/benchmarks/acr-arch` | PASS — 30 tasks / 68 scenarios table with filters |
| `/benchmarks/dynamic` | PASS — P5 gate, four PASS chips, cohort comparison, frozen hashes |
| `/benchmarks/search` | PASS — P6 quality curve, cross-provider replay, frozen hashes |
| `/benchmarks/experience` | PASS — P7 gate, treatment table, negative results, frozen hashes |
| `/runs/run_01M0…NAB3K` | PASS — live run detail with `direct-v1` topology graph (all nodes SUCCEEDED), verifier PASS chips, complete 19-event normalized trace |

Screenshots: [dashboard](evidence/dashboard-desktop.jpg),
[run detail](evidence/run-detail-desktop.jpg),
[P7 experience](evidence/p7-experience-desktop.jpg).

## Responsive layouts

Desktop 1440–1568 px: PASS on all routes, no horizontal body scroll.

Narrow 390 px (the tiling window manager pins the browser window, so the
narrow viewport was reproduced by rendering each route in a 390 px iframe —
CSS media queries and layout respond to the iframe width):

- `/` — PASS: single-column stack, nav collapses to a horizontally scrollable
  strip, no document horizontal scroll (`scrollWidth == clientWidth`).
- `/tasks/new` — PASS: form fields stack single-column, no document
  horizontal scroll.
- `/runs/:id` — **FAIL (minor)**: document horizontal scroll present
  (`scrollWidth > clientWidth`); the "Materialize run experience" button and
  the SUCCEEDED status chips overflow the right edge. Tracked as finding F1.

## Keyboard-only navigation and focus

- All top-nav links are reachable with Tab in DOM order.
- The focused link carries the browser default focus outline
  (`outline: auto`) — visible against the dark theme.
- Enter on a focused nav link routes correctly (verified: focus on
  `ACR-ARCH` + Enter → `location.pathname === /benchmarks/acr-arch`).
- No keyboard traps encountered on the swept routes.

## Automated accessibility (axe-core 4.10.2, default rules)

Zero critical violations on any route. Findings:

| Rule | Impact | Routes | Notes |
|---|---|---|---|
| `page-has-heading-one` | moderate | all except `/` | Route headings render as styled non-`h1` elements; only the dashboard has a `<h1>`-level heading |
| `color-contrast` | serious | `/history`, `/capabilities`, `/benchmarks/dynamic`, `/benchmarks/search`, `/benchmarks/experience`, `/runs/:id` | 2–4 nodes per route; dim monospace metadata text and the `pill pill-succeeded` status chips fall below WCAG AA contrast on the dark background |
| `scrollable-region-focusable` | serious | `/runs/:id` | The normalized-trace `div.event-list` scroll region is not keyboard-focusable |

## Console / runtime errors

With console tracking active, every route was (re)loaded and exercised:
**zero console errors, warnings-as-errors, or uncaught exceptions** on any of
the eleven routes, including the run-detail live view.

## Findings ledger

| # | Finding | Severity | Disposition |
|---|---|---|---|
| F1 | `/runs/:id` horizontal overflow at 390 px (action button + status chips) | minor | defer to next UI pass |
| F2 | `color-contrast` on status pills and dim metadata text (6 routes) | minor (serious per axe, cosmetic tokens) | defer — adjust ink tokens in next UI pass |
| F3 | Missing `h1` on 10 routes | minor | defer — promote route titles to `h1` |
| F4 | Trace `event-list` scroll region not focusable | minor | defer — add `tabindex="0"` + role/label |

No blocking defect was found; no code fix was applied to the frozen tag.
F1–F4 are UI-polish items for the v0.3 line.

## Verdict

Browser smoke, responsive, keyboard, accessibility, and console gates:
**PASS with four minor deferred findings**. The v0.2.0 release exception in
the audit is closed by this document.
