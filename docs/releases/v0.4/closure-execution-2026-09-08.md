# Approved v0.4 closure execution

Started 2026-09-08 (Asia/Bangkok). Local validation completed 2026-09-09.
Status: **MERGED INTO DEVELOP — approved closure complete on 2026-09-09.**
Santapong approved the [parallel completion plan](completion-plan-2026-09-08.md)
in the Codex conversation. This record distinguishes executed checks from the
original planning audits and existing v0.4.0/v0.4.1 releases.

## Isolation and ownership

The execution base is freshly fetched `origin/develop` at
`8ded8bab151267ec8e06182f52543dabed6eaca7`. Four worktrees under
`/mnt/data/accretion-v04-execution-2026-09-08/` preserve the original checkout
and planning worktrees:

| Worktree | Branch | Ownership |
|---|---|---|
| `integration` | `fix/v04-closure-hardening` | Coordinator, integration, compatibility witnesses and final gates |
| `documentation` | `docs/v04-close-status`, then `test/v04-upcast-identity` and `fix/v04-upcast-identity` | C0 corrections, preserved compatibility witness checkpoint, then bounded repair |
| `budget` | `fix/v04-budget-admission` | C1/C2 shared admission and authoritative accounting |
| `research` | `docs/v04-next-pilot-protocol` | R0 claim decision, R1 prospective protocol and fake instrumentation |

The coordinator plus three workers is the maximum concurrent team. Each testing
worktree has its own locked Python environment; its `accretion` import path was
checked against that worktree. Early research probes used the original checkout's
dependency interpreter with an explicit local `PYTHONPATH=src`; the final 39-test
run and committed fake execution used the research worktree's own environment.
One task-owned, disposable PostgreSQL 16 container
uses loopback port 55441 and separate integration, budget, research and
compatibility databases. Concurrency witnesses intentionally share the budget
lane's database across independent clients. No production service is reused.

## Evidence and current dispositions

| Package | Executed evidence | Current disposition |
|---|---|---|
| C0 | Worker commit `68c39a3`, integrated as `dcf98c6`; SVG rendered and inspected; integrated docs check passed for 232 Markdown and 18 SVGs; frozen package and protocols preserved | Implemented and locally verified |
| C1/C2 budget | Original witnesses reproduced double admission, lost overrun and unusable accounting accepted as empty. Repair `0b2879`, integrated as `7a4db47`, passed 78 focused tests with PostgreSQL and separate processes; combined full suite and gates passed | Implemented, independently reviewed and locally verified. [Accounting runbook](../../runbooks/v04-routing-accounting.md) defines scope and mixed-version rollout limits |
| C1/C2 compatibility | Initial witnesses reproduced hidden graph receipts and false lineage 404s. Repair `0a76d83`, integrated as `4e52ec3`, passed 518 focused tests across MemoryStore and PostgreSQL; combined full suite and gates passed | Implemented, independently reviewed and locally verified. Verified writer seals and scope preserve read lineage; unknown execution/accounting fields and executable amendments remain denied |
| R0 | [Adopted scoped NO-GO](research-handoff-2026-09-08.md), preserving PARTIAL paired synthetic evidence; final migration, capability and independent-verifier witnesses passed in the combined gates | Decision recorded; inherited technical entry conditions have evidence, without expanding execution scope |
| R1 | [Separate draft study](../../research/v0.4/provider-pilot-2026-09-08/README.md), 39 passing tests and [clean-commit worker execution](../../research/v0.4/provider-pilot-2026-09-08/instrumentation-report-2026-09-09.md); 48 exported files validated from the Git index; fresh integrated run at `f8b47a8` also passed | FAKE-only preparation verified with PASS/FAIL/INCONCLUSIVE and zero provider calls; live execution NOT_AUTHORIZED |

Fixture-construction errors preceding corrected C1 runs are not bug evidence.
The initial two compatibility tests exercise read lineage. Expanded witnesses
cover writer seal and derived-hash verification, row/payload scope consistency,
unknown execution inputs, and both-store parity. Independent review additionally
reproduced a projected node hash accepted by accounting and an override that
resealed a projected receipt/context into an executable successor. The repair
refuses both paths; cancellation remains a terminal operation. No stored writer
payload is rewritten and the reader projection does not become an execution pin.

Review corrected two test weaknesses before final integration: malformed-charge
fixtures now have valid node scope and assert the charge-specific refusal
(`b44c821`, 14 focused tests passed). Revision fixtures now clean only their own
superseding row (`690a1cc`): the previous budget-then-migration sequence yielded
33 passes and two downgrade refusals; the corrected sequence on a fresh lane
database yielded 35 passes, zero skips and zero remaining revision rows.

## Validation and version decision

The clean integration candidate `f8b47a8a56226a729097273624dbcc2d65c20608`
passed all twelve serial commands from 00:35:27 to 00:43:14 on 2026-09-09
(Asia/Bangkok). [Machine-readable evidence](closure-evidence-2026-09-09/validation.json)
retains commands, durations and hashes of the accompanying original logs.
Those logs retain the commands' original whitespace so their hashes remain
reproducible; source and prose whitespace checks exclude only these raw outputs.

| Check | Observed result |
|---|---|
| Clean PostgreSQL upgrade → downgrade to base → upgrade | All three pass; head `0019_v04_m8_activation`; separate disposable integration database |
| `make check` | Lock, Ruff, mypy (150 source files), docs, 21 unchanged schemas, ESLint and TypeScript pass |
| `make test` | 3,544 backend tests pass; six signed-in provider tests skipped; 286 frontend tests pass across 27 files |
| `make acceptance` | `in scope: 167   proven: 159   unmet MUST: 0`; five frontend criteria and three recorded manual witnesses accounted for |
| `make release-gate` | All five conditions pass, including capability denial, isolation and inherited regressions |
| `make future-sdd-check` | 187 unchanged files; synthetic document-design conformance passes, without claiming future runtime or research execution |
| Fresh integrated fake pilot | PASS/FAIL/INCONCLUSIVE; zero provider calls; clean source and own environment recorded in [the summary](closure-evidence-2026-09-09/fake-pilot-summary.json) |
| API generation, clean generated schema and production build | All pass; generated TypeScript schema unchanged |

[Preservation checks](closure-evidence-2026-09-09/preservation.json) match the
original tags, frozen hashes and four access rows. Contract schemas, migrations,
versions, CI configuration and imported SDDs are unchanged from the execution
base. The acceptance policy has only a comment correction and parses identically.
The normal regression harness recomputes existing frozen benchmarks using
temporary logs; no new scientific read or benefit result is claimed.

Independent committed-state review re-ran the two newly discovered MemoryStore
bypasses and confirmed the intended refusals, with no successor records or
dispatch events from the rejected overrides. There are no remaining blockers
within the reviewed implementation scope. Later changes to this candidate only
record validation and handoff documentation; check those links before pushing.

The protected integration gate completed on 2026-09-09 through
[PR #179](https://github.com/santapong/Accretion/pull/179). All eight checks passed:
backend, frontend, browser and clean-checkout for both the
[branch push](https://github.com/santapong/Accretion/actions/runs/34259320348) and
[pull request](https://github.com/santapong/Accretion/actions/runs/34303998036).
There were no unresolved review conversations. GitHub recorded a squash merge
into `develop` at `abd3e0d8d0bd36b86ab28b47d1b789d87f7a7e30`; a fresh Git fetch
and tree comparison matched the reviewed `4796611` exactly. The original
checkout was fast-forwarded to that merged commit. The earlier missing-browser
access condition is historical and resolved. Subsequent dependency maintenance
is recorded in the [dependency review](dependency-review-2026-09-09.md).

Version decision, 2026-09-09: the bounded runtime repairs are compatible with a
future patch release. They introduce no public contract or database migration,
leave the routing defaults unchanged and do not add a routing capability.
This closure targets the repairs at `develop`; package versions stay at
0.4.1 until a separately prepared release candidate is promoted through the
protected `develop` to `main` workflow. No new release tag is part of this closure,
and the existing v0.4.0/v0.4.1 tags and released trees remain immutable.

R2 live execution awaits concrete providers/accounts, independently qualified
outcomes, task inventory and explicit call/money/time limits. E1 selected AGENT
tool support remains separately scoped. Neither changes M0–M10 delivery status.
