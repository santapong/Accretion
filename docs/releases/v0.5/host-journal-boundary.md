# Durable host creation and cleanup boundary

Wave 2/M2 construction slice, 2026-09-09. The internal journal records host
creation identities and cleanup proofs. It does not record episode captures,
normal termination, verification handoffs or acceptance results.

`HostCreationJournal` requires an existing `SimulationAuthority`, exact trusted
`HostProfileBinding` inventory and a supervisor-issued witness verifier. The
caller is the persisted active host SERVICE principal in the bound project.
Creation also rechecks the initiating HUMAN, orchestrator, policy, host and
conformance through the existing authority service. Configuration must be current
at commit; expiry after an awaited collaborator rolls back the mutation.

`HostJournalHooks` implements the Docker callbacks. Each callback opens its own
transaction in its actual asyncio task; no transaction handle crosses a callback
or supervisor task boundary. The hook scope is deployment configuration, never an
HTTP identity header. Docker independently bounds acknowledgement waits and stops
owned processes even if journal publication fails.

| Operation | Durable result |
| --- | --- |
| `begin` / `planned` | Exact name, image/profile/lease originals, bootstrap digest and absolute directory committed before create; only a fresh result permits create. |
| `created` | Exact supervisor-issued container witness and bounded raw inspection originals, after inspection and before worker start. |
| `cleanup_started` | Latest owned lease fenced, resource quarantined, pending dispatch marked uncertain, cleanup attempt appended atomically. |
| `authorize_recovery` | Committed fence followed by a fresh current-state read; returns only the exact cleanup identity. Never authorizes attach/start. |
| `cleaned` | Exact supervisor-issued stopped/removal proof retained; resource stays quarantined until `SimulationAuthority.confirm_cleanup` calls the journal's `CleanupAuthority.verify`. |
| `unresolved` | Scoped page of unresolved records plus `next_after`; a filtered empty page can still have a continuation. Listing grants no Docker operation. |

One creation attempt is allowed per lease. Docker names and retained CIDs are
unique. Immutable planned pins cannot change; CID can only narrow from unknown
to the independently observed exact CID. Each record has at most 32 ordered,
hash-chained lifecycle entries and at most 1 MiB of original retained JSON.
A changed-body idempotency retry conflicts. A historical receipt cannot reopen
creation, reverse cleanup or restore execution permission.

If create commits at the daemon but its reply is lost, the planned row survives.
Restart first fences the old lease, then Docker verifies the recorded exact
name/label/image/profile/directory/CID and only removes that owned container.
Missing or unreadable inspection is unresolved, not proof of absence. An exact
cleanup witness retained by the same supervisor can be redelivered after a
storage outage; a restarted process cannot synthesize that witness from absence.

Migration `0024_v05_host_creation_journal` adds only
`simulation_host_creations`, with existing project/workspace, episode, run,
lease, resource and host principal references. Earlier migration table lists
remain unchanged. Memory and PostgreSQL share the same service/DTO checks.

The tests use synthetic authority and supervisor proof doubles on Memory and a
disposable PostgreSQL database. They establish construction, transaction and
negative-case behavior. Actual host launch/restart and composite AC5 evidence
must be reported separately by the host validation lane.
