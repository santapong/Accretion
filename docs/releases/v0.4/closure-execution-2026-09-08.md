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
checked against that worktree. One task-owned, disposable PostgreSQL 16 container
uses loopback port 55441 and separate integration, budget, research and
compatibility databases. Concurrency witnesses intentionally share the budget
lane's database across independent clients. No production service is reused.

## Evidence and current dispositions

| Package | Executed evidence | Current disposition |
|---|---|---|
| C0 | Worker commit `68c39a3`, integrated as `dcf98c6`; docs check passed for 221 Markdown and 18 SVGs, SVG rendered and inspected; frozen package and protocols preserved | Implemented; final integrated docs validation pending |
| C1/C2 budget | Original witnesses reproduced double admission, lost overrun and unusable accounting accepted as empty. Repair `0b2879`, integrated as `7a4db47`, passed 78 focused tests with PostgreSQL and separate processes; Ruff/mypy/schema checks passed | Implemented and reviewed; final integrated gates pending. [Accounting runbook](../../runbooks/v04-routing-accounting.md) defines scope and mixed-version rollout limits |
| C1 compatibility | [New reference tests](../../../tests/test_v04_upcast_references.py) fail at the intended assertions: a coherent future-minor writer chain disappears from graph receipt listing and lineage lookup returns false 404 | Defects reproduced; bounded read-identity repair pending, unknown execution semantics remain denied |
| R0 | [Adopted scoped NO-GO](research-handoff-2026-09-08.md), preserving PARTIAL paired synthetic evidence; 22 bounded capability/verifier tests passed at `b83ac5c` in 4.75 seconds | Decision recorded; final migration and capability/verifier refresh pending |
| R1 | [Separate draft study](../../research/v0.4/provider-pilot-2026-09-08/README.md), schema and illustrative fixtures reviewed | FAKE-only implementation in progress; live execution NOT_AUTHORIZED |

Fixture-construction errors preceding corrected C1 runs are not bug evidence.
The two compatibility tests currently exercise read lineage; they do not prove
an executable configuration or permit dispatch of unknown fields. The final
repair must preserve this distinction and verify both stores.

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
The confirmed defects are candidates for a backward-compatible patch after
repair review; the exact version/release decision is still pending.

R2 live execution awaits concrete providers/accounts, independently qualified
outcomes, task inventory and explicit call/money/time limits. E1 selected AGENT
tool support remains separately scoped. Neither changes M0–M10 delivery status.
