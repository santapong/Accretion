import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readFileSync } from "node:fs";

// The header version is read from package.json at build time rather than typed into the
// UI. It was typed in once, said "v0.2" through the whole v0.3 release, and nothing
// failed - a hardcoded version is a claim no build can check. Reading it here means the
// only way to get it wrong is to get the package version wrong.
const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

// The dev server and the preview server need identical proxying: the app calls /api on
// its own origin (`api.ts` defaults API_ROOT to ""), so whichever server is in front has
// to forward it to the backend.
const PROXY = { "/api": "http://localhost:8000", "/healthz": "http://localhost:8000" };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(version) },
  server: {
    port: 5173,
    proxy: PROXY,
  },
  // `vite preview` does NOT read `server.proxy` - it reads this. The accessibility gate
  // drives the production build rather than the dev server, so without this block every
  // /api call from the previewed app 404s and the failure reads like a dead backend.
  preview: {
    port: 4173,
    proxy: PROXY,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    // Scoped deliberately, and the split is by FILENAME so it cannot drift:
    // `*.test.ts` belongs to vitest, `*.spec.ts` belongs to Playwright. Vitest's default
    // include is `**/*.{test,spec}.?(c|m)[jt]s?(x)`, which would otherwise collect the
    // Playwright specs under e2e/ and fail on their `@playwright/test` import. The pure
    // logic in e2e/ (the waiver rules) is still unit-tested here rather than through a
    // browser, which is both faster and a better fit.
    include: ["src/**/*.test.{ts,tsx}", "e2e/**/*.test.ts"],
  },
});
