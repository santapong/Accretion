"""v0.4.1 C: the shipped selector optimises the utility the benchmark measured.

Pre-registration item 3 froze the corpus weights 1.0 / 0.3 / 0.15 and named the discrepancy
it would not smuggle a fix into: the M2 objective minter shipped 1.0 / 0.25 / 0.15, so every
number M10 reported described a policy nobody ran. ADR4.1-002 reconciles the two, and the two
claims here are what keep them reconciled.

**The default is the registered vector.** ``DEFAULT_UTILITY_WEIGHTS`` equals the literal
1.0 / 0.3 / 0.15 *and* the ``weights`` block of every corpus registered under ``evals/router``
— the shipped one, the locked test set and the drift replica. Pinning the literal alone would
let a corpus be re-tuned to whatever the code became; comparing to the corpora alone would let
both move together. Asserting both, against all three roots, means any single edit is red.

**The minter mints that object rather than restating it.** ``routing/freeze.py`` imports the
constant, so the two sites are equal by construction and not by a second literal somebody has
to remember to update. The minted contract still carries its *own* copy: an objective contract
may declare weights that differ from the default, and a caller editing a persisted record must
never rewrite a process-wide constant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from accretion.contracts import (
    AcceptancePolicy,
    PrincipalRef,
    PrincipalStatus,
    Project,
    RiskLevel,
    Task,
    TaskEnvelope,
)
from accretion.contracts.routing import UtilityWeights
from accretion.persistence.store import MemoryStore
from accretion.router_benchmark import CORPUS_ROOT, RouterBenchmarkCorpus
from accretion.routing.freeze import ObjectiveContractMinter
from accretion.routing.selector import DEFAULT_UTILITY_WEIGHTS

FIXED_TIME = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
TASK_ID = "tsk_01K4DQ9HVJXBQBN3YF83E5Y9TC"
PRINCIPAL = PrincipalRef(
    principal_id="usr_01K4DQ9HVJXBQBN3YF83E5Y9TD",
    display_name="v0.4.1 selector weights",
    status=PrincipalStatus.ACTIVE,
)

REGISTERED_CORPUS_ROOTS: tuple[Path, ...] = (
    CORPUS_ROOT,
    CORPUS_ROOT / "locked",
    CORPUS_ROOT / "drift",
)
"""Every corpus whose numbers are quoted anywhere: shipped, locked test set, drift replica."""

PREREGISTERED_WEIGHTS = UtilityWeights(quality=1.0, cost=0.3, latency=0.15)
"""Pre-registration item 3, restated as a literal on purpose: this is the frozen claim."""


def task_row() -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id=TASK_ID,
            project_id=PROJECT_ID,
            objective="mint the objective contract the selector will optimise",
            allowed_capabilities=["cap.repo.read"],
            risk_level=RiskLevel.MEDIUM,
            required_outputs=[{"path": "result.json"}],
        ),
        created_at=FIXED_TIME,
    )


def policy_row() -> AcceptancePolicy:
    return AcceptancePolicy(
        policy_id="acceptance-v041-selector-weights",
        required_verifiers=["output-contract"],
        score_thresholds={"quality": 0.5},
        created_at=FIXED_TIME,
    )


async def setup_minter() -> tuple[MemoryStore, ObjectiveContractMinter]:
    """A minter over a fresh store, so the contract read back is the one just minted."""

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4.1 selector weights",
            repository_path=Path("/tmp/accretion-v041-selector-weights"),
            created_at=FIXED_TIME,
        )
    )
    minter = ObjectiveContractMinter(
        store=store, created_by=PRINCIPAL, workspace_id=WORKSPACE_ID
    )
    return store, minter


def test_the_selector_default_is_the_weight_vector_every_registered_corpus_declares() -> None:
    assert DEFAULT_UTILITY_WEIGHTS == PREREGISTERED_WEIGHTS

    for root in REGISTERED_CORPUS_ROOTS:
        corpus = RouterBenchmarkCorpus.load(root)
        assert corpus.config.weights == DEFAULT_UTILITY_WEIGHTS, (
            f"the corpus at {root.name} scores a utility the shipped selector does not optimise"
        )


async def test_the_minted_objective_contract_carries_the_selector_default() -> None:
    store, minter = await setup_minter()

    await minter.for_task(task_row(), policy_row())

    contracts = await store.list_objective_contracts(workspace_id=WORKSPACE_ID)
    assert len(contracts) == 1
    minted = contracts[0].utility_weights
    assert minted == DEFAULT_UTILITY_WEIGHTS
    assert minted.model_dump() == {"quality": 1.0, "cost": 0.3, "latency": 0.15}
    # A persisted record owns its weights: editing one must not rewrite the default that
    # every other contract, and the selector's own signature, will be minted from next.
    assert minted is not DEFAULT_UTILITY_WEIGHTS
