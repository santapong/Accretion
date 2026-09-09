# v0.5 simulation protocol — prospective M0 draft

Status: **DRAFT / NOT REGISTERED / NO EVALUATION EXECUTED**. Prepared 9 September
2026 under the approved [implementation plan](../../releases/v0.5/completion-plan-2026-09-09.md)
and [M0 decisions](../../releases/v0.5/m0-decisions-2026-09-09.md). This draft
records required decisions; it does not supply a registered dataset, approved
scientific thresholds, a simulator result or a study execution allowance.

## Question and scope

Can the same high-level simulated manipulation experiment execute reproducibly
through conforming UR5e/2F-85 and Panda adapters under Accretion's independent
verification, permission, safety and evidence contracts, without verified-success
or safety regression relative to direct simulator-specific scripts?

The initial implementation profile is MuJoCo, with separate adapter translations
and one pinned physics profile. Exact scenes/models/controllers, renderer and
worker image hashes must be fixed before a qualifying execution. This supports
a bounded simulation foundation; it does not establish cross-simulator portability,
learned cross-embodiment transfer or physical performance. External-provider
allowance is zero unless a later separately reviewed study changes that scope.

## Required study matrix

Use three task families: reach a target pose; pick/place one rigid object with
physical grasp/lift/transport/release and stable placement; and recover from a
declared perception or planning failure. Register failure injection and permitted
bounded recovery before observation. Pair on task, embodiment, world/controller,
seed, sampled randomization and verifier versions; reset between trials and
counterbalance treatment order.

| Treatment | Definition |
| --- | --- |
| B0 | Direct simulator-specific script for the corresponding embodiment |
| B1 | Accretion with a static workflow/configuration |
| B2 | Accretion routing with retrieved experience disabled |
| B3 | The same routing/configuration universe with eligible verified experience |

B2/B3 need a non-inert compatible local configuration set. Four labels around
one FAKE execution are not an ablation. Freeze a development-only experience
snapshot; held-out outcomes never update that snapshot or the producer/router.
Different-embodiment experience remains an attributed weak prior and cannot
change live action selection.

## Registration blockers

Before outcome access, freeze an attributable study ID and adopted protocol
digest; disjoint development/qualification/locked task identities and seeds;
the randomization distributions and sampled values; independent verifier
implementations/identities and qualification cases; task/safety/false-acceptance
definitions; replay class and development-derived tolerances; paired analyses,
nonregression margins, precision/power calculation and multiplicity rules;
intention-to-treat denominators and handling of FAIL/INCONCLUSIVE/missing data;
all exact episode approvals, retry/exclusion rules, resource/storage caps and
stop conditions; and the immutable candidate, adapter/environment/evaluator
closure. Report approval overhead in end-to-end timing.

The [acceptance audit](../../releases/v0.5/planning/acceptance-audit-2026-09-09.md#splits-budgets-and-treatment-qualification)
offers a capacity proposal of 912 primary attempts and up to 912 replays, not
a sample-size justification or an approved campaign. The separate twenty-minute
M0 feasibility envelope in V05-ADR-001 is development work and cannot be spent
as a held-out study. If adequate precision cannot fit the eventually approved
budget, record INCONCLUSIVE or revise prospectively; do not lower thresholds
after inspecting outcomes. The v0.4 locked corpora, protocol and access log are
outside this study and remain unchanged.

## Decision and preservation

Independent deterministic task, safety and evidence-completeness verification
must precede acceptance. Preserve original execution artifacts, failures,
quarantine, contradictions and every consumed attempt. A command crash without
an observed exit is not a passing run. Uncertain action acknowledgement aborts
the episode and requires a reset/new identity before any allowed recovery.

Report GO only if every registered joint gate and required adapter/task stratum
passes. NO-GO and INCONCLUSIVE preserve engineering work but do not satisfy the
unamended v0.5 release claim. No v0.4 threshold automatically becomes a v0.5
standard. Freeze the executable candidate before locked execution. A repair
that changes its closure stops qualification of that candidate; preserve the
original outcomes and follow the prospective amendment/new-evaluation rule.

## Implemented M0 evidence-integrity gate

The active [SDD §22](../../sdd/Accretion_SDD_v0.5.md#22-release-acceptance-criteria)
registers AC5-001–AC5-030 with separate owner and required witness classes:
automated behavior (A), actual simulator execution (S), frontend/browser (U),
and scientific study (B). All are explicit intermediate deferrals at M0.
Ordinary acceptance continues to gate inherited in-scope criteria; a stage
containing only deferrals returns NOT READY. The explicit v0.5 command below
requires ordinary acceptance plus every composite record and refuses deferrals
or waivers:

```bash
uv run --no-sync python scripts/check_acceptance.py \
  --release v0.5 --evidence-manifest /absolute/path/to/candidate-evidence/manifest.json
```

The source checkout must be clean. Keep the bundle outside that checkout to
avoid a self-referential candidate hash; archive it with the later release
audit. An identical-tree protected release bridge may additionally pass
`--evidence-candidate <full-audited-commit>`: the command verifies that this
locally available commit has exactly the current checkout's complete tree.
It cannot reuse evidence for a changed tree. Candidate cleanliness/identity is
checked again after claiming tests run.

This command supplements the inherited `scripts/release_gate.py` conditions,
full pytest/frontend/browser/clean-checkout CI and independent release review.
It does not replace them or alone authorize promotion/tagging. M9 must wire the
combined release process to both checks and the actual witness producers.

The JSON bundle format is version 1. Top-level fields are `schema_version`,
`release: "v0.5"`, the full `candidate_commit` and `candidate_tree`, and `criteria`.
The latter maps all thirty IDs to exactly their required classes. Each class
points to an existing nonempty JSON report using `{path, sha256}` relative to
the bundle root. Missing/extra IDs or classes, path traversal/symlinks, duplicate
JSON keys, absent files and digest mismatches fail closed.

Every report carries `schema_version: 1`, `criterion_id`, `witness_class`, the
same candidate identities, `outcome: "PASS"`, timezone-aware `executed_at`, the
actual `command` argument list, observed integer `exit_code: 0`, nonempty
unique `{id, outcome: "PASS"}` cases, and nonempty original `artifacts` pointers.
Execution timestamps must not be future-dated or older than the existing
180-day evidence freshness horizon. That metadata horizon is not an empirical
safety threshold or automatic evidence renewal.

S/B records additionally retain `evidence_type: "SIMULATION"`, distinct
`producer_identity`/`verifier_identity`, and nonempty `simulator_digests` and
`adapter_digests`. Conformance criteria AC5-006/007 and every B record require
at least two distinct adapter digests. A B record also includes its hashed
`protocol`, `registered_at < first_outcome_at <= executed_at`, `claim_decision:
"GO"`, positive equal `planned_trials`/`completed_trials`, all four `treatments`
and all three `task_families` (`reach`, `pick_place`, `declared_recovery`).

These are integrity and coverage checks over reported evidence, not a new
scientific evaluator or cryptographic authentication service. Actual witness
producers, authenticated verifier results, exact trial-level matrix membership,
case-to-requirement coverage and registered statistical calculations still need
their M1–M9 implementations and independent qualification. A report's PASS label
cannot establish those facts on its own. The synthetic fixtures in
`tests/test_v05_m0_acceptance.py` validate only refusal/registration mechanics and
carry no AC5 acceptance markers. No synthetic fixture may be promoted as a real
simulator, conformance, protocol or study artifact.
