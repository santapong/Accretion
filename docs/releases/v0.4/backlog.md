# v0.4 prioritized backlog

Status: **M0 in progress.** The normative contract is [SDD v0.4](../../sdd/Accretion_SDD_v0.4.md);
its §19 orders the milestones and its §20 owns the criteria. This ledger records status only.

## Delivery order

| Priority | Milestone | Owns (SDD §20) | Status |
|---:|---|---|---|
| 1 | M0 contract and feature freeze | none (ADR-052) | in progress ([plan](m0-plan.md)) |
| 2 | M1 compatibility engine | 005-008 | not started |
| 3 | M2 hierarchical deterministic selector | 001, 002, 004, 009-015, 022 | not started |
| 4 | M3 experience and feedback pipeline | 003, 023-034 | not started |
| 5 | M4 offline ranker and calibration | 016 | not started |
| 6 | M5 project adapter and cold start | 021 | not started |
| 7 | M6 shadow routing | 017, 041 | not started |
| 8 | M7 guarded bandit | 018-020 | not started |
| 9 | M8 promotion and rollback | 035-039, 042 | not started |
| 10 | M9 Experiment Studio | 040, 043, 044 | not started |
| 11 | M10 research benchmark integration | 045-050 | not started |

No milestone may enable online exploration before the M0-M6 gates pass (SDD v0.4 §19).

## Carried from v0.3

The items the v0.3 release deliberately deferred are listed under "M7 deferrals" and the
"Deferred to v0.4" notes in the [v0.3 backlog](../v0.3/backlog.md): workspace-shared and
`SERVICE_ACCOUNT` enterprise authorization, session enumeration in the identity page, real
identity-provider interoperability as an expiring manual criterion, and the token-exchange egress
allowlist. None is a v0.4 acceptance criterion; each is scheduled when a v0.4 milestone touches
its surface, and none is added to the M0 freeze. Also carried: the read-boundary schema upcaster (registry §20.5) scheduled for M8 (ADR-057).

## Parked beside v0.4

The v0.3.1 operator-UI redesign (M9 of the v0.3 ladder) is parked after its stylesheet port
completed; its remaining steps (Preflight, projection store, cosmic scene, orbit, dashboard,
release) resume from their plan when the owner reopens it.
