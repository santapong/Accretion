# M2 runtime and authority boundaries

Status: construction in progress under the
[approved completion plan](completion-plan-2026-09-09.md). These declarations
are not observed host isolation, persisted lease behavior or AC5 acceptance.

The coordinator's M2 event extension adds strict simulation run-binding, lease,
approval, safety-issuance and dispatch lifecycle literals. Existing event
meanings and frozen M0 evidence are unchanged. Normal lease release is distinct
from revocation or expiry; cleanup confirmation is a separate event because
expiry alone does not prove that an old process stopped. Dispatch completion
and uncertainty remain attributable after the original admission event.

The planned host uses a dedicated bounded authority channel per lease. The SDK
guard requests admission from the trusted parent; an adapter receives no
database access or private signing key. The parent compares the exact request
with its admitted in-flight request before accessing durable authority.
Lease generation, current identity/policy/approval/key state, complete retained
safety issuance, phase, observation and budgets must agree in the reservation
transaction. An unexpected acknowledgement preserves the reservation and
terminates the episode without resending.

Migration 0022 adds the previously omitted optional initiating principal to the
PostgreSQL Run mapping, preserving legacy unknown values. New simulation runs
require a real persisted human initiating principal, distinct from the service
orchestrator. Task, Run and SimulationRunBinding are created atomically in one
shared unit of work. No
provider label, test fixture or separately committed dummy run supplies that
ownership.

Actual host/container checks, storage and policy witnesses, adapter behavior,
separate verification and full episode recovery remain implementation gates.

## Host construction checkpoint

Wave 1 merged through [PR #183](https://github.com/santapong/Accretion/pull/183)
as `da7e806f7ab3a4eef0b6844b5777d9cb66370ccd`, after all eight CI checks passed.
Its fetched merge tree equals reviewed head `bb50017f0f10e08fe72e4ed137ebff561b101fcd`.
The coordinator and three agents are now constructing the runtime, real UR5e
adapter and pure verifier in separate worktrees under the approved plan.

The host primitives now provide one bounded Unix authority channel per lease,
single-flight JSONL child transport, an immutable internal launch inventory and
an inspected local Docker launch/cleanup path. The worker receives neither
database access nor private signing keys. Exact pending request and pins are
compared before one durable callback; a duplicate cannot invoke it twice. Lost
acknowledgements and failed completion commits retain the original bytes and
reservation. Stderr, frame sizes, request/wall/liveness deadlines are bounded.
Cleanup proceeds even if the durable failure callback raises.

The Docker supervisor admits full local image IDs only, never mutable tags or
manifest-supplied commands. It requires nonroot execution, the default seccomp
and AppArmor restrictions, dropped capabilities, no new privileges, a read-only
image, one read-only private bootstrap/IPC mount, no network or physical device
mounts, bounded memory/swap/processes/tmpfs, CPU rate and process CPU limits.
It verifies the actual created configuration before starting the entrypoint,
then kills the container, observes stopped PID zero and confirms removal.
These settings follow the [Docker run reference](https://docs.docker.com/reference/cli/docker/container/run/)
and [seccomp documentation](https://docs.docker.com/engine/security/seccomp/);
the [local construction probe](evidence/m2-host-construction-2026-09-09/README.md)
records what was actually observed on this host.

The scoped artifact RPC now exposes one private Unix socket per lease. A worker
can read only exact references in the supervisor's initial inventory or its own
verified uploads; possession of a digest from the global store grants no access.
Writes require SIMULATION/RUN metadata and are limited by call, artifact, output
and time budgets. The live access guard is mandatory. Reserved output bytes stay
charged after interrupted writes, and shutdown drains filesystem work before
the store may close. Integrity and complete framing are checked on both sides.
This channel's byte accounting is in-memory construction accounting, not the
durable episode ledger. It supplies no episode approval or acceptance authority.

Both authority and artifact sockets cancel accepted handlers before waiting for
server shutdown. A committed admission whose reply is lost or whose channel is
closing remains attributable and cannot produce a fresh worker permit. This
ordering is covered by real Unix IPC tests, including a publication thread that
outlives its cancelled socket handler.

The worker entrypoint now validates a bounded private bootstrap, constructs the
exact pinned model/description and emits the verified actual initial observation.
It waits for activation matching that ready frame and every expected authority
pin before entering the SDK. Each operational request still uses the live
authority guard. No placeholder approval, synthetic preflight or dynamic module
path is accepted by the entrypoint. Its lazy adapter import preserves ordinary
API installations without simulator dependencies. Bootstrap/pins tests use a
synthetic adapter and are not real robot initialization evidence.

An independent PID-1 watchdog monitors the private container's cumulative cgroup
CPU counter and a wall/authority deadline while the adapter runs in a child.
Normal exit, a stalled child and two CPU-consuming descendants were tested in
[restricted containers](evidence/m2-watchdog-construction-2026-09-09/README.md).
Missing/regressed counters fail closed; diagnostic backpressure cannot block
cleanup. This polling monitor is not a hard real-time scheduler. The Docker CPU
rate and per-process rlimit remain additional limits, and durable whole-episode
budget accounting still needs integration.

The durable host journal now persists immutable launch identity before Docker
creation, fences the owned lease for cleanup and supports cleanup-only restart
reconciliation. Its [four-case actual host witness](m2-host-lease-probe-2026-09-09.md)
passed with all containers removed and original attempt evidence retained.
The ten-check preflight builder refuses missing evidence and always reports
`activation_eligible=False`; trusted production checkers and operational receipt
commit remain with episode integration. This is not a complete operational M2
service or a production-qualified robot image.
The probe uses a dedicated image with no robot or simulator and cannot activate
an adapter or satisfy composite AC5 criteria. The parked numerical-stage evidence correction,
actual independent conformance and verification remain separate obligations.
