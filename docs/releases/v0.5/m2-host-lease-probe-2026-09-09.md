# Wave 2 combined host and lease probe

Status: **four-case actual host/lease construction probe passed** (2026-09-09).
The [finite packet](m2-host-probe-packet-2026-09-09.md) joins the actual Docker host,
Unix authority channel and transport with PostgreSQL leases/reservations/journal.
All policy, conformance, approval and observation fixtures are explicitly synthetic.
It runs no robot or simulator and supplies no operational acceptance authority.

Attempt 1 on `08a22afce9bf70415279e54e49acdf20d651cf75` built the fixed nonrobot
image and ran a durable two-client lease race, but host preparation refused the
IPC directory's actual `0755` mode. The output filesystem did not retain the
requested `0700` mode. **No container was created or started**, and the residual
container check was empty. No dispatch or cleanup success is claimed.

The [unaltered original attempt](evidence/m2-host-lease-probe-2026-09-09/attempt-01.zip)
and [hash inventory](evidence/m2-host-lease-probe-2026-09-09/attempt-01-files.json)
retain the primary isolation refusal and secondary missing-journal reporting error.
Measured use was 8.137810 wall seconds, 13.99 seconds of whole-host active CPU
(a conservative upper bound, not exact task cost), and 74,735 output bytes.

The harness now uses `/tmp` storage whose actual directory and bootstrap modes
are verified as `0700` and `0444`. The production isolation check is unchanged.
Missing pre-create journal rows are explicitly reported, preserving the original
failure. Nine pure checks passed before the second actual attempt used a fresh
output directory and database.


## Confirmed second attempt

Candidate `3c3acb1bf03226271a4bcc7d1a6f4755a71a4de9` passed all four cases
at 11:36 UTC on 9 September 2026, with its source unchanged throughout. The
[original outputs and exact runner](evidence/m2-host-lease-probe-2026-09-09/attempt-02.zip)
and [hash inventory](evidence/m2-host-lease-probe-2026-09-09/attempt-02-files.json)
retain the build context, immutable image identity, commands, confirmed exits,
Docker inspections, original wire bytes, persisted reservations and journals.

| Case | Observed result |
|---|---|
| Ownership and heartbeat loss | Two durable contenders produced one lease winner; foreign/stale pins and duplicate RPCs caused no extra mutation. One admitted RESET received its correlated acknowledgement. Heartbeat loss fenced the lease and led to confirmed cleanup. |
| Revocation and recovery | Revocation after create refused start while the actual container remained created with PID zero. A fresh supervisor in the same Python process reconstructed durable state and performed cleanup only; an injected journal outage accepted redelivery of the same observed cleanup proof. Old recovery left the newer lease generation intact. |
| Lost acknowledgement | One counter mutation followed by a partial acknowledgement/EOF left the reservation uncertain, episode aborted and resource quarantined. Resend was refused; original partial bytes were retained. |
| Expired dispatch deadline | The persisted reservation carried the actual 750 ms fixture cap. The worker received permission and waited past that deadline; the parent refused it at expiry, with zero counter mutation. |

Four serial containers were created, all four received confirmed removal, and
both the final residual-container list and per-case cleanup-error lists were
empty. All four private IPC directories were verified as mode `0700` and removed
after confirmed cleanup. The fixed counter image was
`sha256:2bebcf88f8a54bb6878cec60c77bb361fe0532fa783f4815ce912a7229d79c5a`.

Measured use was **17.227375 wall seconds**, **30.56 whole-host active CPU
seconds as a conservative upper bound**, and **731,546 output bytes at the
recorded measurement** (731,846 bytes after finalizing the execution record), within
the reviewed 600-second, 180-CPU-second and 128 MiB envelope. Whole-host CPU
includes daemon/database/container activity and unrelated host work; it is not
an exact task cost. Missing per-container counters are not treated as zero.

This closes the combined persisted-authority/host construction witness. It does
not run a robot, qualify the UR5e adapter, activate production preflight, prove
hard realtime enforcement or an OS host-crash/restart recovery, complete an
episode, or discharge composite AC5
criteria. Final regression checks and protected integration remain separate
Wave 2 exit requirements.
