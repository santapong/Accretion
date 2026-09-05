"""Deterministic identity for a routing attempt (SDD §8.2, §8.3, §7.3).

Everything in this module answers the same question in a different place: *what is this, and
would two people computing it independently agree?* SDD §8.2 requires repeated routing
requests with identical immutable inputs to return the same receipt, which is only possible
if the request has the same id both times. §8.3 requires a decision to be made against an
exact snapshot, which is only checkable if the snapshot's identity is inside the request's.
ADR-041 makes one graph-node execution instance one routable action, which is only true if
two attempts at one node have different ids and two computations of one attempt have the
same one.

So no identity here is minted. Every one of them is derived — from the run, the node, the
attempt, the snapshot, the policy — with :func:`accretion.ids.derived_id`, which keeps
:func:`accretion.ids.new_id`'s prefix and width so that ``has_prefix`` and every
``CanonicalContract.ID_KIND`` check keep working, and replaces the timestamp-and-randomness
body with a digest over the parts.

M2 is the first milestone that persists a node execution instance.  It therefore has a
dedicated ``exe_`` identity rather than borrowing the ``run_`` namespace used by the M1
placeholder.  The digest remains a pure function of run, logical node key and attempt, so
reconstructing an interrupted attempt restores the same identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from accretion.contracts import (
    AcceptancePolicy,
    EvidenceClass,
    PrincipalRef,
    PrincipalStatus,
    Run,
    Task,
)
from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import (
    Claim,
    Criticality,
    MetricOperator,
    MetricThreshold,
    VerificationSpec,
    VerificationState,
)
from accretion.identity import LOCAL_WORKSPACE_ID
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.protocols import RoutingMode
from accretion.routing.snapshot import RoutingSnapshot

EXECUTION_INSTANCE_DOMAIN = "execution-instance/v1"
"""Versioned domain separator for deterministic node-execution identities."""


def execution_instance_id(run_id: str, node_key: str, attempt: int) -> str:
    """The identity of one attempt at one node of one run.

    Three parts and no more. The run scopes it; the node key names which node, and is the
    *key* rather than the ``node_id`` because a re-planned graph revision mints new node ids
    for the same logical node and an attempt at "review" should stay recognisable as the
    same node across revisions. The attempt separates retries, because ADR-041 makes a retry
    a different routable action: it gets its own receipt, its own experience record and its
    own attribution, and reusing the first attempt's identity would credit the retry's
    outcome to the configuration that failed.

    ``attempt`` is stringified rather than passed through, because
    :func:`~accretion.ids.derived_id` digests the canonical JSON of a list of strings and
    ``1`` and ``"1"`` would otherwise be two identities for one attempt.
    """

    if attempt < 1:
        raise ValueError(
            f"attempt {attempt} is not a valid attempt number; attempts are counted from 1, "
            "and an attempt zero would give the first try the identity of a try that never "
            "happened"
        )
    return derived_id(
        "execution_instance", EXECUTION_INSTANCE_DOMAIN, run_id, node_key, str(attempt)
    )


def routing_request_id(
    node_contract_hash: str,
    snapshot: RoutingSnapshot,
    workspace_router_version: str,
    project_adapter_version: str | None,
    mode: RoutingMode,
) -> str:
    """The identity SDD §8.2 makes a receipt answer, derived from the immutable inputs.

    §8.2's guarantee is that *identical immutable inputs return the same receipt*, so this id
    has to cover exactly the inputs that make two requests identical and nothing else. Seven
    parts:

    * ``node_contract_hash`` — what is being routed. The contract's ``immutable_hash``, not
      its header ``content_hash``, for the reason ``NodeContract`` gives: the immutable hash
      is the value other contracts pin and is stable under later header additions.
    * the four snapshot ids — what the world was (§8.3). All four, not the registry digest
      alone: the same node routed under a different policy, against different connections or
      against different runtimes is a different request, and a request id that covered only
      the registry would return a stale receipt for it. Dropping ``policy_snapshot_id`` in
      particular is the failure this module's tests are written to catch, because it is the
      one that is a label rather than a digest and therefore the one it is easiest to leave
      out by accident.
    * ``workspace_router_version`` and ``project_adapter_version`` — *who* is routing. §7.12
      versions a router model and §7.14 versions a project adapter; a request answered by a
      newer router is a different request even when everything else is equal, and §10.2's
      promotion evaluation depends on being able to tell the two apart.
    * ``mode`` — under which of §11.1's three regimes. A ``SHADOW`` decision and an ``AUTO``
      decision over identical inputs are deliberately different records: one was executed and
      one was not.

    ``project_adapter_version`` is nullable — a project with no adapter is the cold-start case
    §9.8 is about — and the absent case is spelled as a fixed sentinel inside the digest
    rather than as an empty string, so that "no adapter" and "an adapter named the empty
    string" cannot derive one id.
    """

    return derived_id(
        "routing_request",
        node_contract_hash,
        snapshot.capability_registry_snapshot_id,
        snapshot.available_runtime_snapshot_id,
        snapshot.connection_availability_snapshot_id,
        snapshot.policy_snapshot_id,
        workspace_router_version,
        project_adapter_version if project_adapter_version is not None else "\x00no-adapter",
        mode.value,
    )


async def workspace_for_run(store: StateStore, run: Run) -> str:
    """The workspace a routing decision for ``run`` is filed under (ADR4-M1-001).

    **Why this is derived rather than read.** A ``CompatibilityDecision`` — like every
    registry §3 record — is workspace-scoped, and a run is not: ``Run`` carries a
    ``project_id`` and a ``principal_id``, and ``Project`` has no workspace column at all. So
    the workspace has to come from somewhere, and there are only two honest candidates: the
    principal's memberships, or the local default.

    The first workspace of ``list_workspaces_for_principal`` is taken, and that method sorts,
    so the answer is deterministic for a principal in several workspaces rather than being
    whichever row the database returned first. It is deliberately not "the workspace the
    project belongs to", because no such column exists and adding one would be a migration
    this milestone has no business making; and it is deliberately not an error for a
    principal in more than one, because a single-workspace deployment — which is every local
    one — would then be the only configuration that worked.

    ``LOCAL_WORKSPACE_ID`` is the fallback for a run with no principal or no membership,
    which is exactly what the identity service seeds for single-user local operation
    (OQ3-17). Falling back to it means a local run routes into the same workspace its
    principal would have been given, rather than into one that does not exist.

    ADR4-M1-001 records this: the derivation is a v0.4 decision, not an accident, and M2's
    persistence should be read with it in mind.
    """

    if run.principal_id:
        workspaces = await store.list_workspaces_for_principal(run.principal_id)
        if workspaces:
            return workspaces[0].workspace_id
    return LOCAL_WORKSPACE_ID


def principal_ref_for_run(run: Run) -> PrincipalRef:
    """The principal a routing decision for ``run`` is created by.

    Raises when the run names no principal. That is the same refusal
    ``CompatibilityEngine.__init__`` makes by requiring ``created_by``: every decision is a
    :class:`~accretion.contracts.canonical.CanonicalContract` and carries an author, and
    substituting a plausible-looking id would put an identity in the audit trail that no
    principal row backs. A run reaching v0.4 routing always has one — the identity service
    seeds a local principal even with no identity provider — so this raises on a wiring bug
    rather than on a supported configuration.

    ``status`` is ``ACTIVE`` and is not read from the store, because this function is
    deliberately synchronous and has only the run. That is safe in one direction and checked
    in the other: the reference is only ever the *author* of a record, and the question "may
    this principal act" is asked by
    :meth:`accretion.routing.gates.PolicyGate.gate_permissions`, which is handed the
    principal reference a caller resolved from the store and refuses a non-``ACTIVE`` one.
    Authority is never taken from this value.
    """

    if not run.principal_id:
        raise ValueError(
            f"run {run.run_id!r} names no principal, so a routing decision for it would "
            "carry an author no principal row backs; routing without an identity is "
            "routing without authority"
        )
    return PrincipalRef(principal_id=run.principal_id, status=PrincipalStatus.ACTIVE)


class VerificationSpecBuilder:
    """Builds the §7.3 verification spec a node is frozen against, idempotently.

    ADR-044 freezes verification semantics *before* routing, which means the spec must exist
    at freeze time and must be the same document every time the same node is frozen. Both
    halves are load-bearing. If the spec were built after selection, a router could choose a
    configuration and then choose what counts as success for it. If two freezes of one node
    produced two documents, the node contract's ``verification_spec_ref.content_hash`` would
    move, the node's own ``immutable_hash`` would move with it, and §8.2's "identical
    immutable inputs return the same receipt" would be false for a node nobody edited.

    Idempotence is arithmetic rather than convention. ``contract_id`` is derived from a
    digest of the spec's own body, so a re-put of an unchanged spec is a put of the same id
    with the same payload — which the append-only store accepts as a no-op — while a changed
    spec gets a different id and becomes a second row rather than a silent overwrite.
    ``created_at`` is taken from the task rather than from a clock for the same reason: the
    header digest covers it, so a wall clock would make two builds of one spec differ in
    ``content_hash`` while agreeing on everything that matters.
    """

    def __init__(self, *, created_by: PrincipalRef, workspace_id: str) -> None:
        self.created_by = created_by
        self.workspace_id = workspace_id

    def build(self, task: Task, policy: AcceptancePolicy) -> VerificationSpec:
        """One REQUIRED claim per required verifier and per required output.

        Both sources are ``REQUIRED`` rather than ``SUPPORTING``, and the reason is what
        ``AcceptancePolicy`` already means: ``required_verifiers`` is the list that
        ``all_required_must_pass`` is about, and a required output the run did not produce is
        a run that did not do what it was asked. A ``SUPPORTING`` claim cannot block
        acceptance, so grading either of these as supporting would turn the spec into a
        report — which is exactly what ``VerificationSpec`` refuses to be built as.

        ``score_thresholds`` becomes the spec's metrics. Dropping them would freeze a spec
        that ignores half of the policy it was built from, and a node could then pass every
        claim while scoring below the floor its policy set.

        Raises when the policy names no verifier and the task requires no output: a node with
        nothing to verify cannot be routed, because ADR-044's freeze would be a freeze of
        nothing and every configuration would be trivially acceptable.
        """

        claims: list[Claim] = []
        seen: set[str] = set()
        for verifier_id in policy.required_verifiers:
            claim_id = _claim_id("verifier", verifier_id)
            if claim_id in seen:
                # A policy that names one verifier twice is not two requirements; the spec
                # itself refuses a repeated claim_id, and de-duplicating here is kinder than
                # a validation error about a field the caller never wrote.
                continue
            seen.add(claim_id)
            claims.append(
                Claim(
                    claim_id=claim_id,
                    description=(
                        f"verifier {verifier_id} accepts this node's work under acceptance "
                        f"policy {policy.policy_id}"
                    ),
                    criticality=Criticality.REQUIRED,
                    required_evidence_types=[EvidenceClass.DIGITAL],
                )
            )
        for index, requirement in enumerate(task.envelope.required_outputs):
            claim_id = _claim_id("output", _output_key(index, requirement))
            if claim_id in seen:
                continue
            seen.add(claim_id)
            claims.append(
                Claim(
                    claim_id=claim_id,
                    description=(
                        f"the required output {_output_key(index, requirement)!r} declared "
                        f"by task {task.envelope.task_id} is present and well formed"
                    ),
                    criticality=Criticality.REQUIRED,
                    required_evidence_types=[EvidenceClass.DIGITAL],
                )
            )
        if not claims:
            raise ValueError(
                f"acceptance policy {policy.policy_id!r} names no required verifier and task "
                f"{task.envelope.task_id!r} requires no output, so there is nothing to "
                "verify; a node with no verifiable claim may not be routed (ADR-044)"
            )

        metrics = [
            MetricThreshold(
                metric_id=metric_id,
                operator=MetricOperator.GTE,
                threshold=threshold,
            )
            # Sorted, because `score_thresholds` is a dict and its iteration order is its
            # insertion order: two policies with the same thresholds written in a different
            # order would otherwise freeze two specs with two digests.
            for metric_id, threshold in sorted(policy.score_thresholds.items())
        ]

        body: Mapping[str, Any] = {
            "workspace_id": self.workspace_id,
            "project_id": task.envelope.project_id,
            "revision": 1,
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "metrics": [metric.model_dump(mode="json") for metric in metrics],
            "accepted_outcomes": [VerificationState.PASS.value],
        }
        digest = hashlib.sha256(canonical_json(body)).hexdigest()
        # `type: ignore[call-arg]` covers the registry §3 header fields only; see
        # `CompatibilityEngine._decide` for why the pydantic mypy plugin cannot see them.
        return VerificationSpec(  # type: ignore[call-arg]
            contract_id=derived_id("verification_spec", digest),
            created_at=task.created_at,
            created_by=self.created_by,
            workspace_id=self.workspace_id,
            project_id=task.envelope.project_id,
            revision=1,
            claims=claims,
            metrics=metrics,
            accepted_outcomes=[VerificationState.PASS],
        )


def _output_key(index: int, requirement: Mapping[str, Any]) -> str:
    """A stable name for one entry of ``TaskEnvelope.required_outputs``.

    ``required_outputs`` is a list of free-form dicts, and the repository's own writers put
    the identifying value under ``path`` (``OutputContractVerifier`` reads it there). When it
    is absent the position is used, which is stable for a task envelope that is itself
    immutable and is honest about being positional.
    """

    path = requirement.get("path")
    if isinstance(path, str) and path:
        return path
    return f"#{index}"


def _claim_id(prefix: str, name: str) -> str:
    """``prefix.name``, digested down when the name would overflow the 64-character field.

    ``Claim.claim_id`` is capped at 64 characters and a path can be longer than that.
    Truncating the name would let two different outputs share a claim, which the spec would
    then reject as a repeated ``claim_id`` — a confusing error about a field nobody wrote —
    so an over-long name is replaced by a digest of itself, which stays unique and stays
    deterministic.
    """

    candidate = f"{prefix}.{name}"
    if len(candidate) <= 64:
        return candidate
    room = 64 - len(prefix) - 1
    return f"{prefix}.{hashlib.sha256(name.encode('utf-8')).hexdigest()[:room]}"


__all__ = [
    "EXECUTION_INSTANCE_DOMAIN",
    "VerificationSpecBuilder",
    "execution_instance_id",
    "principal_ref_for_run",
    "routing_request_id",
    "workspace_for_run",
]
