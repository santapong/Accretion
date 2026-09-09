# Minimal fake instrumentation implementation

State: **FAKE_INSTRUMENTATION_VERIFIED**; see the
[clean-commit execution report](instrumentation-report-2026-09-09.md)
on 2026-09-09 (Asia/Bangkok). Scope: R1 preparation only.
The user-approved completion plan authorizes this bounded fake/local path;
the coordinator reviewed and approved this implementation boundary before code
changes. Live execution remains **NOT_AUTHORIZED**.

## Files and ownership

Added only [the instrument](../../../../src/accretion/provider_pilot.py),
[local CLI](../../../../scripts/provider_pilot_dry_run.py), and
[focused tests](../../../../tests/test_provider_pilot_dry_run.py),
plus generated local evidence under this study's new directory after the run.
No existing runtime, RunManager, routing bootstrap, store, migration or frozen
research file needs an edit. If a missing public seam prevents the join, stop
and propose that change instead of editing shared runtime code concurrently.

The current schema and fixtures are draft export contracts only. Validate
schemas now; do not label illustrative fixture rows as run receipts or evidence.

## Mechanical isolation from the accounting repair

1. Pass BASELINE_ONLY and live providers disabled directly to the existing
   `build_node_routing` and `RunManager` constructors; do not load ambient
   Settings or use a production app factory. Refuse ambient AUTO/SHADOW or
   live-provider settings before constructing a runtime.
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

The focused tests trap hosted-adapter, learned/budget and promotion constructors
and network connection attempts. This exclusion allowed fake preparation during
C1/C2 review. The [final accounting disposition](../../../releases/v0.4/closure-execution-2026-09-08.md)
is recorded separately; the fake run does not prove the excluded behavior safe.

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

The instrument manually orchestrates one AGENT node with actual existing
freeze, route, dispatch-claim, FakeRuntime, OutputContractVerifier and M3
independent-record APIs. It uses MemoryStore and disposable worktrees, not a
full production scheduler, database restart or feedback-pipeline execution.
BASELINE_ONLY is the routing mode; native decisions are EXPLOIT or FALLBACK,
not an invented BASELINE decision kind. The missing-evidence case deliberately
withholds the verifier's required-output observation to exercise INCONCLUSIVE;
it never changes the frozen native VerificationSpec.

Export immutable JSON records and a manifest of content hashes to a new output
directory. Verify referential joins against the actual store; serializing five
unrelated IDs does not meet acceptance. Missing join/usage/verifier data becomes
INCONCLUSIVE or a rejected export. Preserve attempts rejected before dispatch
in a separate admission log with zero executions; never invent a receipt to
fill a row. A new output directory avoids overwriting prior evidence.

Validation applies the JSON schema and native CanonicalContract seal checks,
recomputes the independent result from its saved verifier inputs, verifies the
exact evidence set and artifact digests, and joins native session, lease,
runtime terminal and dispatch events. The per-case timeout wraps setup through
export/verification; the total timeout bounds the three-case loop. Local Git
subprocesses are bounded and reaped on cancellation. Source provenance includes
the commit, tracked/untracked status, relevant code hashes, interpreter,
imported package paths and dependency versions. Unsupported monetary prices
and local-compute accounting remain unavailable, never inferred.

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

The dated execution report now records the commit, commands, counts, artifact
paths/hashes, denials and limitations. Study readiness is
FAKE_INSTRUMENTATION_VERIFIED. Live execution remains NOT_AUTHORIZED and
scientific verifier qualification remains unresolved. A later reproduction
must write a fresh output directory and retain the same provenance checks.
