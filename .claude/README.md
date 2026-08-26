# Claude Code configuration

Agent definitions and workflow scripts used to build Accretion's milestones. Read by the
Claude Code CLI only — nothing in `src/`, `tests/`, `migrations/`, or CI reads this
directory. Agents are discovered at session start, so changes take effect next session.

## Three teams

**Planning** — decides what a milestone is and what would go wrong.

| Agent | Model | Owns |
|---|---|---|
| `sdd-milestone-planner` | opus | Turns a milestone id into an executable plan: decisions with rationale, files, evidence design, PR sequence. |
| `milestone-risk-analyst` | opus | The adversarial counterpart: ranked risks, blast radius, per-criterion tautology traps and the mutation that should break each test. |

**Development** — writes the code and, separately, the proof.

| Agent | Model | Owns |
|---|---|---|
| `milestone-implementer` | opus | Backend: contracts, the three-layer store, migrations, managers, API routes. |
| `frontend-implementer` | opus | `apps/ui/` — React pages, React Flow projections, react-query, vitest/jsdom, the F1–F4 accessibility findings. |
| `evidence-author` | opus | Acceptance tests, in-process fakes, `criteria.toml` entries. **Deliberately not the agent that wrote the feature.** |

**Verification** — decides whether it is actually done.

| Agent | Model | Owns |
|---|---|---|
| `sdd-spec-verifier` | opus | Conformance to the SDD, quoting normative text. Hard-fails if `docs/sdd/` is modified. |
| `acceptance-auditor` | opus | Whether a claiming test proves its criterion or echoes a fixture. Runs the mutation. |
| `contract-guardian` | opus | Compatibility of contract, row, and migration changes, including the locked v0.4 registry. |
| `ci-gate-runner` | sonnet | Runs the gate chain and reports faithfully. Cheap model on purpose: it executes and transcribes rather than judges. |

## The pipeline

```
milestone-plan    planner ∥ risk analyst  ──▶  reconcile by hand  ──── human gate ────
milestone-build   per PR: implement → author evidence
                          → spec ∥ evidence ∥ contracts, then gates
                          → repair, loop until clean
                  leaves everything in the working tree  ──────────── human gate ────
                  review the diff, open the PRs, merge
```

```bash
# plan
Workflow({ name: "milestone-plan",  args: { milestone: "M6" } })

# build, once the plan is reconciled and approved
Workflow({ name: "milestone-build", args: {
  milestone: "M6",
  planPath: "/path/to/plan.md",
  prs: [{ name: "plugins-page", kind: "frontend", scope: "…", criteria: ["AC3-UI-01"] }]
}})
```

Gates sit **between milestones**, not between PRs, so a bad plan costs one milestone
rather than the whole release.

## Two traps every agent here is told about

- **`check_acceptance.py --stage <M>` prints `PASS` over `in scope: 0   proven: 0`** while
  its criteria are still `not_yet_due`. Read the counts line, never the banner.
- **pytest needs both** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` *and* `-p pytest_asyncio.plugin`.
  Either alone breaks the suite. Postgres runs on 5433 and must be migrated first.

## Why evidence is written by a different agent

`docs/releases/v0.3/acceptance-baseline.md` records this project's own history of tests
that passed without proving anything: digest pins that hashed the file just read, a
privilege-expansion test that short-circuited before the privilege branch, benchmarks
replaying literal outcome fields while the acceptance tables read as measurement. The
split between `milestone-implementer` and `evidence-author`, and the mutation check both
the author and the auditor must run, exist to make that harder to repeat.
