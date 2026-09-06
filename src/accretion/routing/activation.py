"""The activation ledger: "active" as the head of a sequence (SDD §7.14, §10.3, ADR-061).

M0 read §13.1's "one active workspace router per workspace" as a partial unique index over
``router_model_versions.status``. The rule is right and that implementation of it is
unreachable: the v0.4 contract family has no ``update_`` method on any table, by design, so
the first ``ACTIVE`` row can never be retired and, with the index in place, a second can
never be inserted. A workspace was activatable exactly once, forever.

This module is the replacement. ``RouterActivation`` rows form an append-only ledger, one
sequence per ``(workspace_id, scope, family_key)``, and the active version of a family is
the ``router_version_id`` of the entry with the greatest ``sequence``. Promotion appends;
rollback appends; nothing is edited; "who activated what, when, and why" is the table rather
than a reconstruction from timestamps and statuses.

Two objects, and they face opposite directions. :class:`ActivationLedger` is the *write*
side — it works out the next sequence and the version it displaces, and hands the composite
write to the store, which is the only place that can make the version rows and the ledger
entry one act. :class:`LedgerActiveVersionResolver` is the *read* side, and it is the answer
to "which router is serving this project right now" that :mod:`accretion.routing.bootstrap`
hands to the routing service as its
:class:`~accretion.routing.stages.ActiveVersionResolver`.

Neither knows anything about promotion policy. Whether a candidate has *earned* activation
is :mod:`accretion.routing.promotion`'s question; this module records the answer.
"""

from __future__ import annotations

from collections.abc import Mapping

from accretion.contracts import PrincipalRef
from accretion.contracts.routing import (
    RouterActivation,
    RouterActivationKind,
    RouterModelVersion,
    RouterScope,
)
from accretion.ids import new_id
from accretion.persistence.store import StateStore
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.stages import ActiveVersions
from accretion.routing.train import ALGORITHM_ID

WORKSPACE_FAMILY_KEY = "workspace"
"""The family key every ``TEAM_WORKSPACE`` activation shares.

A workspace prior is one family by definition — there is no second axis to partition it on —
so the key is a constant rather than the algorithm id. Writing the algorithm id here instead
would have made a workspace's ledger restart from sequence 1 the first time the training
algorithm changed, and "active" would then have had two heads with equal claim.
"""


def adapter_family_key(project_id: str, algorithm_id: str) -> str:
    """The family key of a project adapter: the project and the algorithm that fitted it.

    §7.12 calls ``algorithm_id`` the router family, and two adapters fitted by different
    algorithms for the same project are a comparison rather than a conflict — the same
    reading M0's ``uq_router_versions_active_project_adapter`` had of §13.1's fourth bullet.
    Each such pair is therefore its own sequence, and promoting one leaves the other's head
    where it was.
    """

    return f"{project_id}:{algorithm_id}"


def family_key_for(version: RouterModelVersion) -> str:
    """The partition a version belongs to, read off the version itself.

    Derived rather than passed in, because a caller that could name the family separately
    from the version could append an entry to one family that activates a version belonging
    to another, and the ledger would then be contiguous, unique, fully constrained and
    wrong.
    """

    if version.scope is RouterScope.TEAM_WORKSPACE:
        return WORKSPACE_FAMILY_KEY
    if version.project_id is None:  # pragma: no cover - the contract forbids it
        raise ValueError(
            f"router version {version.contract_id} is a PROJECT_ADAPTER and names no "
            "project; it has no adapter family to be activated in"
        )
    return adapter_family_key(version.project_id, version.algorithm_id)


class ActivationLedger:
    """Append one entry, having worked out what it must say to be readable as history.

    The caller supplies the act — a kind, the version rows it writes, who approved it and
    why. The ledger supplies the two fields that are not the caller's to choose: the
    ``sequence``, which is one past the current head, and the ``previous_version_id``, which
    is the head's ``router_version_id`` and not any of the rows being written. Getting the
    second of those from the caller was the first draft and it was wrong in a way nothing
    would have caught: :class:`~accretion.routing.promotion.PromotionService` mints a *new*
    ``RETIRED`` row to record the displaced head, and that row's fresh ``rmv_`` id is not the
    id of the version that was serving. A ledger whose ``previous_version_id`` named the
    tombstone rather than the version would chain to a record that was never active.

    Both fields are computed and then re-checked by the store's contiguity guard against the
    state inside the write transaction, because between this read and that write another
    promotion may have landed. This is the optimistic half; the guard is the authoritative
    half, and they are deliberately not the same code.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def bind(self, store: StateStore) -> ActivationLedger:
        """The same ledger against a transaction-scoped store.

        :meth:`activate` reads the head and writes the entry, and those two must see one
        transaction. A service that holds an injected ledger built over the *outer* store
        and then opens ``activation_transaction`` would otherwise write through the outer
        store from inside the scope, which on ``MemoryStore`` publishes immediately and on
        ``PostgresStore`` deadlocks against the advisory lock the scope is holding. Binding
        is one line and it keeps the collaborator injectable.
        """

        return ActivationLedger(store)

    async def head(
        self, *, workspace_id: str, scope: RouterScope, family_key: str
    ) -> RouterActivation | None:
        """The entry now serving for one family, or ``None`` before the first promotion."""

        return await self.store.head_router_activation(
            workspace_id=workspace_id, scope=scope, family_key=family_key
        )

    async def activate(
        self,
        *,
        kind: RouterActivationKind,
        version: RouterModelVersion,
        previous: RouterModelVersion | None = None,
        rollback_target: str | None = None,
        promotion_report_id: str | None = None,
        approved_by: PrincipalRef,
        cause: str | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> RouterActivation:
        """Write ``version``, ``previous`` and the entry that names ``version``, atomically.

        ``version`` is the row that becomes the head; ``previous`` is the row recording what
        the head displaced — a ``RETIRED`` tombstone after a promotion, a ``ROLLED_BACK`` one
        after a withdrawal — and is absent only for the first activation of a family.

        ``approved_by`` is both the entry's ``approved_by`` and its header ``created_by``.
        §10.3 makes activation a human act and OQ-411 requires the approver on **every**
        entry, rollbacks included; attributing the row's creation to a service identity while
        its approval named a person would put two different answers to "who did this" in one
        record.
        """

        family_key = family_key_for(version)
        current = await self.head(
            workspace_id=version.workspace_id,
            scope=version.scope,
            family_key=family_key,
        )
        # Built through ``model_validate`` for the reason ``SnapshotBuilder.build`` gives:
        # the pydantic mypy plugin does not carry ``CanonicalContract``'s header fields onto
        # a subclass declared in another module, and the blanket ignore the keyword
        # constructor would need would also hide a misspelled field.
        activation = RouterActivation.model_validate(
            {
                "contract_id": new_id("router_activation"),
                "created_by": approved_by,
                "workspace_id": version.workspace_id,
                "project_id": version.project_id,
                "scope": version.scope,
                "family_key": family_key,
                "sequence": 1 if current is None else current.sequence + 1,
                "kind": kind,
                "router_version_id": version.contract_id,
                "previous_version_id": (
                    None if current is None else current.router_version_id
                ),
                "rollback_target_version_id": rollback_target,
                "promotion_report_id": promotion_report_id,
                "approved_by": approved_by,
                "cause": cause,
                "labels": dict(labels or {}),
            }
        )
        versions = [version] if previous is None else [version, previous]
        return await self.store.activate_router_version(
            activation=activation, versions=versions
        )


class LedgerActiveVersionResolver:
    """Read the two heads a routing decision has to name, in two queries and no scans.

    "Which router is serving?" is now a question about the ledger and not about
    ``router_model_versions.status``, and it has to stay that cheap: it is asked once per
    routing request. Both lookups are single-row reads on the partition key, which is what
    ``head_router_activation`` is indexed for.

    ``algorithm_id`` is a constructor argument defaulted to the one algorithm this repository
    trains, because the adapter family key is ``(project, algorithm)`` and a resolver cannot
    read the algorithm off a project. When a second algorithm ships, the caller that knows
    which family it wants passes it here rather than this class guessing.
    """

    def __init__(self, store: StateStore, algorithm_id: str = ALGORITHM_ID) -> None:
        self.store = store
        self.algorithm_id = algorithm_id

    async def resolve(
        self, *, workspace_id: str, project_id: str | None = None
    ) -> ActiveVersions:
        """The active workspace router and project adapter, as ids and as labels.

        Returns :class:`~accretion.routing.stages.ActiveVersions` — §9.4's own type and not a
        local mirror of it — because this class *is* the
        :class:`~accretion.routing.stages.ActiveVersionResolver` the routing service holds.
        M8.1 declared the four fields locally because ``stages.py`` landed in the same window;
        keeping the duplicate now would leave two structurally identical dataclasses that a
        reader has to compare field by field to know are interchangeable, and one of them
        would eventually gain a fifth field.

        ``router_label`` is never ``None`` and ``adapter_label`` is ``None`` exactly when no
        adapter is active: a workspace that has promoted nothing is routed by the audited
        deterministic baseline, which is a state with a name
        (:data:`~accretion.routing.catalog.WORKSPACE_ROUTER_VERSION`) rather than an absence,
        and there is no deterministic *adapter* for the cold-start case to fall back to.
        """

        workspace_head = await self.store.head_router_activation(
            workspace_id=workspace_id,
            scope=RouterScope.TEAM_WORKSPACE,
            family_key=WORKSPACE_FAMILY_KEY,
        )
        adapter_head: RouterActivation | None = None
        if project_id is not None:
            adapter_head = await self.store.head_router_activation(
                workspace_id=workspace_id,
                scope=RouterScope.PROJECT_ADAPTER,
                family_key=adapter_family_key(project_id, self.algorithm_id),
            )
        router_version_id = (
            None if workspace_head is None else workspace_head.router_version_id
        )
        adapter_version_id = (
            None if adapter_head is None else adapter_head.router_version_id
        )
        return ActiveVersions(
            router_version_id=router_version_id,
            adapter_version_id=adapter_version_id,
            router_label=(
                WORKSPACE_ROUTER_VERSION
                if router_version_id is None
                else router_version_id
            ),
            adapter_label=adapter_version_id,
        )


__all__ = [
    "WORKSPACE_FAMILY_KEY",
    "ActivationLedger",
    "LedgerActiveVersionResolver",
    "adapter_family_key",
    "family_key_for",
]
