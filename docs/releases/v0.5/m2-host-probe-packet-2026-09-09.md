# Wave 2 finite host and lease integration probe

Status: **SOURCE REVIEW PACKET — NOT EXECUTED** (2026-09-09). This is the
coordinator's Wave 2 host/lease witness. It does not run a robot, qualify an
adapter, accept an episode, or establish v0.5 completion. Stop before Wave 3.

## Exact boundary and authority

The fixed image runs a nonrobot request counter. The host uses production
`DockerSupervisor`, `LeaseAuthorityChannel`, `AdapterTransport`,
`SimulationAuthority`, PostgreSQL runtime transactions, and
`HostCreationJournal`/`HostJournalHooks`. Actual Docker inspection, Unix IPC,
durable lease/reservation rows, mutation diagnostics and cleanup observations
must agree. A synthetic callback-only test is insufficient for this packet.

Test identities, model/closure metadata, policy and conformance collaborator,
preflight receipt, observation payload and human approval are explicitly owned
by the existing `tests/test_v05_authority.py` fixture. They carry
`SYNTHETIC_TRUST_CONTROL`/construction labels. The fixture profile uses the
existing internal UR5E enum solely to exercise journal validation; its exact
image ID and manifest name a **NONROBOT_CONSTRUCTION_PROBE**. It is never
installed as production configuration or presented as UR5e evidence. No
production worker factory, robot model, signing-key inventory, learned policy,
live grant or acceptance attestor is used. Missing real preflight evidence
continues to refuse operational activation.

The ten-check `host/preflight.py` builder is separately tested evidence
assembly. It always returns `activation_eligible=False`; the packet must not
convert its construction findings into a production preflight receipt.

## Inputs, files and finite resources

- Runner: `tests/test_v05_host_lease_probe.py` (explicit opt-in; ordinary CI
  skips without `ACCRETION_RUN_HOST_LEASE_PROBE=1`).
- Fixed image source: `scripts/robotics/m2_host_probe/{Dockerfile,worker.py}`.
- Pinned base: `python@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf`.
  Build only from the already-local base with network disabled and no pull;
  missing base stops the packet. Capture the actual built image ID and source
  hashes before admission. Never reuse the stale robot image `74ba36d...`.
- Output root: `/mnt/data/accretion-v05-m2-host-lease-probe-2026-09-09/` (new,
  task-owned directory). The wrapper stores context/accounting/logs there and
  passes a new empty `observations/` subdirectory to the harness. Persist all
  failed attempts too.
- Bootstrap/Unix sockets: task-owned private subdirectories below that output,
  mode0700 with bootstrap mode0444; sole worker mount read-only.
- Disposable database: a new `accretion_v05_host_probe_20260909` database on
  task container `accretion-v05-20260909-pg`. Supply its known URL through
  `ACCRETION_DATABASE_URL`/`ACCRETION_TEST_POSTGRES_URL`; do not print URLs or
  inspect credentials. Migrate this database only to the reviewed candidate.
- At most four containers, serial; each 0.5 CPU,128MiB memory with no swap,
 16PIDs,1MiB `/tmp`,1MiB `/dev/shm`,5 CPU seconds,20 wall seconds. Rootfs
  read-only, nonroot UID/GID, all capabilities dropped, no-new-privileges,
  private cgroup namespace, default seccomp, networknone. No device, privileged,
  Docker socket, credential, shared writable artifact or host-network mount.
- Whole packet: at most600s wall,180s aggregate measured CPU,128MiB output.
  Host command log and response evidence each have a fixed byte ceiling; abort
  on any exceeded ceiling. Root's external wrapper measures the delta of all
  active host CPU ticks from `/proc/stat` (user+nice+system+irq+softirq+steal;
  exclude idle/iowait and double-counted guest ticks). This is a conservative
  **host-wide upper bound**, including Docker/build/PostgreSQL and unrelated
  work, not exact task cost. Child RUSAGE is separate supporting evidence.
  Exceeding180s refuses completion even if unrelated host work contributed.

## Four serial cases and required observations

| Case | Required actual combined observation |
| --- | --- |
|1 Lease ownership, once-only dispatch and heartbeat loss| Two simultaneous durable contenders for one resource yield one lease. Only its exact episode/generation can obtain an authority permit and advance the nonrobot counter. Foreign/stale scope and duplicate RPC fail without another mutation. One RESET has one durable reservation and exactly correlated acknowledgement. Loss of heartbeat fences/quarantines the lease, stops the actual container and records stoppedPID0 plus removal. |
|2 Revocation before start and cleanup recovery| Persist and create a container, revoke its lease before `start`, then prove the mandatory current journal start guard denies before Docker start. A fresh supervisor/journal instance resolves the durable attempt for cleanup only; never attach/resume. Retain exactCID/name/bootstrap/image evidence. Inject a labelled post-removal journal publication outage and require same-process redelivery of the exact retained cleanup witness. After durable cleanup permits a new generation on the same resource, replay old recovery and require the new resource/lease remain unchanged. No second container is created. Absence after a restart without proof remains unresolved. |
|3 Lost acknowledgement after a mutation| Child obtains one durable reservation, increments once, emits a bounded mutation diagnostic, then exits without acknowledgement. Transport retains partial/absent ACK distinction and fences by request digest. Dispatch becomes uncertain, episode aborted and resource quarantined. A resend is refused. Actual stoppedPID0/removal and durable cleanup evidence remain attributable. |
|4 Exact permit deadline| A labelled current fixture interval is capped750ms inside the reservation transaction. The persisted reservation must carry the earliest mandatory deadline; the worker must record PERMIT_RECEIVED before its intentional delay. Require retained LEASE_INVALID at or after that exact deadline, zero mutations, once-only reservation and actual cleanup. An arbitrary crash/EOF or zero mutations alone does not satisfy this case. No extra container is used for output exhaustion; that remains the explicitly separate existing pure transport witness. |

No success is inferred from silence, a missing container name, a fixture PASS
label, an exception alone, or a process exit without current stopped/removal
observations. Failed assertions are evidence and prevent a Wave 2 completion
claim. No retry is automatic; any corrected rerun needs new review of changed
inputs and retains the failed packet.

Before the real start in each case, substitute the admitted profile's resource,
image and adapter pins before prepare, then substitute the actual created
witness's CID, bootstrap digest and image profile. All must refuse; reinspection
must still show statuscreated/PID0. This tests replacement of authenticated
handles/pins through the joined host boundary. Actual on-disk bootstrap tampering
remains a separate pure regression and is not mislabeled as executed here.

## Review and execution order

1. Root and C review the fixed worker, harness and these authority labels. Pin
   candidate commit, Python lock, Docker base/image, worker, watchdog and harness
   hashes. Review A's journal and the mandatory prestart guard together.
2. Confirm output directory is new; local base available; no preexisting
   task-labelled probe containers. Run pure source tests first.
3. With approved finite packet, build the COPY-only image with `--network=none
   --pull=false`, capture command/exits and actual image inspection, then migrate
   only the disposable database. Build is a prerequisite, not an isolation test.
4. Run exactly the opted-in four-case harness serially with the image ID and
   output path in `ACCRETION_HOST_LEASE_PROBE_IMAGE_ID` and
   `ACCRETION_HOST_LEASE_PROBE_OUTPUT`, plus
   `ACCRETION_RUN_HOST_LEASE_PROBE=1`. No `pytest -n`, background retry,
   model downloads, provider access or simulator imports.
5. In `finally`, fence the latest owned lease before killing/removing each exact
   container. Cleanup failure leaves durable unresolved evidence. Close sockets
   and local attach processes; verify no task-labelled containers remain.
6. Write a source-linked result with commands/exits, each claim and raw witness,
   measured resource upper bound, all failures and residual process/container state.
   Stop; no Wave 3 work follows this packet.

## Existing evidence that may be reused

`evidence/m2-host-construction-2026-09-09/README.md` records actual nonroot,
capability, filesystem/network and kernel resource-limit probes.
`evidence/m2-watchdog-construction-2026-09-09/README.md` records the actual PID1
watchdog with a stalled worker and two CPU-burning descendants. Reuse the latter
only after hashing the executed watchdog closure against this candidate; report
historical evidence explicitly. The current packet adds durable lease/journal
and private authority IPC integration. It does not relabel old watchdog or
simulator evidence as newly executed.

## Implementation status at packet creation

Committed: Docker create-before-IO hooks (`ee16d374`), exact cleanup-proof
redelivery (`6419d79`), preflight assembly (`73c81e2`); A's journal `29f905ee`
integrated by root as `400a717`; mandatory fresh prestart checks (`d1da55ec`,
`63d6684`). The fixed counter image and four-case harness are implemented
construction artifacts awaiting final review, with8 pure source/evidence tests passing and actual
execution intentionally skipped. The packet also requires A's queued reservation
deadline repair; the harness asserts its persisted cap includes mandatory lease,
heartbeat and approval expiry rather than masking missing metadata at IPC.
Root must pin the final focused host candidate after these repairs, not this
development tree's broader historical closure. Actual execution remains pending
final source review. No new production checker or
Wave 3/4 qualification dependency is required merely to exercise this bounded
construction-authority Wave 2 gate.
