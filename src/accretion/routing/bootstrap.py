"""Application assembly for the opt-in, offline M2 baseline catalog."""

from __future__ import annotations

import platform

from accretion.contracts import Provider, Run, Task
from accretion.contracts.canonical import content_hash
from accretion.contracts.refs import EnvironmentRef
from accretion.contracts.routing import EnvironmentBinding
from accretion.resolver import CapabilityResolver
from accretion.routing.catalog import ConfigurationCatalog, ConfigurationCatalogFactory
from accretion.routing.protocols import FrozenNode
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot
from accretion.services.run_manager import RunManager


def build_node_routing(
    manager: RunManager, *, policy_id: str, granted_permissions: set[str]
) -> DefaultNodeRoutingService:
    """The shipped audited bundle is FAKE; unsupported live profiles require review.

    The environment digest describes the local interpreter/platform rather than
    pretending the worktree is a container image. An actual image-backed catalog can
    supply its own EnvironmentRef through the service's factory seam.
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
    )
