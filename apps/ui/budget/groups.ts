import { LAZY_ONLY_MODULES } from "./budget.js";

/**
 * The Rolldown code-splitting groups, and the CSS skip that protects the cascade.
 *
 * ## Why this is not inline in `vite.config.ts`
 *
 * It used to be, and nothing could see it. The `!NOT_CSS.test(id)` guard below is the only
 * thing keeping the stylesheet cascade in its original order once the JavaScript is split
 * into five chunks, and no rule in `evaluate.ts` is able to notice if it disappears:
 * `scriptModuleIds` filters CSS ids out before the `chunk-of` and `lazy-only` rules run,
 * the two `initial-css-*` rules measure bytes rather than order, and PR2's axe gate reads
 * the accessibility tree, not pixels. Deleting three characters would therefore reorder a
 * production stylesheet with all eight CI checks green.
 *
 * Exporting the group list is what makes that assertable. `groups.test.ts` holds the
 * groups to the promise this comment makes; a module-private constant in a config file
 * could only ever be verified by reading it.
 *
 * ## Why every `test` is a function
 *
 * React Flow's stylesheet is imported by `RunExecution.tsx` and its module id is
 * `.../node_modules/@xyflow/react/dist/style.css`, which matches the `vendor-flow` package
 * pattern exactly. A group written as a bare RegExp would therefore capture the stylesheet
 * along with the JavaScript, move it out of the app chunk, and CHANGE THE ORDER the
 * stylesheets are linked in. The JavaScript is split; the CSS stays exactly where it was,
 * one file in one order.
 */

/** A module id that is a stylesheet rather than JavaScript. Mirrors `STYLESHEET_ID` in `evaluate.ts`. */
export const NOT_CSS = /\.css(\?|$)/;

/**
 * Match a package pattern, but never a stylesheet.
 *
 * The CSS test comes first and is the load-bearing half: see the file header. Removing it
 * is a silent production change, which is why `groups.test.ts` asserts it directly.
 */
export const captures = (pattern: RegExp) => (id: string) => !NOT_CSS.test(id) && pattern.test(id);

/**
 * `[\\/]` throughout, per Rolldown's own guidance: module ids use backslashes on Windows,
 * and a pattern written with `/` alone would match nothing there — producing a build that
 * is correct on Linux and silently unsplit elsewhere.
 *
 * Every group carries an explicit `priority`. Rolldown breaks ties by array index, which
 * means without them the placement of a dependency would depend on the order somebody
 * happened to type the groups in. The array is written in descending priority so the two
 * orders agree and a reader does not have to hold both in their head.
 */
export const CODE_SPLITTING_GROUPS = [
  {
    // Nothing matches this today. It exists so that when PR7 adds the three.js scene, the
    // scene lands in its own chunk by construction rather than by a decision taken after
    // measuring how big it turned out to be. The budget gate enforces the other half of
    // the deal: modules matching this pattern must be reachable ONLY dynamically.
    name: "cosmic",
    test: captures(LAZY_ONLY_MODULES),
    priority: 100,
  },
  {
    // React and its renderer change a few times a year; the app changes daily. Splitting
    // them apart is what makes a returning visitor's cache worth anything.
    name: "vendor-react",
    test: captures(/node_modules[\\/](react|react-dom|scheduler)[\\/]/),
    priority: 40,
  },
  {
    // React Flow and the d3 packages it pulls in. `classcat` is React Flow's own class
    // helper and travels with it.
    name: "vendor-flow",
    test: captures(/node_modules[\\/](@xyflow[\\/]|d3-|classcat[\\/])/),
    priority: 30,
  },
  {
    // TanStack Query and the router: the data and navigation layer. `react-router` with no
    // trailing separator deliberately covers `react-router-dom` as well, which is a
    // re-export shell over it.
    name: "vendor-data",
    test: captures(/node_modules[\\/](@tanstack[\\/]|react-router)/),
    priority: 20,
  },
  {
    // Everything else from node_modules, lowest priority so it can only ever collect what
    // the named groups did not claim.
    //
    // zustand is deliberately named in NO group. `@xyflow/react` pins `zustand ^4.4.0`,
    // and PR6's planned zustand 5 would install a SECOND physical copy; a `zustand` regex
    // in `vendor-flow` would quietly drag both into a chunk whose name then lies about
    // what is in it. It falls in here until PR6 measures the two copies and decides.
    name: "vendor",
    test: captures(/node_modules[\\/]/),
    priority: 10,
  },
];
