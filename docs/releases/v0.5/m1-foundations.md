# v0.5 registry and execution foundations

Status: combined local construction checks passed; protected review pending. This record does not claim
complete M1/M3 acceptance or actual simulator conformance. Follow the
[execution checkpoint](execution-2026-09-09.md) for confirmed results and the
[API contract](m0-api-contract.md) for the full planned surface.
The [registry persistence note](m1-registry-2026-09-09.md) records the additive
migration, event-contract extension and attestation boundary.

## Registry access

`ACCRETION_ENABLE_SIMULATION` defaults to `false`. Enabling it constructs the
declaration registry; it starts no simulation worker. Collections and details
require explicit `workspace_id` and `project_id` query parameters. The service
checks the persisted principal, membership and explicit project binding on
every access. Immutable registrations require an `Idempotency-Key`; responses
carry an ETag, original writer JSON and its verified content hash. A duplicate
key with changed content conflicts. A second record cannot replace an existing
logical name/version. An absent or foreign resource returns a generic 404.

The HTTP boundary streams at most 1 MiB and 1,024 chunks within ten seconds,
refuses duplicate authority headers and query fields, and accepts UTF-8 JSON
without content compression. Current read projections never replace the
original writer document. The OpenAPI request deliberately describes a sealed
writer object; clients must preserve the original JSON when forwarding it,
including future-minor fields and JSON number spelling. The typed response
contains `original_json`; the canonical schemas are exported separately under
[the contract inventory](../../contracts/v0.5/README.md).

For a single-user local deployment, an explicit
`ACCRETION_SIMULATION_LOCAL_PROJECT_IDS` JSON list may bind known existing
projects to `workspace_local` during startup. The binding is durable and cannot
be reassigned. This bootstrap is refused in OIDC mode. A request header, a
workspace membership alone, or the first registration cannot claim an unbound
legacy project. Shared deployments must provision bindings through trusted
administration before access; no new public ownership-claim route is exposed.

Adapter manifest registration requires an injected artifact trust verifier.
The default application does not configure one, so manifest registration
remains unavailable until the host trust path is installed. Conformance is
joined against the exact dependency closure and admitted independent report;
requested capabilities grant no permission. The public conformance-run route
checks scope then returns `SIMULATION_UNAVAILABLE` until an admitted host can
actually execute the suite. A caller-provided PASS cannot activate an adapter.

## Artifact and execution boundaries

The content-addressed artifact store publishes immutable SHA-256 blobs with
per-artifact and total byte limits, bounded chunks and a process lock. It
refuses symlink substitution, nonregular files, digest/length changes and
physical evidence. Consumers must finish the stream and verify the final
integrity result before using bytes. The store grants no project authority;
the calling service must authorize the scoped reference. No deletion or
retention downgrade is exposed by this primitive.
Known-digest duplicate streams are fully consumed and verified before reusing
an existing blob; reuse also syncs the directory so a retry can complete a
previous publisher's interrupted durability step. Streams without an expected
digest require staging headroom. An expected digest never bypasses input or
existing-file integrity checks.

Protocol construction tests use a fault adapter without importing MuJoCo.
They cannot produce activation-ready conformance evidence. The pure safety
evaluator checks commanded quintic Hermite trajectories conservatively, while
actual tracking, contacts and swept geometry require a trusted host preview.
Each unordered contact body pair has one aggregate force/penetration bound per
physics interval; duplicate entries cannot divide the permitted force limit.
Signed safety receipts alone are insufficient admission: M2 must persist the
exact issuance context and preview keyed by receipt hash, then atomically
compare the live phase, physics step, dependency closure, lease, approval and
budgets before consuming authority once. Unknown acknowledgements never
authorize a resend to the original episode.

Combined tests and PostgreSQL parity passed on the candidates identified in
the [construction validation](evidence/m1-construction-2026-09-09/README.md).
The actual isolated host and both real adapters remain separate evidence obligations. All thirty composite AC5
criteria remain pending until their required evidence classes are complete.
