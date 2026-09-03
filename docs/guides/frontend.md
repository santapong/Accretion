# Operator frontend guide

The Accretion frontend is the browser-based operator console for the same typed
HTTP API and durable records used by automation. It is implemented for the full
P0–P7 scope plus the v0.3 M6 administration surface: project and task setup,
deterministic planning, live run control, graph and verifier evidence,
governance, bounded candidate search, verified-experience replay, reproducible
benchmark views, and the Plugins, Connections, MCP Servers, Capability Inspector
and Identity pages.

<img src="../assets/operator-ui-map.svg" alt="Map of the implemented Accretion frontend routes, the central live-run surface, and the authoritative FastAPI snapshot, React Query, and resumable event data flow" width="100%" />

> [!IMPORTANT]
> The frontend is feature-complete for the v0.3 release. Its generated
> contract, lint, TypeScript, component tests, and production build pass.
> Rendered browser and accessibility evidence **is** claimed for v0.3.0: axe-core
> 4.10.2 reports zero violations across all seventeen routes, no route scrolls
> horizontally at 390 px, and no measured text falls below WCAG AA. See
> [browser and accessibility evidence](../releases/v0.3/browser-a11y-evidence.md).
> The v0.2 release exception in issue #52 is thereby discharged.

## What an operator can do

| Surface | Route | Implemented behavior |
|---|---|---|
| Dashboard | `/` | Runtime health, active/recent runs, current system state, and navigation into run evidence |
| New task | `/tasks/new` | Project registration, typed task constraints, budgets, outputs, capabilities, profiling, planning review, and strategy override |
| Live run | `/runs/:runId` | Run controls, read-only React Flow topology, node/loop state, approvals, verifiers, runtime decisions, candidate lineage, P7 materialization/replay provenance, and normalized events |
| Runtimes | `/runtimes` | Provider health, supported runtime versions, pressure, and active session inspection |
| History | `/history` | Durable run history and full audit lookup |
| Approvals | `/approvals` | Pending human gates and explicit approve/deny decisions |
| Capabilities | `/capabilities` | Capability, skill, plugin, and policy registry inspection |
| ACR-ARCH | `/benchmarks/acr-arch` | Frozen v0.1 architecture benchmark filters, task details, utility, regret, and replay evidence |
| P5 Dynamic | `/benchmarks/dynamic` | Frozen static/dynamic cohort comparison, structure/replan metrics, safety, fallback, and release classification |
| P6 Search | `/benchmarks/search` | Frozen N=1/2/4 quality-versus-compute results and provider/null-result comparison |
| P7 Experience | `/benchmarks/experience` | Frozen fresh/success/failure/replay treatments, transfer-safety metrics, task results, and gate reproduction |
| Plugins | `/admin/plugins` | Installed plugin version, state, requested capabilities, grants, connector status, and lifecycle history |
| Connections | `/admin/connections` | Per-connection status, scopes, owner and last health check, with connect, reauthorize, revoke and health actions that never render a credential |
| MCP servers | `/admin/mcp` | Server transport, patterns, discovered tools and capabilities, cache hints, and circuit-breaker state |
| Capability inspector | `/admin/capabilities/inspect` | Resolution of a capability onto its binding, connection, risk and policy, stating the reason when resolution is refused |
| Identity and roles | `/admin/identity` | The caller's issuer, subject and session, plus enterprise-authorization state; read-only, with no way to change a role |

Unknown routes render an explicit not-found page. Live provider actions remain
disabled unless the deployment and project gates permit them.

## Operator journey

1. Open **New task** and register a local Git repository.
2. Create a typed task with objective, constraints, concrete success criteria,
   budgets, output paths, capability bounds, and risk.
3. Inspect the deterministic profile, selected static strategy, evidence, and
   unknown inputs before starting a run.
4. Optionally retrieve and freeze P7 experience, propose and validate a P5
   workflow, and attach a P6 or P7 search before activation.
5. Open **Live run** to follow graph state, loop iterations, candidate branches,
   approvals, verifier evidence, and the resumable normalized event stream.
6. Use **History**, **Runtimes**, **Approvals**, and **Capabilities** to diagnose
   operational or policy state without treating the UI cache as authority.
7. Reproduce ACR-ARCH, P6, or P7 research from its dedicated benchmark page.

## Data and authority model

The UI is intentionally a projection, not an execution authority:

- FastAPI responses are the typed snapshot source of truth.
- `openapi-typescript` generates `apps/ui/src/api/schema.d.ts`; handwritten UI
  types alias that generated contract.
- Styling is mid-migration. `apps/ui/src/theme.css` holds the design tokens and
  the Tailwind v4 layers; `apps/ui/src/styles.css` still holds every rule the app
  actually renders and is deliberately unlayered, so it takes precedence over any
  utility. Tailwind's Preflight reset is not imported yet. Moving a rule therefore
  means deleting it and adding the utility in the same edit — a utility placed
  beside a surviving rule has no effect.
- React Query keys cache entries by durable resource identity and polls active
  projections where needed.
- Live runs load an authoritative audit snapshot first, then subscribe to
  monotonic server-sent events. A sequence gap closes the stream and refetches
  snapshots before reconnecting.
- Buttons request backend transitions. Backend policy, optimistic revisions,
  feature flags, approval rules, budgets, and verifiers decide whether the
  transition is valid.
- Provider messages and frontend labels cannot mark work accepted.

## P5–P7 frontend coverage

| Milestone | Planning surface | Run/research surface |
|---|---|---|
| P5 dynamic workflows | Propose, validate, inspect findings, attach only to accepted graph nodes, activate | Graph revision/runtime decisions plus frozen static-versus-dynamic cohort evidence |
| P6 candidate search | Choose mode, node, branch/parallel limits, and branch/total budgets | Candidate tree with runtime/model/version, reviewer, spend, score, terminal reason, selection, and promotion state |
| P7 verified experience | Retrieve same-repository matches, inspect provenance/compatibility/risk, freeze up to three, choose replay | Explicit terminal materialization, fresh/replay labels, source lineage, safe segment IDs/guidance, revalidation, and frozen transfer gate |

P5, P6, and P7 remain independently disabled by default. The UI can opt a
project in, but it cannot override a disabled deployment flag.

## Run the frontend

From a `develop` checkout after the root installation steps:

```bash
make api
```

In another terminal:

```bash
make ui
```

Open `http://localhost:5173`. Vite proxies `/api` to the local FastAPI service.
Start with the deterministic `FAKE` runtime; signed-in Codex and Claude sessions
are optional local integrations.

## Verify frontend changes

```bash
npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts
npm run check
npm run test
npm run build
```

Accessibility is gated separately, in a real browser, because jsdom cannot decide it. With
Postgres up and migrated:

```bash
npm run build --workspace @accretion/ui
cd apps/ui && npx playwright install chromium && npx playwright test
```

The gate starts its own API and preview server, seeds a deterministic run through
`examples/showcase.py`, then sweeps all seventeen routes: axe-core with its **full default
ruleset**, one `h1` per route, no horizontal overflow at 390 px and no element overflowing
without a scrollable ancestor, and WCAG AA on every text node. Waivers live in
`apps/ui/e2e/allowlist.ts` and expire.

Bundle size is gated by the build itself. `npm run build` prints a budget table -
every chunk's raw and gzip size, the initial-load totals, and a PASS/FAIL line per
rule - and exits non-zero if any rule fails, so the `frontend`, `clean-checkout`
and `browser` CI jobs all enforce it without a step of their own. Four of the
table's rows, with the numbers and the content hash elided - run the build for the
real ones:

```
  initial JS  <raw> B raw / <gzip> B gzip   initial CSS  <raw> B raw / <gzip> B gzip

  PASS  per-chunk-raw:assets/vendor-react-<hash>.js       <measured> B <= <cap> B cap (raw)
  PASS  initial-js-raw                                    <measured> B <= <cap> B cap (raw)
  PASS  chunk-of:node_modules[\\/]react-dom[\\/]          all 1 matching module(s) in chunk "vendor-react"
  PASS  lazy-only                                         no matching modules (vacuous until PR7)
```

The elisions are deliberate. A byte count pasted into a guide is a number nothing
re-measures: the content hash moves on the next dependency bump and every total
moves with it, and no test, gate or CI job can see the page go stale. Measured
numbers live in one place only - every constant in `apps/ui/budget/budget.ts`
carries a comment naming the PR that set it and quoting the measurement behind it,
and `budget/evaluate.test.ts` re-states them so a re-measurement cannot land
without the comment being rewritten in the same edit.

Five rules: no chunk over 500,000 B raw; initial JS and initial CSS under their
raw and gzip caps; `react-dom` and `@xyflow/react` in their named vendor chunks;
and modules matching `LAZY_ONLY_MODULES` reachable only through a dynamic import.
The four initial caps are `ceil(measured x 1.05)` against the M9 PR3 build.

**If the build fails on the budget, raise the cap in the same commit as the change
that needs it and quote the new measured number.** A cap raised in a separate
"fix CI" commit is a number with no justification attached, which is the thing the
file exists to prevent.

The v0.3 release gate records 97 component tests plus successful ESLint,
TypeScript, generated-contract, and production-build checks, and the
accessibility evidence above.

When changing an API response, regenerate the OpenAPI client and commit the
schema diff with the backend change. When changing a surface, add a component
test for the user-visible state and retain semantic labels/regions used by
assistive technology.

## Frontend source map

| Path | Responsibility |
|---|---|
| `apps/ui/src/App.tsx` | The root: `BrowserRouter` around the shell, plus the two stylesheet imports. Thirteen lines |
| `apps/ui/src/OperatorShell.tsx` | Navigation bar and router outlet, both derived from `ROUTES`; `end` derived at the call site |
| `apps/ui/src/routes.tsx` | `RouteEntry` and `ROUTES`: the seventeen rows the nav and the router both read |
| `apps/ui/src/pages/*Page.tsx` | One screen per file, named export, with its private sub-components — the M6 convention |
| `apps/ui/src/pages/formLines.ts` | Textarea-to-list parsing shared by the task form and the planning review |
| `apps/ui/src/RunExecution.tsx` | Live graph, controls, approvals, verifiers, P6 candidate tree, P7 experience lineage, and materialization |
| `apps/ui/src/EventStream.tsx` | Resumable normalized event trace: audit snapshot, SSE, and gap recovery |
| `apps/ui/src/StatePill.tsx` | The single state badge every screen reuses |
| `apps/ui/src/runState.ts` | `terminal`, `shortId`, and the `useNow` clock that ticks only while a run is live |
| `apps/ui/src/api.ts` | Typed HTTP client and resumable event URL construction |
| `apps/ui/src/api/schema.d.ts` | Generated OpenAPI TypeScript contract; do not hand-edit |
| `apps/ui/src/types.ts` | Small aliases over generated schemas |
| `apps/ui/src/graphLayout.ts` | Deterministic layout for read-only execution projections |
| `apps/ui/src/styles.css` | Responsive visual system and state styling |
| `apps/ui/src/*.test.tsx` | Component, event recovery, lineage, benchmark, and layout evidence |
| `apps/ui/budget/budget.ts` | The committed size caps, the lazy-only pattern, and the grouping expectations |
| `apps/ui/budget/evaluate.ts` | Pure budget rules over a bundle graph; no I/O, unit-tested on synthetic bundles |
| `apps/ui/budget/plugin.ts` | The Vite plugin: measures the real bundle, prints the table, fails the build |
| `apps/ui/budget/groups.ts` | The vendor chunk groups and the CSS skip that keeps the stylesheet cascade in one order |
| `apps/ui/budget/groups.test.ts` | Holds the chunk groups to the CSS skip: no group may capture a stylesheet id, and priority, not array order, decides placement |
| `apps/ui/budget/wiring.test.ts` | Loads the real config and asserts the gate, the groups and their CSS skip are the ones actually wired |
| `apps/ui/vite.config.ts` | Dev/preview proxying, vitest config, and the wiring of the groups and the gate |

Where a new declaration belongs, why the nav and the router read one array, and the two
traps the layout sets are in the
[operator UI source tree runbook](../runbooks/v03-operator-ui.md).

For end-to-end system behavior, continue with the [developer showcase](showcase.md).
For release status, see the [v0.2 release audit](../releases/v0.2/audit.md) and
[delivery plan](../releases/v0.2/plan.md).
