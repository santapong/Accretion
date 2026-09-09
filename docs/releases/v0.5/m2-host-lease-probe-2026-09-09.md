# Wave 2 combined host and lease probe

Status: **first attempt refused before container creation; corrected retry pending**.
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
failure. Nine pure checks passed; the next actual attempt uses a fresh output
and database and must pass all four cases before Wave 2 can be complete.
