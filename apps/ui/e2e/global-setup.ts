import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Seed the backend and record the run id the spec needs.
 *
 * `/runs/:runId` is the only parameterised route, and there is no stable id to hardcode:
 * `accretion.ids.new_id` is a timestamp plus randomness, so every seeding produces a new
 * one. `examples/showcase.py` prints its ids as JSON on stdout, which is the most direct
 * source - it names the exact run it just created rather than guessing at the newest row.
 *
 * The seeder is ADDITIVE, not idempotent: it creates a fresh project, task and run on
 * every invocation with no lookup-or-create. Against CI's throwaway database that is fine.
 * Against a developer's database it accumulates rows, which is worth knowing before
 * wondering why the dashboard has fourteen "Accretion showcase" projects.
 *
 * It drives the FAKE runtime (`provider: "FAKE"`), so no signed-in Codex or Claude session
 * is involved and nothing here consumes a subscription.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../..");
export const SEED_FILE = resolve(HERE, ".seed.json");

export interface Seed {
  readonly run_id: string;
  readonly project_id: string;
  readonly task_id: string;
  readonly state: string;
}

export default function globalSetup(): void {
  const raw = execFileSync(
    "uv",
    ["run", "--no-sync", "python", "examples/showcase.py", "--repository", REPO_ROOT],
    // The script's own client-side ceiling is 30s; allow more so its error message
    // surfaces rather than being killed mid-poll and reported as a timeout here.
    { cwd: REPO_ROOT, encoding: "utf8", timeout: 180_000 },
  );

  const seed = JSON.parse(raw) as Seed;
  if (seed.state !== "SUCCEEDED") {
    throw new Error(
      `showcase run ${seed.run_id} ended ${seed.state}; the run page would be audited ` +
        "in a state the evidence never described",
    );
  }
  mkdirSync(dirname(SEED_FILE), { recursive: true });
  writeFileSync(SEED_FILE, JSON.stringify(seed, null, 2));
}
