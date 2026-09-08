# v0.4 research decision and v0.5 entry handoff

Decision: **ADOPTED — NO-GO for the full pre-registered routing-benefit claim.**
Date: 2026-09-08 (Asia/Bangkok). Maintainer: Santapong.

Santapong approved execution of the complete v0.4 completion plan in the Codex
conversation on this date. That plan explicitly proposed this scoped no-go in
R0. This document records adoption through that plan approval; it does not
invent a separate signature, vote or positive experimental result. The reviewed
plan is `docs/releases/v0.4/completion-plan-2026-09-08.md` at planning commit
`55536dce89b2a64d16dcae20a1e5a0f11db05638`, SHA-256
`e5e7987611a8b61aefd24374d164532d4f201e9da8d88e805b6dbc597dcedb56`.
The inspected source baseline for this decision is
`8ded8bab151267ec8e06182f52543dabed6eaca7`.

## Decision and retained evidence

The released v0.4.0/v0.4.1 engineering foundation is retained. The full
pre-registered routing-benefit claim is not established by its research
evidence. The documented no-go closes that claim assessment without erasing
the narrower synthetic result or requiring a favorable new experiment.

- Retain the synthetic paired-regret result: locked contrast
  `0.406954 [0.009603, 0.939517]`; drift contrast
  `0.168669 [0.039887, 0.320070]`, under the registered adjusted analysis.
- Retain `PARTIAL (paired only)`: binary `g_learn` intervals span zero and no
  recovered fraction is reported. This is uncertainty, not proof of no benefit.
- Retain the original safety reading and amendment 1 separately. Under the
  amended rate reading, M9 fails the locked false-acceptance ceiling
  (`0.067901 > 0.05`) and passes both drift point-rate gates marginally
  (`0.706790` verified; `0.049383` false acceptance). This does not establish
  joint safety success or the full routing claim.
- The priced real-provider routing pilot was not run. No hosted-model,
  production-generalization, simulation or physical result follows.

Sources: [frozen results](../../research/v0.4/results.md), lines 49–77,
173–216, 248–289, 315–328 and 379–393; [amendment 1](../../research/v0.4/amendment-1.md),
lines 43–77; [v0.4 protocol §20](../../sdd/future/v0.4-v1.0/03_RESEARCH/Accretion_v0.4_Research_Protocol.md),
lines 563–578. The protocol expressly distinguishes improved regret with a
failed safety gate from a successful full claim.

Default-off routing, `BASELINE_ONLY`, compatibility checks, independent
verification and existing promotion gates remain the operational boundary.
New research must use a distinct scoped protocol and fresh eligible evidence.
The [provider pilot preparation](../../research/v0.4/provider-pilot-2026-09-08/README.md)
is separate from release closure and is not authorized for provider execution.

## v0.5 §2.4 entry evidence

The [v0.5 entry conditions](../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md#24-entry-conditions)
require five individual decisions. This handoff verifies historical records,
inspects executable witnesses and records the coordinator's bounded local test
run below. Line references below are at the inspected baseline.

| Condition | Evidence and current disposition | Remaining candidate check |
|---|---|---|
| 1. v0.1–v0.3 release gates passed | **Historical condition satisfied.** [v0.1 audit](../v0.1/audit.md), lines 5–9 and 17–48; [v0.2 audit](../v0.2/audit.md), lines 54–74, includes its explicit browser exception; [v0.3 baseline](../v0.3/baseline.md), lines 29–32 and 44–58, records protected CI and acceptance. Local annotated tag objects and peeled commits match all three baseline records | Preserve the known exception as historical, retain current manual-evidence expiry, and run applicable release gates for any new code candidate |
| 2. v0.4 claim or documented no-go | **Satisfied by the adopted scoped no-go above.** The paired synthetic result and binary uncertainty remain unchanged | No additional scientific read is needed. Any new benefit claim needs its own evidence |
| 3. v0.4 contract migrations stable | **Established for v0.4.1, candidate refresh pending.** [v0.4.1 audit addendum](audit.md), lines 223–239, records clean-database upgrade → downgrade → upgrade; it states hardening changed no contract or migration. Migration tests contain data-preservation and refusal witnesses | On the final integrated candidate, perform the same clean-database cycle and relevant migration witnesses serially in an isolated database. The concurrent accounting work may change what must be checked |
| 4. Gateway denies undeclared capabilities | **Fresh bounded witnesses PASS before runtime changes.** [gateway denial test](../../../tests/test_p4_governance.py), lines 114–135, proves denied/unknown requests make zero executor calls; [routing capability tests](../../../tests/test_v04_m1_gates.py), lines 536–601 and 688–749, distinguish unauthorized, denied and unlisted capabilities. [Execution attribution test](../../../tests/test_v04_seam_executing_provider.py), lines 1187–1244, additionally covers a registered capability the task did not allow; that additional witness is source-inspected here | Re-run on the final integrated candidate; include the additional attribution witness. Do not infer this from tags or a capability catalog alone |
| 5. Independent PASS/FAIL/INCONCLUSIVE verifier | **Fresh bounded witnesses PASS before runtime changes.** [verifier tests](../../../tests/test_verifiers.py), lines 65–100, exercise executable PASS/FAIL/INCONCLUSIVE outcomes. [M3 verification tests](../../../tests/test_v04_m3_verification.py), lines 220–274, 381–432 and 543–616, cover producer-session rejection, missing coverage and material disagreement. [VerificationState](../../../src/accretion/contracts/routing.py), lines 109–141, keeps ERROR separate and blocks unresolved outcomes | Re-run on the final integrated candidate. A new simulation-specific verifier still requires its own conformance; a fake or digital witness does not verify an embodied task |

Observed local release identities on 2026-09-08:

| Tag | Annotated object | Peeled commit |
|---|---|---|
| `v0.1.0` | `3280e117aadf9ee5f431804dd92bffd2fc80229f` | `6324c8fab1776f0bcc1535f6d6c44fe95588f0e2` |
| `v0.2.0` | `2c455bac152c971ca85932262ac121c8d847274a` | `de146cd9e1a3e651e066f8dde020c7938cbc1316` |
| `v0.3.0` | `6d20bc6a3b4df4ba2f01920b3717b4cf3c69a2e0` | `bf5b774eb964252d448b44ec3ea9d6b7b7511213` |

No remote release re-verification is claimed by this local identity check.
The v0.5 entry matrix is **not yet an unconditional implementation GO**: the
integrated candidate checks above are pending, and this decision grants no
simulation or physical execution authority.

## Bounded checks for the coordinator

The coordinator reports **22 passed in 4.75 seconds** on integration commit
`b83ac5c` on 2026-09-08, before runtime repairs: the three capability cases below
plus `tests/test_verifiers.py` and `tests/test_v04_m3_verification.py`. This is
fresh bounded evidence communicated by the executing coordinator, not a test
run in this document worktree. The additional execution-attribution case below
and the final integrated rerun remain pending.

Run the following existing tests against the final candidate; they use local
or fake execution and do not require a locked scientific corpus read:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_p4_governance.py::test_unknown_and_task_denied_capabilities_fail_closed_without_execution \
  tests/test_v04_m1_gates.py::test_adding_an_unauthorized_capability_never_increases_eligibility \
  tests/test_v04_m1_gates.py::test_a_denied_capability_and_an_unlisted_one_are_told_apart \
  tests/test_v04_seam_executing_provider.py::test_every_capability_terminal_names_the_executing_provider \
  tests/test_verifiers.py tests/test_v04_m3_verification.py
```

The migration witnesses are
[M0](../../../tests/test_v04_m0_migration.py),
[freeze delta](../../../tests/test_v04_freeze_delta_migration.py),
[experience revisions](../../../tests/test_v04_experience_revisions_migration.py)
and [M8](../../../tests/test_v04_m8_migration.py). Their headers require serial
execution because they change database schema during a test. Use a dedicated
disposable database and include any migration tests added by the integrated
accounting repair. Record command, candidate commit, database isolation,
timestamp and result in a new handoff validation record; do not rewrite old
release evidence to represent the new run.

## Frozen evidence preservation

The original protocol and amendment remain byte-frozen. The access log still
contains four rows: one original and one amended read per corpus. No new row
is required to adopt this decision.

| File | SHA-256 |
|---|---|
| `docs/research/v0.4/preregistration.md` | `6b45998b3a9847298ba878a6414211ce38d64c775e5addd587509fdd70cf04ea` |
| `docs/research/v0.4/amendment-1.md` | `139f60692de5e9faa545d3e169fcf92e8aab3e45ac41a7ec1b0891cdae4e3914` |
| `docs/research/v0.4/access-log.jsonl` | `976f3357bc6b4850a9183a5d497158a8c1dafaf284546bc5dfbd2d553d04ca9e` |
