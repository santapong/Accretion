"""Durable create-before-Docker and cleanup-only recovery; no host IO in transactions.

These internal methods accept an authenticated persisted host SERVICE identity.
Deployment supplies exact current profiles and a supervisor-issued-witness verifier.
An idempotent receipt never authorizes another create/start. The only restart
operation fences the old lease before returning its exact cleanup identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics.values import Digest, LeaseBinding
from accretion.robotics.authority import AuthorityScope, SimulationAuthority, copy_update, require
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.docker import (
    CleanupWitness,
    ContainerWitness,
    CreationAttempt,
    LaunchProfile,
)
from accretion.robotics.host.inventory import HostProfileBinding
from accretion.robotics.protocol import bounded_json
from accretion.robotics.runtime_store import (
    DispatchReservation,
    HostJournalEntry,
    RuntimeEpisode,
    RuntimeHostCreation,
    RuntimeLease,
    RuntimeResource,
    RuntimeTransaction,
)


class HostWitnessVerifier(Protocol):
    def verify_created(self, attempt: CreationAttempt, witness: ContainerWitness) -> None:
        """Require an exact supervisor-issued witness; perform no IO."""
        ...

    def verify_cleaned(self, attempt: CreationAttempt, witness: CleanupWitness) -> None:
        """Require actual observed stop/removal proof, never a constructed dataclass."""
        ...


@dataclass(frozen=True)
class HostJournalAdmission:
    record: RuntimeHostCreation
    fresh: bool


@dataclass(frozen=True)
class HostJournalPage:
    records: tuple[RuntimeHostCreation, ...]
    next_after: str | None


def creation_identity(lease_id: str) -> str:
    return "host_creation_" + content_hash(["host-creation-v1", lease_id], exclude=())


def _text(raw: bytes, limit: int) -> str:
    require(type(raw) is bytes and 0 < len(raw) <= limit, Code.PAYLOAD_TOO_LARGE)
    bounded_json(raw, max_bytes=limit)
    return raw.decode("utf-8")


def _attempt_values(attempt: CreationAttempt, bootstrap_digest: str | None) -> dict[str, Any]:
    profile = LaunchProfile.model_validate_json(_text(attempt.profile_bytes, 16384))
    lease = LeaseBinding.model_validate_json(_text(attempt.lease_bytes, 4096))
    require(canonical_json(profile) == attempt.profile_bytes, Code.INVALID_CONTRACT)
    require(canonical_json(lease) == attempt.lease_bytes, Code.INVALID_CONTRACT)
    require(profile.image_id == attempt.image_id, Code.INVALID_CONTRACT)
    directory = str(attempt.bootstrap_directory)
    # Do not inspect the filesystem while holding a transaction. Docker separately
    # opens the exact private directory/bootstrap with no-follow and byte bounds.
    require(
        attempt.bootstrap_directory.is_absolute()
        and str(Path(directory)) == directory
        and ".." not in attempt.bootstrap_directory.parts
        and not any(c in directory for c in ("\0", "\n", ",")),
        Code.INVALID_CONTRACT,
    )
    supplied = getattr(attempt, "bootstrap_digest", None)
    if bootstrap_digest is None:
        bootstrap_digest = supplied
    require(supplied is None or supplied == bootstrap_digest, Code.INVALID_CONTRACT)
    digest = TypeAdapter(Digest).validate_python(bootstrap_digest)
    return dict(
        episode_id=attempt.episode_id,
        host_instance_id=attempt.host_instance_id,
        docker_name=attempt.name,
        resource_id=profile.resource_id,
        lease_id=lease.lease_id,
        lease_generation=lease.generation,
        profile_original=attempt.profile_bytes.decode(),
        profile_hash=content_hash(profile, exclude=()),
        lease_original=attempt.lease_bytes.decode(),
        bootstrap_directory=directory,
        bootstrap_digest=digest,
    )


def creation_attempt(row: RuntimeHostCreation) -> CreationAttempt:
    profile = LaunchProfile.model_validate_json(row.profile_original)
    values: dict[str, Any] = dict(
        name=row.docker_name,
        host_instance_id=row.host_instance_id,
        image_id=profile.image_id,
        profile_bytes=row.profile_original.encode(),
        episode_id=row.episode_id,
        lease_bytes=row.lease_original.encode(),
        bootstrap_directory=Path(row.bootstrap_directory),
        container_id=row.container_id,
    )
    # The Docker hook extension is additive across integration branches. Its
    # bootstrap pin is mandatory at journal entry even on the older dataclass.
    if "bootstrap_digest" in CreationAttempt.__dataclass_fields__:
        values["bootstrap_digest"] = row.bootstrap_digest
    return CreationAttempt(**values)


class HostCreationJournal:
    def __init__(
        self,
        authority: SimulationAuthority,
        *,
        profiles: Sequence[HostProfileBinding],
        verifier: HostWitnessVerifier | None = None,
    ):
        require(0 < len(profiles) <= 128, Code.ISOLATION_UNAVAILABLE)
        self.authority, self.verifier = authority, verifier
        self._profiles = tuple(canonical_json(p) for p in profiles)
        identities = [(p.workspace_id, p.project_id, p.profile.resource_id) for p in profiles]
        require(len(set(identities)) == len(identities), Code.INVALID_CONTRACT)

    async def _context(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        episode_id: str,
    ) -> tuple[RuntimeEpisode, RuntimeLease, RuntimeResource]:
        await tx.require_scope(scope.workspace_id, scope.project_id)
        await self.authority._actor(tx, scope, service=True)
        ep = await self.authority._get(tx, scope, RuntimeEpisode, episode_id)
        lease, resource = await self.authority._lease(tx, scope, ep, live=False)
        require(scope.actor_id == resource.host_principal_id, Code.CAPABILITY_DENIED)
        return ep, lease, resource

    async def _configuration(
        self,
        tx: RuntimeTransaction,
        row: RuntimeHostCreation,
        ep: RuntimeEpisode,
        lease: RuntimeLease,
        resource: RuntimeResource,
    ) -> None:
        require(
            row.episode_id == ep.id
            and row.run_id == ep.run_id == lease.run_id
            and row.lease_id == lease.id == ep.lease_id
            and row.lease_generation == lease.generation
            and row.resource_id == resource.id == lease.resource_id
            and row.host_principal_id == resource.host_principal_id
            and row.host_instance_id == resource.host_instance_id
            and row.dependency_hash == content_hash(ep.setup.dependencies, exclude=())
            and row.lease_original
            == canonical_json(
                LeaseBinding(lease_id=lease.id, generation=lease.generation)
            ).decode(),
            Code.LEASE_INVALID,
        )
        profiles = [HostProfileBinding.model_validate_json(raw) for raw in self._profiles]
        matches = [
            p
            for p in profiles
            if (
                p.workspace_id,
                p.project_id,
                p.host_principal_id,
                p.host_instance_id,
                canonical_json(p.profile).decode(),
                p.dependencies,
            )
            == (
                row.workspace_id,
                row.project_id,
                row.host_principal_id,
                row.host_instance_id,
                row.profile_original,
                ep.setup.dependencies,
            )
        ]
        require(len(matches) == 1, Code.ISOLATION_UNAVAILABLE)
        profile = matches[0]
        now = await tx.now()
        require(
            profile.disposition == "ACTIVE" and profile.valid_from <= now < profile.valid_until,
            Code.ISOLATION_UNAVAILABLE,
        )
        tx.require_valid_interval(
            profile.valid_from, profile.valid_until, Code.ISOLATION_UNAVAILABLE
        )
        require(
            content_hash(profile.profile, exclude=()) == row.profile_hash, Code.INVALID_CONTRACT
        )

    async def _entry(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        kind: str,
        previous: tuple[HostJournalEntry, ...] = (),
        evidence: Any = None,
    ) -> HostJournalEntry:
        require(len(previous) < 32, Code.RESOURCE_CAP_EXHAUSTED)
        body = dict(
            sequence=len(previous) + 1,
            kind=kind,
            actor_id=scope.actor_id,
            occurred_at=await tx.now(),
            evidence_original=canonical_json(evidence or {}).decode(),
            previous_hash=previous[-1].entry_hash if previous else None,
        )
        return HostJournalEntry.model_validate(
            {**body, "entry_hash": content_hash(body, exclude=())}
        )

    async def begin(
        self,
        scope: AuthorityScope,
        *,
        attempt: CreationAttempt,
        bootstrap_digest: str | None = None,
    ) -> HostJournalAdmission:
        require(self.verifier is not None, Code.ISOLATION_UNAVAILABLE)
        values = _attempt_values(attempt, bootstrap_digest)
        require(attempt.container_id is None, Code.INVALID_CONTRACT)
        identity = creation_identity(values["lease_id"])
        async with self.authority._transaction(scope) as tx:
            ep, lease, resource = await self._context(tx, scope, values["episode_id"])
            current = await tx.get(RuntimeHostCreation, identity)
            previous = await self.authority._retry(
                tx, scope, "host.begin", identity, values, RuntimeHostCreation
            )
            if previous:
                require(
                    current is not None and previous.revision <= current.revision,
                    Code.INVALID_CONTRACT,
                )
                await self._configuration(tx, previous, ep, lease, resource)
                return HostJournalAdmission(previous, False)
            require(current is None, Code.CONTRACT_CONFLICT)
            require(scope.expected_revision is None, Code.REVISION_CONFLICT)
            require(ep.status == "LEASED", Code.EPISODE_STATE_CONFLICT)
            await self.authority._lease(tx, scope, ep)
            tx.require_valid_interval(
                lease.last_heartbeat_at,
                min(lease.expires_at, lease.heartbeat_deadline),
                Code.LEASE_INVALID,
            )
            await self.authority._check(tx, scope, "ACQUIRE_LEASE", ep, lease, resource)
            first = await self._entry(tx, scope, "PLANNED", evidence=values)
            deadline = min(
                lease.expires_at,
                lease.heartbeat_deadline,
                tx.authority_valid_until or lease.expires_at,
            )
            row = RuntimeHostCreation(
                id=identity,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                run_id=ep.run_id,
                host_principal_id=resource.host_principal_id,
                dependency_hash=content_hash(ep.setup.dependencies, exclude=()),
                created_at=first.occurred_at,
                created_by=scope.actor_id,
                creation_valid_until=deadline,
                entries=(first,),
                **values,
            )
            await self._configuration(tx, row, ep, lease, resource)
            row = row.model_copy(
                update={"creation_valid_until": min(deadline, tx.authority_valid_until or deadline)}
            )
            await tx.put(row, insert=True)
            await self.authority._remember(tx, scope, "host.begin", identity, values, row)
            return HostJournalAdmission(row, True)

    async def created(
        self,
        scope: AuthorityScope,
        *,
        attempt_id: str,
        witness: ContainerWitness,
    ) -> RuntimeHostCreation:
        evidence = dict(
            container_id=witness.container_id,
            host_instance_id=witness.host_instance_id,
            profile_original=_text(witness.profile_bytes, 16384),
            profile_hash=witness.profile_hash,
            episode_id=witness.episode_id,
            lease_original=_text(witness.lease_bytes, 4096),
            bootstrap_directory=str(witness.bootstrap_directory),
            daemon_original=_text(witness.daemon_bytes, 131072),
            image_original=_text(witness.image_bytes, 131072),
            inspection_original=_text(witness.inspection_bytes, 131072),
            creation_name=witness.creation_name,
            bootstrap_digest=witness.bootstrap_digest,
        )
        async with self.authority._transaction(scope) as tx:
            row = await self.authority._get(tx, scope, RuntimeHostCreation, attempt_id)
            ep, lease, resource = await self._context(tx, scope, row.episode_id)
            await self._configuration(tx, row, ep, lease, resource)
            previous = await self.authority._retry(
                tx, scope, "host.created", row.id, evidence, RuntimeHostCreation
            )
            if previous:
                return previous
            self.authority._revision(scope, row)
            require(
                row.status == "PLANNED" and row.container_id is None, Code.EPISODE_STATE_CONFLICT
            )
            require(ep.status == "LEASED", Code.EPISODE_STATE_CONFLICT)
            await self.authority._lease(tx, scope, ep)
            await self.authority._check(tx, scope, "HEARTBEAT", ep, lease, resource)
            require(await tx.now() < row.creation_valid_until, Code.LEASE_INVALID)
            tx.require_valid_interval(row.created_at, row.creation_valid_until, Code.LEASE_INVALID)
            require(self.verifier is not None, Code.ISOLATION_UNAVAILABLE)
            assert self.verifier is not None
            require(
                witness.creation_name == row.docker_name
                and witness.bootstrap_digest == row.bootstrap_digest,
                Code.INVALID_CONTRACT,
            )
            require(
                (
                    witness.host_instance_id,
                    witness.profile_bytes.decode(),
                    witness.profile_hash,
                    witness.episode_id,
                    witness.lease_bytes.decode(),
                    str(witness.bootstrap_directory),
                )
                == (
                    row.host_instance_id,
                    row.profile_original,
                    row.profile_hash,
                    row.episode_id,
                    row.lease_original,
                    row.bootstrap_directory,
                ),
                Code.INVALID_CONTRACT,
            )
            self.verifier.verify_created(creation_attempt(row), witness)
            entry = await self._entry(tx, scope, "CREATED", row.entries, evidence)
            result = copy_update(
                row,
                status="CREATED",
                container_id=witness.container_id,
                entries=(*row.entries, entry),
            )
            await tx.put(result)
            await self.authority._remember(tx, scope, "host.created", row.id, evidence, result)
            return result

    async def _fence(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        ep: RuntimeEpisode,
        lease: RuntimeLease,
        resource: RuntimeResource,
    ) -> None:
        require(
            resource.current_lease_id == lease.id and resource.generation == lease.generation,
            Code.LEASE_INVALID,
        )
        if ep.in_flight:
            dispatch = await self.authority._get(tx, scope, DispatchReservation, ep.in_flight)
            if dispatch.status == "RESERVED":
                await tx.put(copy_update(dispatch, status="UNCERTAIN", completed_at=await tx.now()))
        if lease.status == "ACTIVE":
            await tx.put(copy_update(lease, status="REVOKED"))
        if not resource.quarantined:
            await tx.put(copy_update(resource, quarantined=True))
        if ep.status != "ABORTED":
            # This M2 failure/cleanup path cannot claim recorded normal completion.
            aborted = copy_update(ep, status="ABORTED")
            if lease.status == "ACTIVE":
                await self.authority._event(
                    tx, scope, aborted, "simulation_lease.revoked", {"lease_id": lease.id}
                )
            else:
                await tx.put(aborted)

    async def cleanup_started(
        self,
        scope: AuthorityScope,
        *,
        attempt_id: str,
        reason: str = "OWNED_HOST_CLEANUP",
    ) -> RuntimeHostCreation:
        require(
            reason in {"OWNED_HOST_CLEANUP", "SUPERVISOR_RESTART", "CREATE_UNCERTAIN"},
            Code.INVALID_REQUEST,
        )
        body = {"reason": reason}
        async with self.authority._transaction(scope) as tx:
            row = await self.authority._get(tx, scope, RuntimeHostCreation, attempt_id)
            ep, lease, resource = await self._context(tx, scope, row.episode_id)
            await self._configuration(tx, row, ep, lease, resource)
            previous = await self.authority._retry(
                tx, scope, "host.cleanup_started", row.id, body, RuntimeHostCreation
            )
            if previous:
                return previous
            require(row.status != "CLEANED", Code.EPISODE_STATE_CONFLICT)
            await self._fence(tx, scope, ep, lease, resource)
            entry = await self._entry(tx, scope, "CLEANUP_STARTED", row.entries, body)
            result = copy_update(row, status="CLEANUP_PENDING", entries=(*row.entries, entry))
            await tx.put(result)
            await self.authority._remember(tx, scope, "host.cleanup_started", row.id, body, result)
            return result

    async def authorize_recovery(
        self, scope: AuthorityScope, *, attempt_id: str
    ) -> RuntimeHostCreation:
        """Fence latest owned state, then return exact cleanup-only identity."""
        await self.cleanup_started(scope, attempt_id=attempt_id, reason="SUPERVISOR_RESTART")
        # Re-read current state; an idempotency receipt is never recovery authority.
        async with self.authority._transaction(scope) as tx:
            row = await self.authority._get(tx, scope, RuntimeHostCreation, attempt_id)
            ep, lease, resource = await self._context(tx, scope, row.episode_id)
            await self._configuration(tx, row, ep, lease, resource)
            require(
                row.status == "CLEANUP_PENDING"
                and resource.quarantined
                and lease.status != "ACTIVE"
                and resource.current_lease_id == lease.id
                and resource.generation == lease.generation,
                Code.LEASE_INVALID,
            )
            return row

    async def cleaned(
        self,
        scope: AuthorityScope,
        *,
        attempt_id: str,
        witness: CleanupWitness,
    ) -> RuntimeHostCreation:
        evidence = dict(
            container_id=witness.container_id,
            host_instance_id=witness.host_instance_id,
            episode_id=witness.episode_id,
            lease_original=_text(witness.lease_bytes, 4096),
            removed=witness.removed,
            stopped_inspection=_text(witness.stopped_inspection, 131072),
        )
        async with self.authority._transaction(scope) as tx:
            row = await self.authority._get(tx, scope, RuntimeHostCreation, attempt_id)
            ep, lease, resource = await self._context(tx, scope, row.episode_id)
            await self._configuration(tx, row, ep, lease, resource)
            previous = await self.authority._retry(
                tx, scope, "host.cleaned", row.id, evidence, RuntimeHostCreation
            )
            if previous:
                return previous
            require(
                row.status == "CLEANUP_PENDING"
                and resource.quarantined
                and resource.current_lease_id == lease.id
                and resource.generation == lease.generation
                and lease.status != "ACTIVE",
                Code.LEASE_INVALID,
            )
            require(
                witness.removed is True
                and (row.container_id is None or row.container_id == witness.container_id)
                and (witness.host_instance_id, witness.episode_id, witness.lease_bytes.decode())
                == (row.host_instance_id, row.episode_id, row.lease_original),
                Code.INVALID_CONTRACT,
            )
            require(self.verifier is not None, Code.ISOLATION_UNAVAILABLE)
            assert self.verifier is not None
            self.verifier.verify_cleaned(creation_attempt(row), witness)
            entry = await self._entry(tx, scope, "CLEANUP_CONFIRMED", row.entries, evidence)
            result = copy_update(
                row,
                status="CLEANED",
                container_id=witness.container_id,
                entries=(*row.entries, entry),
            )
            await tx.put(result)
            await self.authority._remember(tx, scope, "host.cleaned", row.id, evidence, result)
            return result

    async def unresolved(
        self, scope: AuthorityScope, *, after: str = "", limit: int = 25
    ) -> HostJournalPage:
        """Bounded scoped inventory only; caller must separately authorize recovery."""
        async with self.authority._transaction(scope) as tx:
            await self.authority._actor(tx, scope, service=True)
            rows = await tx.list_rows(
                RuntimeHostCreation,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                after=after,
                limit=limit,
            )
            return HostJournalPage(
                tuple(
                    r
                    for r in rows
                    if r.status != "CLEANED" and r.host_principal_id == scope.actor_id
                ),
                rows[-1].id if len(rows) == limit else None,
            )

    async def verify(
        self, tx: RuntimeTransaction, resource: RuntimeResource, lease: RuntimeLease
    ) -> None:
        """CleanupAuthority: accepted durable cleanup, never arbitrary absence proof."""
        row = await tx.get(RuntimeHostCreation, creation_identity(lease.id))
        require(row is not None, Code.ISOLATION_UNAVAILABLE)
        assert row is not None
        scope = AuthorityScope(
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            actor_id=resource.host_principal_id,
            idempotency_key="cleanup-read",
        )
        ep, _, _ = await self._context(tx, scope, row.episode_id)
        await self._configuration(tx, row, ep, lease, resource)
        require(
            row.status == "CLEANED"
            and row.container_id is not None
            and row.entries[-1].kind == "CLEANUP_CONFIRMED"
            and resource.current_lease_id == lease.id
            and resource.generation == lease.generation
            and resource.quarantined
            and lease.status != "ACTIVE",
            Code.ISOLATION_UNAVAILABLE,
        )


class HostJournalHooks:
    """Per-episode Docker hooks; each callback owns a fresh bounded transaction.

    Scope must come from trusted deployment composition, never caller headers.
    Docker bounds acknowledgement waits; cancellation never means DB rollback.
    """

    def __init__(self, journal: HostCreationJournal, scope: AuthorityScope):
        self.journal = journal
        self.scope = AuthorityScope.model_validate_json(canonical_json(scope))

    def _scope(
        self, operation: str, attempt: CreationAttempt, revision: int | None = None
    ) -> AuthorityScope:
        return self.scope.model_copy(
            update={
                "idempotency_key": content_hash(
                    [self.scope.idempotency_key, attempt.name, operation], exclude=()
                ),
                "expected_revision": revision,
            }
        )

    async def planned(self, attempt: CreationAttempt) -> bool:
        receipt = await self.journal.begin(self._scope("planned", attempt), attempt=attempt)
        return receipt.fresh

    async def created(self, attempt: CreationAttempt, witness: ContainerWitness) -> None:
        await self.journal.created(
            self._scope("created", attempt, 1),
            attempt_id=creation_identity(
                LeaseBinding.model_validate_json(attempt.lease_bytes).lease_id
            ),
            witness=witness,
        )

    async def cleanup_started(self, attempt: CreationAttempt) -> None:
        identity = creation_identity(LeaseBinding.model_validate_json(attempt.lease_bytes).lease_id)
        await self.journal.cleanup_started(
            self._scope("cleanup-started", attempt), attempt_id=identity
        )

    async def cleaned(self, attempt: CreationAttempt, witness: CleanupWitness) -> None:
        identity = creation_identity(LeaseBinding.model_validate_json(attempt.lease_bytes).lease_id)
        await self.journal.cleaned(
            self._scope("cleaned", attempt), attempt_id=identity, witness=witness
        )

    async def authorize_recovery(self, attempt: CreationAttempt) -> None:
        identity = creation_identity(LeaseBinding.model_validate_json(attempt.lease_bytes).lease_id)
        record = await self.journal.authorize_recovery(
            self._scope("recover", attempt), attempt_id=identity
        )
        require(creation_attempt(record) == attempt, Code.LEASE_INVALID)
