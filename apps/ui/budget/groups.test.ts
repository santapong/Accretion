import { expect, test } from "vitest";
import { CODE_SPLITTING_GROUPS } from "./groups";

/**
 * The chunk groups, held to the two promises their comments make.
 *
 * The second of those promises is the one with no other witness anywhere in the repo. The
 * `!NOT_CSS.test(id) &&` guard in `captures()` keeps stylesheets out of every named vendor
 * chunk, which is what keeps `dist/` at one stylesheet in one order after the JavaScript
 * is split five ways. Nothing else can see it go:
 *
 *   - `evaluate.ts`'s `scriptModuleIds` filters CSS ids out before `chunk-of` and
 *     `lazy-only` look at anything, so neither rule can watch a stylesheet move;
 *   - `initial-css-raw` and `initial-css-gzip` measure total bytes, and moving a
 *     stylesheet between chunks does not change the total;
 *   - PR2's Playwright/axe gate reads the accessibility tree, not the cascade.
 *
 * So deleting three characters from `captures()` would reorder a production stylesheet
 * with all eight CI checks green. These tests are the check.
 */

/**
 * Which group actually claims an id, by Rolldown's own rule: highest `priority` wins, ties
 * broken by array index.
 *
 * Written out rather than assumed, because "first match in array order" is only the same
 * answer while the array happens to be sorted by priority — and the whole reason the
 * priorities are explicit is that nobody should have to rely on that.
 */
function claimedBy(id: string): string | null {
  const matched = CODE_SPLITTING_GROUPS.map((group, index) => ({ group, index })).filter(
    ({ group }) => group.test(id),
  );
  if (matched.length === 0) return null;
  matched.sort((a, b) => b.group.priority - a.group.priority || a.index - b.index);
  return matched[0].group.name;
}

test("no chunk group captures a CSS module id", () => {
  // The load-bearing one. `RunExecution.tsx:3-15` imports this stylesheet, and its id
  // matches the `vendor-flow` package pattern character for character. Without the CSS
  // skip it would be pulled out of the app chunk into `vendor-flow`, and the order the
  // two stylesheets are linked in would change.
  const reactFlowStyles = "/repo/node_modules/@xyflow/react/dist/style.css";
  // Would fall to `vendor` without the skip: any stylesheet shipped by any dependency.
  const dependencyStyles = "/repo/node_modules/some-ui-kit/dist/theme.css";
  // Would be claimed by `cosmic` without the skip, once PR7's directory exists.
  const cosmicStyles = "/repo/apps/ui/src/cosmic/scene.css";
  // The app's own stylesheet. Matched by no group with or without the skip — asserted so
  // that the day somebody adds an app-source group, this says out loud that CSS is still
  // not its business.
  const appStyles = "/repo/apps/ui/src/styles.css";

  for (const id of [reactFlowStyles, dependencyStyles, cosmicStyles, appStyles]) {
    expect(CODE_SPLITTING_GROUPS.every((group) => group.test(id) === false)).toBe(true);
    expect(claimedBy(id)).toBeNull();
  }

  // A query suffix is how Vite addresses the same stylesheet through its own pipeline, and
  // `/\.css(\?|$)/` covers it. A skip written as `.endsWith(".css")` would not.
  expect(CODE_SPLITTING_GROUPS.every((group) => group.test(`${reactFlowStyles}?used`) === false)).toBe(
    true,
  );
});

test("the JavaScript beside those stylesheets is still claimed by its named group", () => {
  // The control for the test above: without it, "no group captures a CSS id" would also
  // pass if the groups had been gutted and captured nothing at all. Each of these is the
  // JavaScript sibling of a stylesheet asserted above, or the anchor package of a group.
  expect(claimedBy("/repo/node_modules/@xyflow/react/dist/esm/index.js")).toBe("vendor-flow");
  expect(claimedBy("/repo/node_modules/d3-zoom/src/zoom.js")).toBe("vendor-flow");
  expect(claimedBy("/repo/node_modules/react-dom/client.js")).toBe("vendor-react");
  expect(claimedBy("/repo/node_modules/react/jsx-runtime.js")).toBe("vendor-react");
  expect(claimedBy("/repo/node_modules/@tanstack/react-query/build/index.js")).toBe("vendor-data");
  expect(claimedBy("/repo/node_modules/react-router-dom/dist/index.js")).toBe("vendor-data");
  expect(claimedBy("/repo/node_modules/some-ui-kit/dist/index.js")).toBe("vendor");
  expect(claimedBy("/repo/node_modules/three/build/three.module.js")).toBe("cosmic");
  expect(claimedBy("/repo/apps/ui/src/cosmic/scene.ts")).toBe("cosmic");

  // App source is not vendored and belongs in the entry chunk.
  expect(claimedBy("/repo/apps/ui/src/App.tsx")).toBeNull();
});

test("priority, not array order, decides an overlapping id", () => {
  // `react-dom` under `@xyflow` would be a hoisting accident rather than a real layout,
  // but the point stands for any id two patterns can both claim: the answer must come from
  // `priority`. Every group has a distinct one, so the tie-break by index never runs.
  const priorities = CODE_SPLITTING_GROUPS.map((group) => group.priority);
  expect(new Set(priorities).size).toBe(priorities.length);
  expect(priorities).toEqual([...priorities].sort((a, b) => b - a));

  // `three` inside node_modules matches both `cosmic` (100) and `vendor` (10).
  expect(claimedBy("/repo/node_modules/three/build/three.module.js")).toBe("cosmic");
});

test("Windows module ids are grouped identically to POSIX ones", () => {
  // `[\\/]` in every pattern, because a `/`-only pattern silently matches nothing on
  // Windows: a build that is correct on Linux and unsplit everywhere else.
  expect(claimedBy("C:\\repo\\node_modules\\react-dom\\client.js")).toBe("vendor-react");
  expect(claimedBy("C:\\repo\\node_modules\\@xyflow\\react\\dist\\esm\\index.js")).toBe("vendor-flow");
  expect(
    CODE_SPLITTING_GROUPS.every(
      (group) => group.test("C:\\repo\\node_modules\\@xyflow\\react\\dist\\style.css") === false,
    ),
  ).toBe(true);
});
