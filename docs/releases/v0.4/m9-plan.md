# v0.4 M9 — The Experiment Studio

SDD v0.4 §16 and §17 give the operator four things the router owes them: why one node ran the
configuration it ran (§17.1), what a shadow policy would have done instead (§17.2), which router
version is live and what authorised it (§17.3), and a run page whose graph is a **projection** of
persisted state rather than a control surface (§16.6). M9 builds those four, and it closes with
the claim that ties them together — §16.2's correlation chain, walked end to end by ids.

The milestone claims three criteria: `AC4-M9-040` (node panel shows selected configuration,
uncertainty, alternatives and rejection reasons), `AC4-M9-043` (React Flow remains a projection
only) and `AC4-M9-044` (all routing/recovery/promotion events are correlated end to end).
`AC4-M6-041` and `AC4-M8-042` were proven by their own milestones and are *rendered* here.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M9a — `pr1-routing-panel` (#144) | `RoutingPanel.tsx`, `routingIndex.ts`, the §17.1 panel on the run page; `RouteUnderTest.structuralChange` and `styleDiff.obligationsFor` | the panel reads receipt + slate for the selected node and nothing else; the style-diff gate keeps measuring every route it can still compare |
| M9b — `pr2-shadow-comparison` (#148) | `ShadowComparison.tsx` beside the panel: this run's pairs out of a workspace-wide report, predicted against observed, and the gates a promotion still owes | a run that named no receipt asks the shadow routes for nothing; the aggregate is labelled as the stage's and never recomputed per run |
| M9c — `pr3-router-admin` (#149) | `/admin/router`: lineage, the promotion report that authorised the active version, and the ledger head as the answer to "what is live" | `AC4-M8-042` rendered; the eighteenth route |
| M9d — `pr4-badges-correlation-ts-twin` (this) | `src/projection/` as a package with no API reach; `routingBadges.ts`; `shadowStages.ts` lifted out of `ShadowComparison.tsx`; `contracts/canonical.ts`; `tests/test_v04_m9_correlation.py`; `-040`, `-043`, `-044` flipped | a badge derives from records the panels already hold and adds no request; the chain is walked by id from the task to the promotion report and back |

## What M9d decides

**The projection is a package, and the package is the claim (`AC4-M9-043`).**
`ProjectionCanvas`, `ProjectionNodeLabel`, `NodeBadges` and `LoopBackEdge` moved out of
`RunExecution.tsx` into `src/projection/` byte for byte, and the two stylesheet imports moved with
the canvas — `@xyflow/react/dist/style.css` and then `../react-flow.css`, adjacent, because the
second only beats the first by source order (`cssPort.test.ts` asserts the adjacency; its importer
scan is recursive from this PR, since a scan of `src/` alone would have found no importer at all
and failed loudly, which is the right failure). What the boundary buys is that "React Flow remains
a projection" becomes checkable as a property of a directory: no module under `src/projection/`
may name `../api`, `@tanstack/react-query`, `fetch(` or `EventSource(`, and a test reads the
package's own source and fails if one does.

**A badge is derived from what the panels already hold, and the canvas subscribes rather than
fetches (ADR4-M9-006).** `routingBadges.ts` is pure and imports no client. It takes the audit's
node → receipt index (`routingIndex.ts`), whichever receipts and slates the §17.1 panel has read,
whichever pairs the §17.2 comparison has, and the graph projection, and it returns one badge per
routed node: the decision word (`routed` / `shadowed` / `overridden` / `explore` / `fallback` /
`human_review`), the runtime and model of the selected configuration, its tool count, the node's
verification state, the receipt's revision, and the predicted cost and latency. `RunExecution.tsx`
reads the panels' query keys through react-query's `skipToken`, which observes a cache entry and
can never fetch it, and passes the badges down as props. A node whose receipt nobody opened shows
no routing badge; that is the design, not a gap, because no route lists a run's receipts
(ADR4-M9-002) and a canvas that asked per node would be the request storm that argument refuses.

**No new CSS rule (ADR4-M9-003).** The routing badge reuses `.node-badge`,
`.node-badge-capability` and `.node-badge-part`, the unlayered classes the capability badges
already use inside the canvas. `theme.css` cannot grow while union-equality against
`fixtures/styles.pre-pr5.css` is the completeness proof of the port, and neither can
`react-flow.css`: both are compared rule for rule against the pinned sheet. The brief asked for
badge rules in `react-flow.css`; adding one fails three cases of `cssPort.test.ts`, and the fix
would have been to weaken the gate that guarantees the stylesheet port changed nothing.

**The TypeScript canonical twin verifies; it does not write (ADR4-M9-005).**
`apps/ui/src/contracts/canonical.ts` implements ADR-056 in the browser and replays all nineteen
committed vectors from the same fixture the Python suite replays. Its two interesting rules are
the ones a JavaScript implementation gets wrong by default: keys sort by **code point** (RFC 8785
sorts by UTF-16 code unit and would put `😀` before `ｽ`), and a float that happens to be integral
still prints `1.0` (JavaScript has one numeric type, so the distinction is carried by an explicit
`float()` wrapper and an integral `number` beyond 2^53 is refused rather than rounded).

**The correlation chain is walked over one real run (`AC4-M9-044`).**
`tests/test_v04_m9_correlation.py` drives the M2 routed-graph fixture with M3's feedback pipeline
attached, then follows ids: task → run → graph revision → node contract → routing request →
receipt → `RUNTIME_CALL_STARTED.payload.routing_receipt_id` (and the projection `node_id` that
makes it findable from the canvas) → `ROUTING_DECISION_CREATED.causation_id` → verification result
→ experience record → training-snapshot manifest → promotion report → and back to the run, through
the `ROUTER_PROMOTION_EVALUATED` event the evaluator announces on the run's own log.

## The one link the shipped writers do not join

`SnapshotBuilder.build` dereferences `ExperienceRecord.contract_id` with `get_experience` and
skips a record whose experience is missing, treating it as retracted. That holds for the records
the M4 and M8 fixtures build, whose `contract_id` **is** an experience id (ADR-054 b: the
projection "is keyed by the same `experience_id` — carried as the header's `contract_id`"). It
cannot hold for a real run: one run materialises ONE P7 experience and projects one record per
routed node, so `feedback/experience.py` mints
`derived_id("experience", experience_id, execution_instance_id, "1")` and passes the experience id
to the store separately, where it becomes the row's parent column.

Measured, not inferred: over a routed run whose `act` node is `eligible_for_learning`,
`get_experience(record.contract_id)` is `None`, `get_experience(record.labels["experience_id"])`
is the experience, and `SnapshotBuilder.build` over the window refuses the whole snapshot with
"no experience record ... is eligible for learning". **No experience produced by a live routed run
can enter a training snapshot today**, and the message reads as "this run produced no evidence"
rather than as "its evidence could not be dereferenced".

`test_the_snapshot_builder_cannot_yet_include_a_run_projected_record` pins that, unmarked, with
its cause. The chain test seals its training snapshot from the run's records directly — the same
contract the builder would have written, through the contract's own validators — so the remaining
links are still proven end to end. The repair belongs to the milestone that owns
`training_snapshot.py` and `feedback/experience.py`: either the builder resolves the record's
`experience_id` label (or the store's parent column), or the projection carries the experience id
in a declared field. When it lands, that test goes red on purpose and the chain test should seal
its snapshot with `SnapshotBuilder` instead.

## Acceptance

| Criterion | Claiming test | Mutation that kills it |
|---|---|---|
| `AC4-M9-040` | `RoutingPanel.test.tsx:120 node panel shows selected configuration, uncertainty, alternatives and rejection reasons` (M9a) | render the slate without filtering `pareto_dominated` → the "alternatives" list stops being alternatives |
| `AC4-M9-043` | `projection.test.ts:97 no module in the projection package can reach the API, react-query, fetch or an event stream` | add `import { api } from "../api"` to any module in the package → red |
| `AC4-M9-043` | `projection.test.ts:244 driving the canvas issues no request and exposes no control inside a badge` | make a badge a `<button>`, or fetch on mount → red on the role query or on the fetch counter |
| `AC4-M9-044` | `test_the_chain_from_the_task_to_the_promotion_report_is_walkable_by_ids` | drop `routing_receipt_id` from `RUNTIME_CALL_STARTED`'s payload → the walk cannot get from an executed call to its decision (measured: `KeyError` at the first hop) |

Supporting witnesses, none of them marked:

| Test | What it would catch |
|---|---|
| `projection.test.ts` — the run page badges a node from the receipt the routing panel read, and asks for it once | a canvas that fetched its own receipts: the badge would look identical and the request count would double |
| `routingBadges.test.ts` — a shadow pair naming the receipt replaces routed, and never replaces the other four | `shadowed` overwriting `explore` or `fallback`, which would hide what the router did |
| `routingBadges.test.ts` — the node's newest decision is the one badged, and an unread receipt badges nothing | a re-routed node showing the decision it did *not* run under |
| `canonical.test.ts` — keys sort by code point, which is not what Array.sort does | a JCS-shaped `sort()`; reddens `astral_key_sort_order` |
| `canonical.test.ts` — an integer and a float of the same value are different documents | `1.0` printed as `1`; reddens `float_one` |
| `test_the_snapshot_builder_cannot_yet_include_a_run_projected_record` | the seam above being silently closed or silently widened |

## Counts

```
in scope: 158   proven: 149   unmet MUST: 1
```

`--stage v0.4-M9` reports `in scope: 3   proven: 1` with the other two classified `FRONTEND`, and
`PASS`. The one unmet MUST is `V02-P5-001`, whose claiming test
(`tests/test_p5_dynamic_service.py::test_p5_api_surface_is_additive_and_project_gated`) passes on
its own and fails inside the full suite with `asyncpg InvalidPasswordError` when no lane database
is up. It fails identically with this PR's test file removed; it is an environment artefact of a
run without Postgres, not a finding.

The vitest suite reads `27 files, 286 tests, all passing` (243 before this PR). The Python suite
reads `7 failed, 3214 passed, 100 skipped`: the two `tests/test_acceptance_harness.py` node-id
assertions, which compare a pytest node id against a relative path and therefore fail in any git
worktree, plus the five Postgres-authentication failures above. All seven fail identically on the
base.

The bundle budget passes unchanged — the projection package is a move, not an addition:

```
initial JS  595,379 B raw / 175,178 B gzip   initial CSS  53,181 B raw / 10,638 B gzip
PASS  initial-js-raw     595,379 B <= 622,146 B cap (raw)
PASS  initial-js-gzip    175,178 B <= 183,105 B cap (gzip)
PASS  initial-css-raw     53,181 B <= 54,066 B cap (raw)
PASS  initial-css-gzip    10,638 B <= 10,870 B cap (gzip)
```

No cap moved, and `apps/ui/budget/budget.ts` is untouched.

## Reproduce

```bash
npm run api:generate && git diff --exit-code -- apps/ui/src/api/schema.d.ts
npm run check && npm run test && npm run build
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m9_correlation.py tests/test_v03_m6_api_client_contract.py \
  tests/test_frontend_anchor_sync.py tests/test_acceptance_harness.py
uv run --no-sync ruff check . && uv run --no-sync mypy src
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py --stage v0.4-M9
```

The browser gates (`e2e/a11y.spec.ts`, `e2e/style-diff.spec.ts`) need two builds and a seeded
backend; `/runs/:runId` carries a `structuralChange` waiver re-declared for this PR, because every
routed node now renders a badge inside the React Flow node and again in the summary list, so the
run page's element count differs from the merge-base by construction. Every other route is still
compared byte for byte, and the element floor, the focus pass and the whole a11y sweep still run
on the waived one.

## Deviations recorded

- **No rule was added to `react-flow.css`.** The brief asked for badge rules there; ADR4-M9-003
  already measured what that costs (three failing cases in `cssPort.test.ts`), so the badge reuses
  the pinned sheet's own `.node-badge*` vocabulary and the stylesheet is unchanged apart from the
  header sentence naming its importer.
- **`cssPort.test.ts`'s xyflow-importer scan became recursive** and its adjacency expectation now
  computes the relative specifier from the importer's directory. Without it the move to
  `src/projection/` would have failed "exactly one component imports xyflow's stylesheet" — the
  file pinned the import SITE, which the brief flagged as a thing to check before moving it.
- **`shadowStages()` lives in its own module, which adds two files the brief did not name**:
  `apps/ui/src/shadowStages.ts` is created and `apps/ui/src/ShadowComparison.tsx` is modified (its
  inline filter becomes one call; nothing it renders changes and its own tests are untouched and
  green). The rule moved because the run page needs the same answer the panel needs — which shadow
  stage could have scored this run — to pick the cached report the canvas's `shadowed` badge reads,
  and two spellings of that question would let the canvas and the panel below it disagree. It is a
  module rather than an export from the component because
  `react-refresh/only-export-components` warns on a component file that also exports a function
  ("Use a new file to share constants or functions between components", measured against a probe
  file) and `eslint .` over `apps/ui` is otherwise warning-free.
- **The canonical twin's tests use `crypto.subtle`, not `node:crypto`.** `tsconfig.app.json`
  typechecks everything under `src/` against the browser lib with no `@types/node` — the boundary
  `tsconfig.node.json` documents — so a `node:crypto` import fails `npm run build` even though it
  runs under Vitest. The digests are still compared against ones Python's `hashlib` produced, so
  the two implementations behind the comparison are still independent.
- **The training snapshot in the chain test is sealed directly rather than built.** See "the one
  link the shipped writers do not join" above; the alternative was a chain test that stops three
  hops early or one that seeds fixture-shaped records and proves nothing about a real run.
