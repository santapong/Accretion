"""Content-bound configuration inputs for the deterministic M2 router.

The catalog is deliberately not a list of provider names followed by guessed defaults.  Every
entry is an immutable reference derived from a runtime health observation or from an object in
the capability, skill, or verifier registry.  A caller must supply model ids explicitly because
``AgentRuntime`` does not expose a model catalogue; treating a provider name as a model would put
an invented execution surface in a routing receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from accretion.contracts import (
    LIVE_PROVIDERS,
    AgentRuntime,
    CapabilityResolutionOutcome,
    MetaSkill,
    PrincipalRef,
    Provider,
    Run,
    Task,
)
from accretion.contracts.canonical import content_hash
from accretion.contracts.refs import RuntimeRef, SkillRef, ToolRef, VerifierRef
from accretion.contracts.routing import (
    EnvironmentBinding,
    ExecutionConfiguration,
    ModelBinding,
    NodeContract,
    ToolBinding,
    VerifierBinding,
)
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.snapshot import RoutingSnapshot
from accretion.verifiers.registry import VerifierRegistry

CATALOG_VERSION = "configuration-catalog/1"
FALLBACK_BUNDLE_VERSION = "fallback-bundle/1"
WORKSPACE_ROUTER_VERSION = "deterministic-router/1"

BEAM_WIDTH_BY_NODE_CLASS: Mapping[str, int] = {
    "AGENT": 8,
    "TOOL": 8,
    "VERIFIER": 4,
}
PARETO_EPSILON = 0.02


class CatalogError(ValueError):
    """The registered world cannot be represented without guessing."""


@dataclass(frozen=True, slots=True)
class RuntimeModelOption:
    """One observed runtime and one explicitly configured model for it."""

    runtime: RuntimeRef
    model: ModelBinding


@dataclass(frozen=True, slots=True)
class ToolCatalogEntry:
    """A tool binding plus the scopes observed on its selected connection."""

    binding: ToolBinding
    granted_scopes: tuple[str, ...] = ()
    connection_required: bool = False


@dataclass(frozen=True, slots=True)
class VerifierCatalogEntry:
    """One implementation registered in the trusted verifier registry."""

    verifier: VerifierRef
    version: str


@dataclass(frozen=True, slots=True)
class FallbackBundle:
    """Exact configurations audited as fallbacks, pinned by a content digest."""

    configurations: tuple[ExecutionConfiguration, ...] = ()
    version: str = FALLBACK_BUNDLE_VERSION
    digest: str = ""

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.configurations, key=lambda item: item.configuration_hash))
        if len({item.configuration_hash for item in ordered}) != len(ordered):
            raise CatalogError("fallback bundle repeats a configuration signature")
        computed = content_hash(
            {
                "version": self.version,
                "configurations": [item.model_dump(mode="python") for item in ordered],
            },
            exclude=(),
        )
        if self.digest and self.digest != computed:
            raise CatalogError("fallback bundle digest does not match its configurations")
        object.__setattr__(self, "configurations", ordered)
        object.__setattr__(self, "digest", computed)

    @property
    def configuration_hashes(self) -> frozenset[str]:
        return frozenset(item.configuration_hash for item in self.configurations)


@dataclass(frozen=True, slots=True)
class ConfigurationCatalog:
    """The exact building blocks candidate construction may combine."""

    runtime_models: tuple[RuntimeModelOption, ...]
    tools: tuple[ToolCatalogEntry, ...]
    skills: tuple[SkillRef, ...]
    verifiers: tuple[VerifierCatalogEntry, ...]
    environments: tuple[EnvironmentBinding, ...]
    fallback_bundle: FallbackBundle
    version: str = CATALOG_VERSION
    digest: str = ""

    def __post_init__(self) -> None:
        runtime_models = tuple(
            sorted(
                self.runtime_models,
                key=lambda item: (
                    item.runtime.runtime_id,
                    item.model.model_id,
                    item.runtime.adapter_version,
                ),
            )
        )
        tools = tuple(
            sorted(
                self.tools,
                key=lambda item: (
                    item.binding.capability.capability_id,
                    item.binding.binding_id,
                    item.binding.tool.implementation_digest,
                ),
            )
        )
        skills = tuple(sorted(self.skills, key=lambda item: (item.skill_id, item.version)))
        verifiers = tuple(
            sorted(
                self.verifiers,
                key=lambda item: (item.verifier.verifier_contract_id, item.version),
            )
        )
        environments = tuple(
            sorted(self.environments, key=lambda item: item.environment.environment_id)
        )
        runtime_keys = {
            (item.runtime.runtime_id, item.runtime.adapter_version, item.model.model_id)
            for item in runtime_models
        }
        tool_keys = {
            (item.binding.capability.capability_id, item.binding.binding_id) for item in tools
        }
        skill_keys = {(item.skill_id, item.version, item.package_digest) for item in skills}
        verifier_keys = {
            (item.verifier.verifier_contract_id, item.version, item.verifier.implementation_digest)
            for item in verifiers
        }
        for option in runtime_models:
            if option.runtime.provider is not option.model.provider:
                raise CatalogError("runtime/model catalog entry crosses providers")
            if option.runtime.model is not None and option.runtime.model != option.model.model_id:
                raise CatalogError("runtime/model catalog entry names two different models")
        for configuration in self.fallback_bundle.configurations:
            if (
                configuration.runtime.runtime_id,
                configuration.runtime.adapter_version,
                configuration.model.model_id,
            ) not in runtime_keys:
                raise CatalogError("fallback runtime/model is absent from its catalog")
            if configuration.environment not in environments:
                raise CatalogError("fallback environment is absent from its catalog")
            if any(
                (item.capability.capability_id, item.binding_id) not in tool_keys
                for item in configuration.tools
            ):
                raise CatalogError("fallback tool is absent from its catalog")
            if any(
                (item.skill_id, item.version, item.package_digest) not in skill_keys
                for item in configuration.skills
            ):
                raise CatalogError("fallback skill is absent from its catalog")
            verifier_key = (
                configuration.verifier.verifier.verifier_contract_id,
                configuration.verifier.version,
                configuration.verifier.verifier.implementation_digest,
            )
            if verifier_key not in verifier_keys:
                raise CatalogError("fallback verifier is absent from its catalog")
        computed = content_hash(
            {
                "version": self.version,
                "runtime_models": [
                    {"runtime": item.runtime, "model": item.model} for item in runtime_models
                ],
                "tools": [
                    {
                        "binding": item.binding,
                        "granted_scopes": item.granted_scopes,
                        "connection_required": item.connection_required,
                    }
                    for item in tools
                ],
                "skills": list(skills),
                "verifiers": [
                    {"verifier": item.verifier, "version": item.version}
                    for item in verifiers
                ],
                "environments": list(environments),
                "fallback_bundle_digest": self.fallback_bundle.digest,
            },
            exclude=(),
        )
        if self.digest and self.digest != computed:
            raise CatalogError("configuration catalog digest does not match its entries")
        object.__setattr__(self, "runtime_models", runtime_models)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "verifiers", verifiers)
        object.__setattr__(self, "environments", environments)
        object.__setattr__(self, "digest", computed)


def _skill_ref(skill: MetaSkill) -> SkillRef:
    return SkillRef(
        skill_id=skill.skill_id,
        version=skill.version,
        package_digest=content_hash(skill, exclude=()),
    )


class ConfigurationCatalogFactory:
    """Build a conservative catalog from the exact registries used by execution."""

    @classmethod
    async def build(
        cls,
        store: StateStore,
        runtimes: Mapping[Provider, AgentRuntime],
        verifiers: VerifierRegistry,
        *,
        run: Run,
        snapshot: RoutingSnapshot,
        environment: EnvironmentBinding,
        model_ids: Mapping[Provider, Sequence[str]],
        fallback_configurations: Sequence[ExecutionConfiguration] = (),
        allow_live_providers: bool = False,
    ) -> ConfigurationCatalog:
        """Observe configured runtimes and derive catalog refs without provider defaults.

        ``model_ids`` is mandatory authority/configuration data: a registered runtime with an
        empty entry yields no runtime-model option.  Live providers are omitted unless the
        deployment explicitly enables them; merely having a CLI installed is not authority to
        submit paid or externally visible work.
        """
        del run  # retained in the seam because callers already have it; constraints decide fit.
        runtime_options: list[RuntimeModelOption] = []
        for provider in sorted(model_ids, key=lambda item: item.value):
            if provider in LIVE_PROVIDERS and not allow_live_providers:
                continue
            runtime = runtimes.get(provider)
            configured_models = tuple(sorted({item for item in model_ids[provider] if item}))
            if runtime is None or not configured_models:
                continue
            health = await runtime.health()
            if health.provider is not provider:
                raise CatalogError("runtime health provider does not match its registry key")
            profile_digest = content_hash(
                {
                    "runtime_id": health.runtime_id,
                    "provider": health.provider,
                    "runtime_version": health.runtime_version,
                    "capabilities": sorted(health.capabilities),
                },
                exclude=(),
            )
            runtime_options.extend(
                [
                    RuntimeModelOption(
                        runtime=RuntimeRef(
                            runtime_id=health.runtime_id,
                            adapter_version=health.runtime_version,
                            provider=health.provider,
                            model=model_id,
                            capability_profile_digest=profile_digest,
                        ),
                        model=ModelBinding(model_id=model_id, provider=health.provider),
                    )
                    for model_id in configured_models
                ]
            )
        runtime_models = tuple(runtime_options)

        connections = {row.connection_id: row for row in await store.list_connections()}
        tool_entries: list[ToolCatalogEntry] = []
        for resolved in snapshot.capabilities:
            binding = resolved.binding
            if binding is None or not binding.enabled:
                continue
            tool_name = binding.backend.tool_name
            if not tool_name:
                # There is no normalized tool identity to put in ToolRef.  The safe answer is
                # not to bind it, rather than derive a plausible-looking name from a capability.
                continue
            connection_required = (
                resolved.outcome is not CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED
            )
            granted_scopes: tuple[str, ...] = ()
            if resolved.connection is not None:
                connection = connections.get(resolved.connection.connection_id)
                if connection is None:
                    continue
                granted_scopes = tuple(sorted(set(connection.granted_scopes)))
            implementation_digest = content_hash(
                {"capability": resolved.capability, "binding": binding}, exclude=()
            )
            tool_entries.append(
                ToolCatalogEntry(
                    binding=ToolBinding(
                        capability={
                            "capability_id": resolved.capability.capability_id,
                            "capability_version": resolved.capability.version,
                        },
                        tool=ToolRef(
                            tool_id=tool_name,
                            implementation_digest=implementation_digest,
                        ),
                        binding_id=binding.binding_id,
                        binding_version=binding.schema_version,
                    ),
                    granted_scopes=granted_scopes,
                    connection_required=connection_required,
                )
            )

        snapshot_skill_ids = set(snapshot.skills)
        skills = tuple(
            _skill_ref(skill)
            for skill in await store.list_skills()
            if skill.skill_id in snapshot_skill_ids
        )

        verifier_entries: list[VerifierCatalogEntry] = []
        for verifier_id in snapshot.verifier_ids:
            try:
                implementation = verifiers.get(verifier_id)
            except LookupError:
                continue
            version = implementation.verifier_version
            implementation_identity = (
                f"{type(implementation).__module__}.{type(implementation).__qualname__}"
            )
            verifier_entries.append(
                VerifierCatalogEntry(
                    verifier=VerifierRef(
                        verifier_contract_id=verifier_id,
                        implementation_digest=content_hash(
                            {
                                "verifier_id": verifier_id,
                                "version": version,
                                "implementation": implementation_identity,
                            },
                            exclude=(),
                        ),
                    ),
                    version=version,
                )
            )

        fallback = FallbackBundle(tuple(fallback_configurations))
        catalog = ConfigurationCatalog(
            runtime_models=runtime_models,
            tools=tuple(tool_entries),
            skills=skills,
            verifiers=tuple(verifier_entries),
            environments=(environment,),
            fallback_bundle=fallback,
        )
        cls._validate_fallbacks(catalog, snapshot)
        return catalog

    @staticmethod
    def _validate_fallbacks(
        catalog: ConfigurationCatalog, snapshot: RoutingSnapshot
    ) -> None:
        runtime_keys = {
            (item.runtime.runtime_id, item.runtime.adapter_version, item.model.model_id)
            for item in catalog.runtime_models
        }
        tools = {
            (item.binding.capability.capability_id, item.binding.binding_id)
            for item in catalog.tools
        }
        skills = {(item.skill_id, item.version, item.package_digest) for item in catalog.skills}
        verifiers = {
            (item.verifier.verifier_contract_id, item.version, item.verifier.implementation_digest)
            for item in catalog.verifiers
        }
        for configuration in catalog.fallback_bundle.configurations:
            if (
                configuration.runtime.runtime_id,
                configuration.runtime.adapter_version,
                configuration.model.model_id,
            ) not in runtime_keys:
                raise CatalogError("fallback runtime/model is absent from the observed catalog")
            if configuration.environment not in catalog.environments:
                raise CatalogError("fallback environment is absent from the observed catalog")
            if any(
                (tool.capability.capability_id, tool.binding_id) not in tools
                for tool in configuration.tools
            ):
                raise CatalogError("fallback tool is absent from the observed catalog")
            if any(
                (skill.skill_id, skill.version, skill.package_digest) not in skills
                for skill in configuration.skills
            ):
                raise CatalogError("fallback skill is absent from the observed catalog")
            verifier_key = (
                configuration.verifier.verifier.verifier_contract_id,
                configuration.verifier.version,
                configuration.verifier.verifier.implementation_digest,
            )
            if verifier_key not in verifiers:
                raise CatalogError("fallback verifier is absent from the observed catalog")
            health = snapshot.runtime(configuration.runtime.runtime_id)
            if health is None or health.runtime_version != configuration.runtime.adapter_version:
                raise CatalogError("fallback runtime is absent from the exact routing snapshot")

    @classmethod
    async def build_fake_baseline(
        cls,
        store: StateStore,
        runtimes: Mapping[Provider, AgentRuntime],
        verifiers: VerifierRegistry,
        *,
        run: Run,
        task: Task,
        node_contract: NodeContract,
        snapshot: RoutingSnapshot,
        environment: EnvironmentBinding,
        created_by: PrincipalRef,
    ) -> ConfigurationCatalog:
        """Build the audited local FakeRuntime fallback used by deterministic tests/runs.

        ``fake-model`` is the FakeRuntime contract's supported model identifier, not a default
        inferred from the provider enum.  All other components are still resolved from the
        actual snapshot and registries, and absence of any required component yields a catalog
        with no fallback instead of a made-up reference.
        """

        base = await cls.build(
            store,
            runtimes,
            verifiers,
            run=run,
            snapshot=snapshot,
            environment=environment,
            model_ids={Provider.FAKE: ("fake-model",)},
            fallback_configurations=(),
            allow_live_providers=False,
        )
        runtime_model = next(
            (item for item in base.runtime_models if item.runtime.provider is Provider.FAKE), None
        )
        tool_by_capability = {
            item.binding.capability.capability_id: item
            for item in base.tools
            if not item.connection_required or item.granted_scopes
        }
        skill_by_id = {item.skill_id: item for item in base.skills}
        verifier = base.verifiers[0] if base.verifiers else None
        required_tool_entries = [
            entry
            for requirement in node_contract.required_capabilities
            for entry in [tool_by_capability.get(requirement.capability.capability_id)]
            if entry is not None
            and (
                not entry.connection_required
                or requirement.required_scope in entry.granted_scopes
            )
        ]
        required_skills = [skill_by_id.get(skill_id) for skill_id in task.envelope.requested_skills]
        if (
            runtime_model is None
            or verifier is None
            or len(required_tool_entries) != len(node_contract.required_capabilities)
            or any(item is None for item in required_skills)
        ):
            return base
        fallback = ExecutionConfiguration(  # type: ignore[call-arg]
            contract_id=derived_id(
                "execution_configuration",
                "audited-fake-baseline",
                node_contract.immutable_hash,
                base.digest,
            ),
            created_at=node_contract.created_at,
            created_by=created_by,
            workspace_id=node_contract.workspace_id,
            project_id=node_contract.project_id,
            objective_contract_ref=node_contract.objective_contract_ref,
            labels={"fallback_bundle_version": FALLBACK_BUNDLE_VERSION},
            environment=environment,
            runtime=runtime_model.runtime,
            model=runtime_model.model,
            tools=[item.binding for item in required_tool_entries if item is not None],
            skills=[item for item in required_skills if item is not None],
            verifier=VerifierBinding(
                verifier=verifier.verifier,
                version=verifier.version,
                verification_spec_hash=node_contract.verification_spec_ref.content_hash,
            ),
        )
        result = ConfigurationCatalog(
            runtime_models=base.runtime_models,
            tools=base.tools,
            skills=base.skills,
            verifiers=base.verifiers,
            environments=base.environments,
            fallback_bundle=FallbackBundle((fallback,)),
        )
        cls._validate_fallbacks(result, snapshot)
        return result


__all__ = [
    "BEAM_WIDTH_BY_NODE_CLASS",
    "CATALOG_VERSION",
    "FALLBACK_BUNDLE_VERSION",
    "PARETO_EPSILON",
    "WORKSPACE_ROUTER_VERSION",
    "CatalogError",
    "ConfigurationCatalog",
    "ConfigurationCatalogFactory",
    "FallbackBundle",
    "RuntimeModelOption",
    "ToolCatalogEntry",
    "VerifierCatalogEntry",
]
