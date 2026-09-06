import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";
import { loadConfigFromFile } from "vite";
import { CODE_SPLITTING_GROUPS } from "./groups";

/**
 * The gate is only a gate while it is installed. This is the test for that.
 *
 * Every other test under `budget/` proves the gate is *correct*: `evaluate.test.ts` kills
 * each rule with its own mutation, `plugin.test.ts` pins the three measurements, and
 * `groups.test.ts` holds the chunk groups to the CSS skip. None of them can see the gate
 * being unplugged. Deleting `bundleBudget()` from the `plugins` array in `vite.config.ts`
 * is one line, and it leaves NO symptom anywhere:
 *
 *   - every `budget/**` test still passes, because they all import the modules directly;
 *   - `npm run check` still passes, because the imports simply become unused-free code
 *     that lints and typechecks;
 *   - `npm run build` still exits 0 and prints no budget table at all — and, because the
 *     chunk groups keep every chunk under 500 kB, not even Vite's own advisory reappears
 *     to hint that something used to be watching.
 *
 * Eight green CI checks, and `.claude/agents/frontend-implementer.md`, `CHANGELOG.md` and
 * `docs/guides/frontend.md` all still claiming in the present tense that `npm run build`
 * enforces a bundle budget. That claim is what this file makes true.
 *
 * The groups are asserted here for the same reason and are the other half of the same
 * deletion: emptying `codeSplitting.groups` to `[]` is caught by the gate today (the app
 * would be one 561 kB chunk again, over the per-chunk cap) but only for exactly as long as
 * the plugin above it is still installed. Assert both, and neither removal is silent.
 *
 * ## Why the config is loaded through Vite rather than imported
 *
 * `import config from "../vite.config"` fails with `TypeError: The URL must be of scheme
 * file`: the config reads the app version at module scope with
 * `readFileSync(new URL("./package.json", import.meta.url))`, and under Vitest's module
 * runner `import.meta.url` is not a `file:` URL. `loadConfigFromFile` is Vite's own
 * loader, so the config is evaluated exactly the way `vite build` evaluates it.
 */

/** `apps/ui/`, the directory `vite.config.ts` sits in and resolves its own imports against. */
const uiRoot = dirname(dirname(fileURLToPath(import.meta.url)));

test("the budget gate and the chunk groups are wired into the real build config", async () => {
  const loaded = await loadConfigFromFile(
    // The command and mode `vite build` itself uses, since a config is free to branch on
    // them: a gate installed only in `serve` would be no gate at all.
    { command: "build", mode: "production" },
    join(uiRoot, "vite.config.ts"),
    uiRoot,
  );
  expect(loaded).not.toBeNull();
  const config = loaded!.config as {
    plugins?: unknown;
    build?: { rolldownOptions?: { output?: { codeSplitting?: { groups?: unknown } } } };
  };

  // `plugins` is nested: `react()` and `tailwindcss()` each return an array of plugins, so
  // the entry the gate contributes is one leaf among many. Flatten and read names.
  const pluginNames = (config.plugins as { name?: string }[])
    .flat(Infinity as 1)
    .map((plugin) => plugin?.name);
  expect(pluginNames).toContain("accretion:bundle-budget");

  // Name/priority pairs rather than the group objects themselves. `loadConfigFromFile`
  // evaluates the config in a separate module graph, so its `CODE_SPLITTING_GROUPS` holds
  // different function instances for every `test` closure and a deep equal on the raw
  // objects would fail against a perfectly correct config. The pairs are what decides
  // placement, and they are what `groups.test.ts` then judges in detail.
  const wiredGroups = config.build?.rolldownOptions?.output?.codeSplitting?.groups as
    | { name: string; priority: number; test: (id: string) => boolean }[]
    | undefined;
  expect(wiredGroups?.map((group) => [group.name, group.priority])).toEqual(
    CODE_SPLITTING_GROUPS.map((group) => [group.name, group.priority]),
  );

  // The pairs prove WHICH groups are wired; only the functions prove they are the guarded
  // ones. A config that inlined five groups with the same names and priorities but bare
  // RegExps would pass the pairs check, capture React Flow's stylesheet into `vendor-flow`,
  // and link it ahead of the app stylesheet - the cascade reorder `groups.ts` exists to
  // prevent, and one that `groups.test.ts` cannot see because it judges the exported
  // array, not the array the build uses. So ask the wired functions directly, with the
  // JavaScript beside the stylesheet as the control that stops the CSS assertion passing
  // against gutted groups.
  const wired = wiredGroups ?? [];
  const stylesheet = "/repo/node_modules/@xyflow/react/dist/style.css";
  const script = "/repo/node_modules/@xyflow/react/dist/esm/index.js";
  expect(wired.filter((group) => group.test(stylesheet)).map((group) => group.name)).toEqual([]);
  expect(wired.filter((group) => group.test(script)).map((group) => group.name)).toContain(
    "vendor-flow",
  );
});
