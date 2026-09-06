"""Application assembly for the opt-in, offline baseline catalog and the §9.4 stages."""

from __future__ import annotations

import platform
from datetime import UTC, datetime

from accretion.contracts import Provider, Run, Task
from accretion.contracts.canonical import content_hash
from accretion.contracts.refs import EnvironmentRef
from accretion.contracts.routing import EnvironmentBinding
from accretion.feedback.evidence import StoreEvidenceRetriever
from accretion.resolver import CapabilityResolver
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.catalog import ConfigurationCatalog, ConfigurationCatalogFactory
from accretion.routing.coldstart import ColdStartScorer
from accretion.routing.protocols import FrozenNode, RoutingMode
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot
from accretion.routing.stages import (
    CandidateScorer,
    DeterministicBehavior,
    StatusActiveVersionResolver,
)
from accretion.routing.train import LearnedPredictorLoader
from accretion.services.run_manager import RunManager


def _utc_now() -> datetime:
    """The scorer's ``as_of``. A named function so a test can substitute a frozen one."""

    return datetime.now(UTC)


def build_node_routing(
    manager: RunManager,
    *,
    policy_id: str,
    granted_permissions: set[str],
    mode: RoutingMode = RoutingMode.BASELINE_ONLY,
    artifacts: ArtifactStore | None = None,
) -> DefaultNodeRoutingService:
    """The shipped audited bundle is FAKE; unsupported live profiles require review.

    The environment digest describes the local interpreter/platform rather than
    pretending the worktree is a container image. An actual image-backed catalog can
    supply its own EnvironmentRef through the service's factory seam.

    ``mode`` decides whether a learned scorer is built at all, and that is the whole of the
    §11.1 gate at assembly time: under ``BASELINE_ONLY`` the scorer is ``None``, so the
    service refuses ``AUTO`` and ``SHADOW`` for every caller regardless of what a request
    body says. Constructing the scorer and then declining to use it would leave a loaded
    predictor one attribute away from a decision nobody authorised.
    """
    environment = EnvironmentBinding(
        environment=EnvironmentRef(
            environment_id="local-worktree",
            image_digest=content_hash(
                {
                    "kind": "local-process",
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python": platform.python_version(),
                },
                exclude=(),
            ),
            policy_profile=policy_id,
        ),
        workspace_isolation="WORKTREE",
    )

    async def catalog(
        frozen: FrozenNode, snapshot: RoutingSnapshot, run: Run, task: Task
    ) -> ConfigurationCatalog:
        if run.provider == Provider.FAKE:
            return await ConfigurationCatalogFactory.build_fake_baseline(
                manager.store,
                manager.runtimes,
                manager.verifiers,
                run=run,
                task=task,
                node_contract=frozen.node_contract,
                snapshot=snapshot,
                environment=environment,
                created_by=frozen.node_contract.created_by,
            )
        return await ConfigurationCatalogFactory.build(
            manager.store,
            manager.runtimes,
            manager.verifiers,
            run=run,
            snapshot=snapshot,
            environment=environment,
            model_ids={},
        )

    artifact_store = artifacts or ArtifactStore.default()
    scorer: CandidateScorer | None = None
    if mode is not RoutingMode.BASELINE_ONLY:
        scorer = ColdStartScorer(
            LearnedPredictorLoader(manager.store, artifact_store),
            artifact_store,
            _utc_now,
        )

    return DefaultNodeRoutingService(
        store=manager.store,
        snapshots=RegistrySnapshotBuilder(
            manager.store,
            CapabilityResolver(manager.store),
            manager.runtimes,
            manager.verifiers,
            policy_id=policy_id,
        ),
        catalog_factory=catalog,
        runtimes=manager.runtimes,
        granted_permissions=granted_permissions,
        active_versions=StatusActiveVersionResolver(manager.store),
        evidence=StoreEvidenceRetriever(manager.store),
        scorer=scorer,
        behavior=DeterministicBehavior(),
        default_mode=mode,
    )
