# Wave 2 adapter and verifier construction — 9 September 2026

This focused source slice implements the Wave 2 flagship-adapter and pure-verifier
lanes from the [approved plan](completion-plan-2026-09-09.md). The host/authority
lane has its own integration boundary. These modules do not finish M5 or M6,
authorize a simulation episode or satisfy a composite AC5 criterion.

The UR5e/Robotiq adapter verifies model assets, exposes SI observations, prepares
bounded quintic joint/gripper commands, refuses interrupted or duplicate effects,
and separates initial capture identity from the later approved episode pins.
The optional execution hook permits the trusted host to recheck authority at
solver and observation boundaries. Importing these modules requires no simulator.

The pure verifier correlates original contract and protocol bytes, complete raw
observation artifacts, trusted geometry, safety receipts, task predicates and
declared replay tolerances. It returns construction findings, not an independent
production PASS. Missing evidence remains incomplete or inconclusive.

## Evidence and limits

The [UR5e report](m6-ur5e-development-2026-09-09.md) preserves historical real
tracking, aperture, reaching and same-process snapshot-continuation observations.
Subsequent fail-closed and live-permit changes alter source identity; the earlier
real run does not qualify this candidate. No new simulator run is claimed here.

The current internal safety/verification schema expects conservative actual
interval bounds. The adapter deliberately emits sampled development traces and
cannot supply that stronger certificate. The SDD excludes safety certification;
its command-path-interior requirement does not demand a validated continuous-ODE
proof. A separately versioned numerical-stage evidence correction is parked for
later integrated capture work. Until its producers, readers and independent
checks agree, the current sampled traces cannot become accepted verifier input.
Continuous commanded-path checks and the original safety limits remain required.

The real isolated host, complete episodes, independent verifier process, grasp,
recovery, new-process physics replay, second-adapter qualification, Studio and
benchmark/release evidence remain later integration obligations. All thirty
composite AC5 criteria stay pending.

## Validation

The source was split from locally reviewed integration `2724080f` without changing
adapter/verifier implementation bytes. This narrower candidate requires its own
construction tests and protected CI before merge; those results are recorded
only after their actual completion. Shared SDK initialization pins are additive.
The frozen 187-file imported SDD package and the released v0.4.1 line are unchanged.
