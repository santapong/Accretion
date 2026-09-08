# Approved v0.4 closure execution

Started 2026-09-08 (Asia/Bangkok). Status: **IN PROGRESS**.
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
| C0 | Worker commit `68c39a3`, integrated as `dcf98c6`; docs check passed for 221 Markdown and 18 SVGs, SVG rendered and inspected; frozen package and protocols preserved | Implemented; final integrated docs validation pending |
| C1/C2 budget | Original witnesses reproduced double admission, lost overrun and unusable accounting accepted as empty. Repair `0b2879`, integrated as `7a4db47`, passed 78 focused tests with PostgreSQL and separate processes; Ruff/mypy/schema checks passed | Implemented and reviewed; final integrated gates pending. [Accounting runbook](../../runbooks/v04-routing-accounting.md) defines scope and mixed-version rollout limits |
| C1/C2 compatibility | Initial witnesses reproduced hidden graph receipts and false lineage 404s. Repair `0a76d83`, integrated as `4e52ec3`, passed 518 focused tests across MemoryStore and PostgreSQL, with Ruff/mypy clean | Implemented; final review and integrated gates pending. Verified writer seals and scope preserve read lineage; unknown execution/accounting fields and executable amendments remain denied |
| R0 | [Adopted scoped NO-GO](research-handoff-2026-09-08.md), preserving PARTIAL paired synthetic evidence; 22 bounded capability/verifier tests passed at `b83ac5c` in 4.75 seconds | Decision recorded; final migration and capability/verifier refresh pending |
| R1 | [Separate draft study](../../research/v0.4/provider-pilot-2026-09-08/README.md), schema, 39 passing tests and [clean-commit fake execution](../../research/v0.4/provider-pilot-2026-09-08/instrumentation-report-2026-09-09.md) at worker `cb122b7`; 48 exported files validated from the Git index | FAKE-only preparation verified with PASS/FAIL/INCONCLUSIVE and zero provider calls; final integrated refresh pending; live execution NOT_AUTHORIZED |

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

Final integrated `make check`, `make test`, `make acceptance`, `make release-gate`,
clean-database migration cycle, and required remote checks are pending. No new
release, complete test run, priced provider result or scientific read is claimed.

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
