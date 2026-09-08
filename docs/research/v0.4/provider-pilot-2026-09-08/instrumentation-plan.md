# Minimal fake instrumentation implementation

State: **DESIGNED, NOT IMPLEMENTED**. Scope: R1 preparation only.
The user-approved completion plan authorizes this bounded fake/local path;
the coordinator reviews its implementation boundary before code changes.

## Files and ownership

Add only `src/accretion/provider_pilot.py`,
`scripts/provider_pilot_dry_run.py`, and `tests/test_provider_pilot_dry_run.py`,
plus generated local evidence under this study's new directory after the run.
No existing runtime, RunManager, routing bootstrap, store, migration or frozen
research file needs an edit. If a missing public seam prevents the join, stop
and propose that change instead of editing shared runtime code concurrently.

The current schema and fixtures are draft export contracts only. Validate
schemas now; do not label illustrative fixture rows as run receipts or evidence.

## Mechanical isolation from the accounting repair

1. Construct Settings explicitly for BASELINE_ONLY and live providers disabled;
   refuse ambient AUTO/SHADOW or live-provider settings before creating a run.
2. Construct a runtime map containing only the in-process Provider.FAKE. Do not
   instantiate hosted-provider adapters, read provider credentials or use an
   external-runtime factory. Deny any non-FAKE runtime/configuration.
3. Build the existing deterministic routing stack without learned collaborators.
   Assert learned scorer, shadow recorder/rollout executor, exploration stage
   and promotion machinery are absent. No M6/M7 budget admission or settlement
   is reachable; test this with traps that raise if those methods are invoked.
4. Use only LOW_DIGITAL local fixture actions in a new disposable Git repository
   and fresh worktree per case. AGENT selected tools are empty. Deny attempts to
   add a selected tool, remote capability or native/provider tool setting before
   dispatch. The fixture hook writes only its fixed test artifact path.
5. Add independent absolute instrumentation limits for fixture count, one attempt
   per case, one concurrent execution, local deadline and output bytes. Provider
   calls and billed provider amount must remain zero; no proxy is treated as
   money. These limits are unrelated to the questioned AUTO accounting path.

This mechanical exclusion, once tested, lets fake preparation proceed while C1/C2
budget work is reviewed. It does not prove the excluded budget behavior safe.

## Data path and smallest fixture set

Reuse the pattern in
[the M2 real-stack test](../../../../tests/test_v04_m2_end_to_end.py), lines
113–184: MemoryStore, WorktreeManager, RunManager with only FakeRuntime and
`build_node_routing`. Do not import test setup helpers into production code.
Use the existing
[IndependentVerificationRecorder](../../../../src/accretion/feedback/verification.py)
and deterministic output verifier, with explicit independent-session mapping.

Run three separately identified local cases: valid output → PASS; artifact
violates a required deterministic claim → FAIL; required observable evidence
missing → INCONCLUSIVE. Each export names actual persisted configuration,
receipt, execution instance, verification spec/result and artifact hashes.
Capture actual wall/monotonic timing. Record zero provider calls with its basis;
keep unsupported local-compute accounting null and a limitation. The result
must distinguish task verification status from measurement-completeness status.

Export immutable JSON records and a manifest of content hashes to a new output
directory. Verify referential joins against the actual store; serializing five
unrelated IDs does not meet acceptance. Missing join/usage/verifier data becomes
INCONCLUSIVE or a rejected export. Preserve attempts rejected before dispatch
in a separate admission log with zero executions; never invent a receipt to
fill a row. A new output directory avoids overwriting prior evidence.

## Required tests and review gate

| Witness | Required observation |
|---|---|
| Three real-stack fake outcomes | Persisted independent PASS/FAIL/INCONCLUSIVE records, each bound to its own execution and frozen spec |
| Producer attempts self-verification | ERROR/blocked acceptance; same producer session never produces an accepted verification |
| Mode/provider/tool mutation | AUTO, SHADOW, EXPLORE, hosted provider and selected AGENT tool each refuse before submit; submit count remains zero |
| Accounting-path trap | M6 rollout, M7 exploration/admission/settlement and M8 promotion are never entered |
| Export identity tamper | Changed configuration/receipt/execution/result/artifact link is rejected, not silently replaced |
| Missing evidence or accounting | A passing artifact alone cannot produce a complete priced-measurement status |
| Bounded failure and duplicate output | Cancellation/deadline terminates; no automatic retry or overwrite of existing evidence |
| Zero-provider result | Runtime map is FAKE-only; no hosted adapter constructed; zero real provider request count; currency cost basis states NO_PROVIDER_CALLS |

Run only the new focused tests and applicable existing local verification/routing
tests. Do not invoke the locked corpus loader, old replay table regeneration,
live-provider tests, database migrations or a service deployment. A dry run
is complete only after actual records, manifest integrity and all joins are
checked, not when the script or fixtures merely exist.

After a verified run, append a dated local instrumentation report with commit,
commands, counts, artifact paths/hashes, denials and limitations; update this
study's readiness to FAKE_INSTRUMENTATION_VERIFIED. Keep live execution
NOT_AUTHORIZED and scientific verifier qualification unresolved.
