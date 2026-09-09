# M2 watchdog construction evidence — 9 September 2026

Scope: **construction containment only; no robot, simulation acceptance or AC5
completion**. Follow the [runtime boundary checkpoint](../../m2-runtime-boundaries.md).
The [manifest](manifest.json) pins exact files. Each run record includes the
uncommitted source file hashes tested after integration commit `114b6de`.

Three local Docker containers ran the actual PID-1 watchdog with a deliberately
synthetic workload in place of the robot worker. The image copied the watchdog
and error vocabulary byte-for-byte, used the pinned Python base from the prior
isolation probe and required no network during build or execution. The synthetic
bootstrap and worker are explicitly test-only; these runs do not test the
production worker bootstrap or exercise an operational approval.

| Workload | Observed result |
|---|---|
| Normal child return | Exit 0; cumulative container CPU 296,830 microseconds |
| Child stalled without protocol progress | Watchdog exit 124 under its one-second wall allowance; 1.89 seconds measured including Docker preparation/start |
| Two busy forked descendants | Watchdog exit 124 at 1,792,881 microseconds cumulative cgroup CPU, below the two-second allowance |

Every container was inspected, observed stopped with PID zero and removed. The
monitor reads the read-only private cgroup v2 counter; it includes child processes
and threads, unlike a per-process CPU rlimit. It reserves 250 ms CPU and polls
every 50 ms. Scheduler latency prevents a hard real-time guarantee; a durable
episode budget and parent supervision remain required. Diagnostic writes are
nonblocking and shutdown does not wait indefinitely for a killed child.

The separate 09:15:47 isolation rerun verified the inspected EGL/software-renderer
environment, read-only image/bootstrap, no egress/device access and kernel limits
with the prior non-robot probe image. No renderer was initialized by that probe.
Its fresh scoped inspection is retained in
[the CI fixture](../../../../../tests/fixtures/robotics/host/created-egl-construction.json).
The older OSMesa fixture and original evidence remain historical and unchanged.

Raw daemon/image/inspection responses remain in the task's private evidence
directory. These scoped records retain checks and cleanup outcomes without
publishing the daemon's broader configuration. The preserved probe source uses
`.py.txt` extensions to keep original construction bytes out of source linting;
reconstruct `.py` filenames and empty package `__init__.py` files before a
deliberate replay. Runtime launch uses the recorded immutable image ID, never
the development build tag.
