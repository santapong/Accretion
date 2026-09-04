# Operator UI source tree runbook

How the React operator UI under `apps/ui/src/` is laid out after v0.3.1 M9, and the two
rules that keep it that way. The rendered contract — routes, headings, accessibility —
is in the [frontend guide](../guides/frontend.md); this document is about where code
lives and why.

## One table, two consumers

`src/routes.tsx` exports `RouteEntry` and `ROUTES`: sixteen declared paths plus the `*`
fallback, in the order the router matches them. Fifteen carry a `label`, which is the
navigation text.

`src/OperatorShell.tsx` is the only consumer. It derives the navigation bar from
`ROUTES.filter((route) => route.label)` and the router's children from all of `ROUTES`.
Before M9 those were two hand-synced lists in `App.tsx` — a `navigation` array of
`[path, label]` tuples and a literal `<Routes>` body — and nothing checked that they
agreed.

**Adding a screen** is therefore: a component under `src/pages/`, and one row in
`ROUTES`. The navigation entry and the route come from that row. Omit `label` only for a
path that must not be offered as a destination; today that is `/runs/:runId`, reached
from a run link, and `*`.

Two properties of the shell are deliberate and tested in `src/OperatorShell.test.tsx`:

- `end` is **derived** at the call site as `route.path === "/"`, never stored per row.
  Under react-router 7 it is belt and braces — that version's prefix match also requires
  a separator after the `to` path, so `to="/"` is not marked current on `/tasks/new`
  even with `end` removed. Flipping it to `end={false}` nevertheless fails
  `OperatorShell.test.tsx`, which pins the derivation structurally with
  `expect(shellSource).toMatch(/<NavLink end=\{route\.path === "\/"\}/)`; a stored
  per-row flag is caught by `expect(routesSource).not.toMatch(/\bend\s*[?:]/)` and, for
  a quoted key, by tsc's excess-property check against `RouteEntry`. It is kept because
  it costs nothing and stays correct under a router without that guard. Stored per row it
  would be wrong for fourteen of fifteen rows with nothing able to tell. What the test
  really pins is the pairing: each nav link's `to` comes from the same row as its label.
- The shell contains **no literal `path="`**, and exactly one `<Route>` element, the
  mapped one. Every path reaches the router through `ROUTES`, so a route added as a
  stray `<Route>` beside the map — reachable but absent from the navigation — fails the
  suite instead of shipping. The literal regex alone would miss `path={'/x'}`, so the
  test also counts `<Route` occurrences and pins the mapped element's exact text.

**No page may import `routes.tsx`.** Every element in the array is constructed there, so
a page importing `ROUTES` would be a circular value import. The two "back to" links in
the app, on the dashboard and the history page, stay string literals for that reason.

## Where a declaration belongs

| Location | Holds |
|---|---|
| `src/App.tsx` | Thirteen lines: `BrowserRouter`, the shell, and the two stylesheet imports. Nothing else. |
| `src/OperatorShell.tsx` | The navigation bar and the router outlet, both derived from `ROUTES`. |
| `src/routes.tsx` | `RouteEntry` and `ROUTES`. No component export, so `react-refresh/only-export-components` stays quiet. |
| `src/pages/<Name>Page.tsx` | One screen per file, named export, with its private sub-components. The M6 convention, extended to the eleven pages M9 moved out of `App.tsx`. |
| `src/RunExecution.tsx`, `src/EventStream.tsx` | Large shared surfaces that are not routes. They sit at the root beside their pinned tests. |
| `src/runState.ts`, `src/runDuration.ts`, `src/runBadges.ts`, `src/pages/formLines.ts` | Plain `.ts` helpers. Never co-located with a component: a module exporting both a component and a helper trips `react-refresh/only-export-components`, which `npm run check` reports as a warning nobody sees. |
| `src/StatePill.tsx` | One component, imported by ten declarations. |

### ADR3-M9-001 — the route table is the single source of nav and Routes

**Status:** accepted, v0.3.1 M9.

**Context.** `App.tsx` had grown to 1,109 lines holding eleven page components, eight
shared pieces, the navigation array, the shell and the routes. PR5 (Tailwind migration),
PR6 (store and motion) and PR7 (the cosmic scene) all need to change one screen at a
time, which that file shape does not allow. Two questions had to be settled before
moving anything: where pages live, and how the navigation and the router agree.

The SDD suggests `src/features/<domain>/` for frontend code — the frontend layout
sketch at [SDD v0.2](../sdd/Accretion_SDD_v0.2.md) line 1008, carried forward to
[v0.3](../sdd/Accretion_SDD_v0.3.md) line 1725. M6 had already put its five
administration screens in `src/pages/` without recording a decision, so the repository
had a convention the SDD did not describe.

**Decision.** `src/pages/<Name>Page.tsx` is the page-tree convention, extending M6 rather
than introducing a third layout. `src/routes.tsx` holds one `ROUTES` array; the
navigation bar and the `<Routes>` body are both derived from it, and `end` is derived at
the call site.

**Why.** A `features/` tree buys domain grouping, and this UI has no domains to group:
seventeen routes, one API client, one stylesheet. Adopting it would have meant moving
M6's five screens as well, which is churn inside a PR whose entire claim is zero
behaviour change. `pages/` also matches how the routes read.

Deriving both lists from one array removes a class of defect the gates could not see. A
route with no navigation entry is unreachable and every check stays green; a navigation
entry with no route lands on the 404 page and every check stays green. The count in
`App.test.tsx` catches a mismatch in the *total*, not a mismatch in the *pairing*.

**Consequences.** `docs/sdd/` is hash-manifested and must not be edited, so this record
is the divergence note; §1008 and §1725 keep their `features/` suggestion. A future
frontend that does grow domains should revisit this rather than treat `pages/` as
permanent.

## Two traps this layout sets

**The stylesheet cascade is import order.** Retold for `react-flow.css`, which M9 PR5c
created when it deleted `styles.css`. Until PR5c the trap was that `styles.css` was
unlayered and beat Tailwind's layers regardless of order, while React Flow's sheet had to
stay ahead of it. The file is gone; the trap is not, because the reason it existed was
never `styles.css`.

`@xyflow/react/dist/style.css` is unlayered, declares no `@layer` and no `!important`, and
styles `.react-flow`, `.react-flow.dark` and `.react-flow__*`. Our rules for elements
inside the canvas therefore cannot be layered — a layered rule loses to an unlayered one at
any specificity — and several of them (`.projection-node-kind-gate` and
`.projection-node-kind-terminal`, both `(0,1,0)` against xyflow's `.react-flow__node-default`)
beat it only by coming **later** at equal specificity. So they live unlayered in
`apps/ui/src/react-flow.css`, imported from `RunExecution.tsx` on the statement immediately
after the xyflow one, and `App.tsx`'s single `./theme.css` import still has to sit after
`./OperatorShell` — that import is what reaches `RunExecution.tsx` and pulls both unlayered
sheets into the cascade ahead of the layers. The built cascade is xyflow →
`react-flow.css` → layers. Hoisting the `theme.css` import to the top of `App.tsx`, which
looks tidier, puts xyflow's rules after ours; splitting the two adjacent imports in
`RunExecution.tsx` puts ours before xyflow's. Either inverts every same-specificity tie on
the canvas with the page still rendering.

Four gates hold it, at three different levels. `OperatorShell.test.tsx` asserts the shell
import precedes `./theme.css`. `cssPort.test.ts` asserts the import adjacency in
`RunExecution.tsx`'s source text, that `react-flow.css` contains no `@layer`, and that the
canvas rules are in it and nowhere else. `style-diff.spec.ts` fetches the BUILT stylesheet
from the preview server and asserts xyflow's `.react-flow__node-default` precedes
`.projection-node.react-flow__node-default` and that neither that rule nor `button:disabled`
is inside an `@layer` block. The computed-style diff against the merge-base is the fourth
and reports the pixels. The one-off sha256 proof recorded for M9 PR4 (`d0f90429…`,
3 Sep 2026) is spent: the port changed the stylesheet text by design, and these are what
replaced it.

`button:disabled` is in `react-flow.css` and looks out of place there. It is not: xyflow
sets `.react-flow__controls-button { cursor: pointer }`, and `button:disabled` at `(0,1,1)`
beats it only while both are unlayered. PR5a moved it into the layer and the style diff
reported `/runs/:runId @ 390: #148 button cursor not-allowed -> pointer` — the disabled
zoom control had stopped saying it was disabled.

**Prose in a `.tsx` file can change the built CSS.** `src/theme.css` declares
`@source "./**/*.tsx"`, and Tailwind's scanner extracts candidates from the whole file,
comments included. A doc comment that uses a bare utility name as an English word — the
words for a block-level element or a data grid are the two that bit PR4 — adds
`.block{display:block}` or the grid equivalent to the emitted stylesheet. It is 42 bytes
and harmless, but it breaks a byte-identical-CSS claim and is invisible in review.
Rephrase, or expect the hash to move.

## Related

- [Frontend guide](../guides/frontend.md) — the rendered contract, accessibility
  findings, and the bundle budget.
- [Frontend and administration runbook](v03-frontend-admin.md) — operating the M6
  administration screens.
