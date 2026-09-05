"""The offline ranker: five heads, five bags each, and a lower bound the selector can act on.

SDD §7.6 ranks a candidate on five estimates and ADR-045 insists they stay five. A single scalar
reward would let a configuration that reliably passes its own node while degrading the run look
excellent, and that shape — local success bought with global cost — is exactly what §14.3 exists
to catch. So :class:`RankerArtifact` holds one ensemble per head: ``node_verified_success`` and
``run_verified_success`` under logistic loss, ``quality``, ``cost`` and ``latency`` under squared.

Each head is a **bag of five** models rather than one. The bags differ by a seeded bootstrap
resample of the rows and by a seeded feature subsample, so the spread of their predictions is an
estimate of what the model does not know about this row — epistemic uncertainty, the quantity
§9.5's exploration gate is defined against. One model would have to invent that number.

Two kinds of interval come out, and they are labelled differently because they mean different
things. The success heads get a **conformal** interval: the calibrated probability less the
grouped split-conformal quantile from :mod:`accretion.routing.calibration`, which is a real
finite-sample guarantee about held-out projects. The magnitude heads get a two-standard-deviation
**bag band**, which is a description of ensemble disagreement and not a coverage statement at all.
``DistributionEstimate.method`` carries which is which, precisely so that a later recalibration
can tell them apart instead of guessing (OQ-405).

The artefact is a directory whose *name is its digest*, holding the ranker, the calibration and a
manifest that records the digest of each. Calibration is a separate file on purpose: recalibrating
a model must be able to produce a new version without retraining a single tree. Nothing here
touches a store: loading is ``load(directory, digest)``, and a caller who wants that digest to
come from a ``RouterModelVersion`` row is the one holding the store.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import (
    DistributionEstimate,
    PredictedOutcomes,
    UncertaintySummary,
)
from accretion.routing.calibration import (
    DEFAULT_ALPHA,
    Calibrator,
    calibrator_from_json,
    lcb,
)
from accretion.routing.gbdt import (
    GBDT,
    FeatureRow,
    GBDTModel,
    Loss,
    row_sort_key,
)

RANKER_SCHEMA_VERSION = "1.0"
"""Version of the serialized ranker and manifest format."""

DEFAULT_BAGS = 5
"""The M4 plan's bag count: enough spread to estimate variance, cheap enough to train inline."""

DEFAULT_ARTIFACT_DIR = Path(".accretion") / "router-artifacts"
"""Where artefacts live when a caller does not inject a directory. Never read at import time."""

CONFORMAL_METHOD = "split-conformal-grouped"
"""``DistributionEstimate.method`` for the success heads: a real held-out guarantee."""

BAG_BAND_METHOD = "bagged-ensemble-two-sd"
"""``DistributionEstimate.method`` for the magnitude heads: ensemble spread, not coverage."""

_BAG_BAND_WIDTH = 2.0
_MANIFEST_NAME = "manifest.json"
_RANKER_NAME = "ranker.json"
_CALIBRATION_NAME = "calibration.json"


class RankerError(ValueError):
    """Base class for every rejection in this module."""


class ArtifactNotFoundError(RankerError):
    """No artefact directory for that digest, or it is missing one of its three files."""


class ArtifactDigestMismatchError(RankerError):
    """The bytes on disk do not hash to the digest that was asked for.

    Raised before anything is parsed. An artefact that has been edited — by a bad sync, a partial
    write, or a hand — is not a model to be repaired, and predicting from it would attribute
    someone else's numbers to a version id that promised these ones.
    """


class FeatureSchemaMismatchError(RankerError):
    """A predictor was assembled from parts trained under different feature vocabularies.

    SDD §7.12 and §10.1: a model's weights are *about* a feature schema, so the same row means
    something different under a different one. Refuse rather than predict.
    """


class OutcomeHead(StrEnum):
    """The five things a candidate is ranked on (SDD §7.6), spelled as the contract spells them."""

    NODE_VERIFIED_SUCCESS = "NODE_VERIFIED_SUCCESS"
    RUN_VERIFIED_SUCCESS = "RUN_VERIFIED_SUCCESS"
    QUALITY = "QUALITY"
    COST = "COST"
    LATENCY = "LATENCY"


HEAD_LOSSES: Mapping[OutcomeHead, Loss] = {
    OutcomeHead.NODE_VERIFIED_SUCCESS: "logistic",
    OutcomeHead.RUN_VERIFIED_SUCCESS: "logistic",
    OutcomeHead.QUALITY: "squared",
    OutcomeHead.COST: "squared",
    OutcomeHead.LATENCY: "squared",
}
"""Which loss each head is fitted under. Probabilities are logistic; magnitudes are squared."""

SUCCESS_HEADS: tuple[OutcomeHead, ...] = (
    OutcomeHead.NODE_VERIFIED_SUCCESS,
    OutcomeHead.RUN_VERIFIED_SUCCESS,
)
"""The heads whose output is a probability, and therefore the heads a calibrator applies to."""

UNIT_HEADS: frozenset[OutcomeHead] = frozenset({OutcomeHead.QUALITY})
"""Magnitude heads whose scale is [0, 1], clamped so a band cannot leave the scale."""

ORDERED_HEADS: tuple[OutcomeHead, ...] = tuple(sorted(OutcomeHead))
"""Every head, in one fixed order, so that serialization never depends on a set or a dict."""


@runtime_checkable
class OutcomePredictor(Protocol):
    """What the selector needs from anything that predicts: an identity and five estimates.

    ``version_id`` is read-only here because the identity of a predictor is decided when it is
    built. A caller that could reassign it could make a receipt cite a version it did not use.
    """

    @property
    def version_id(self) -> str:
        """The router model version these predictions may be attributed to."""

    def predict(self, row: Sequence[float | None]) -> tuple[PredictedOutcomes, UncertaintySummary]:
        """Predict the five outcomes for one feature row, with the uncertainty around them."""


@dataclass(frozen=True, slots=True)
class BagEnsemble:
    """One head's bag of models. The spread across ``models`` is the head's epistemic term."""

    head: OutcomeHead
    models: tuple[GBDTModel, ...]

    def __post_init__(self) -> None:
        if not self.models:
            raise RankerError(f"head {self.head.value} has no models")
        loss = HEAD_LOSSES[self.head]
        for model in self.models:
            if model.loss != loss:
                raise RankerError(
                    f"head {self.head.value} expects {loss} loss, got {model.loss}"
                )

    def bag_values(self, row: FeatureRow, calibrator: Calibrator | None) -> list[float]:
        """One number per bag: a calibrated probability if calibrated, else the raw score."""

        if calibrator is None:
            return [model.predict(row) for model in self.models]
        return [calibrator.apply(model.predict(row)) for model in self.models]

    def to_json(self) -> dict[str, Any]:
        return {
            "head": self.head.value,
            "models": [model.to_json() for model in self.models],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> BagEnsemble:
        raw_head = payload.get("head")
        if not isinstance(raw_head, str) or raw_head not in OutcomeHead.__members__:
            raise RankerError(f"unknown outcome head {raw_head!r}")
        raw_models = payload.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise RankerError(f"head {raw_head} has no serialized models")
        return cls(
            head=OutcomeHead(raw_head),
            models=tuple(GBDTModel.from_json(model) for model in raw_models),
        )


@dataclass(frozen=True, slots=True)
class RankerArtifact:
    """Five bagged heads, their feature schema, and the seed that produced them.

    ``ensembles`` is a tuple in :data:`ORDERED_HEADS` order rather than a mapping, because a
    mapping would serialize in whatever order it was built and the digest would follow.
    """

    version_id: str
    feature_schema_version: str
    n_features: int
    seed: int
    ensembles: tuple[BagEnsemble, ...]

    def __post_init__(self) -> None:
        if not self.version_id:
            raise RankerError("a ranker artefact must name its version")
        if self.n_features < 1:
            raise RankerError(f"n_features {self.n_features} must be positive")
        heads = tuple(ensemble.head for ensemble in self.ensembles)
        if heads != ORDERED_HEADS:
            raise RankerError(
                f"ensembles must be exactly {[head.value for head in ORDERED_HEADS]}, "
                f"got {[head.value for head in heads]}"
            )
        for ensemble in self.ensembles:
            for model in ensemble.models:
                if model.n_features != self.n_features:
                    raise RankerError(
                        f"head {ensemble.head.value} was fitted on {model.n_features} features, "
                        f"artefact declares {self.n_features}"
                    )

    def ensemble(self, head: OutcomeHead) -> BagEnsemble:
        return self.ensembles[ORDERED_HEADS.index(head)]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": RANKER_SCHEMA_VERSION,
            "version_id": self.version_id,
            "feature_schema_version": self.feature_schema_version,
            "n_features": self.n_features,
            "seed": self.seed,
            "ensembles": [ensemble.to_json() for ensemble in self.ensembles],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> RankerArtifact:
        if payload.get("schema_version") != RANKER_SCHEMA_VERSION:
            raise RankerError(
                f"ranker schema version {payload.get('schema_version')!r} "
                f"is not {RANKER_SCHEMA_VERSION!r}"
            )
        version_id = payload.get("version_id")
        feature_schema_version = payload.get("feature_schema_version")
        n_features = payload.get("n_features")
        seed = payload.get("seed")
        if not isinstance(version_id, str) or not isinstance(feature_schema_version, str):
            raise RankerError("ranker artefact is missing its version or feature schema")
        if not isinstance(n_features, int) or isinstance(n_features, bool):
            raise RankerError(f"n_features {n_features!r} is not an integer")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise RankerError(f"seed {seed!r} is not an integer")
        raw_ensembles = payload.get("ensembles")
        if not isinstance(raw_ensembles, list):
            raise RankerError("ranker artefact is missing its ensembles")
        return cls(
            version_id=version_id,
            feature_schema_version=feature_schema_version,
            n_features=n_features,
            seed=seed,
            ensembles=tuple(BagEnsemble.from_json(item) for item in raw_ensembles),
        )


def artifact_bytes(artifact: RankerArtifact) -> bytes:
    """The canonical JSON bytes of the whole ranker (ADR-056)."""

    return canonical_json(artifact.to_json())


def artifact_digest(artifact: RankerArtifact) -> str:
    """The SHA-256 hex digest of :func:`artifact_bytes`."""

    return hashlib.sha256(artifact_bytes(artifact)).hexdigest()


def train_ranker(
    rows: Sequence[FeatureRow],
    targets: Mapping[OutcomeHead, Sequence[float]],
    *,
    version_id: str,
    feature_schema_version: str,
    seed: int,
    n_trees: int = 60,
    max_depth: int = 3,
    learning_rate: float = 0.1,
    l2: float = 1.0,
    feature_subsample: float = 0.8,
    bags: int = DEFAULT_BAGS,
) -> RankerArtifact:
    """Fit every head's bag and return the artefact.

    The rows are put into canonical order *before* the bootstrap draws, which is the only reason
    the artefact is reproducible: a bag is a list of row positions, so drawing positions out of
    the caller's incidental order would make the same training set produce a different model
    every time it was read back in a different sequence.
    """

    if bags < 1:
        raise RankerError(f"bags {bags} must be at least 1")
    if not rows:
        raise RankerError("cannot train a ranker on an empty training set")
    missing = [head.value for head in ORDERED_HEADS if head not in targets]
    if missing:
        raise RankerError(f"no targets supplied for heads {missing}")
    n_rows = len(rows)
    for head in ORDERED_HEADS:
        if len(targets[head]) != n_rows:
            raise RankerError(
                f"head {head.value} has {len(targets[head])} targets for {n_rows} rows"
            )
    order = sorted(
        range(n_rows),
        key=lambda index: (
            row_sort_key(rows[index]),
            [float(targets[head][index]) for head in ORDERED_HEADS],
        ),
    )
    ordered_rows = [rows[index] for index in order]
    ordered_targets = {
        head: [float(targets[head][index]) for index in order] for head in ORDERED_HEADS
    }
    ensembles: list[BagEnsemble] = []
    for head in ORDERED_HEADS:
        models: list[GBDTModel] = []
        for bag in range(bags):
            bag_seed = seed + bag
            rng = random.Random(bag_seed)
            draw = [rng.randrange(n_rows) for _ in range(n_rows)]
            learner = GBDT(
                loss=HEAD_LOSSES[head],
                n_trees=n_trees,
                max_depth=max_depth,
                learning_rate=learning_rate,
                l2=l2,
                feature_subsample=feature_subsample,
                seed=bag_seed,
            )
            models.append(
                learner.fit(
                    [ordered_rows[index] for index in draw],
                    [ordered_targets[head][index] for index in draw],
                )
            )
        ensembles.append(BagEnsemble(head=head, models=tuple(models)))
    return RankerArtifact(
        version_id=version_id,
        feature_schema_version=feature_schema_version,
        n_features=len(rows[0]),
        seed=seed,
        ensembles=tuple(ensembles),
    )


@dataclass(frozen=True, slots=True)
class LearnedOutcomePredictor:
    """A trained artefact plus the calibration that turns its margins into honest probabilities.

    Assembled from three independently versioned things — trees, calibration, and a conformal
    quantile — and it refuses to be assembled from parts that disagree about the feature schema,
    because that disagreement is silent at every other layer.
    """

    artifact: RankerArtifact
    calibrator: Calibrator
    conformal_quantile: float
    feature_schema_version: str
    alpha: float = DEFAULT_ALPHA

    def __post_init__(self) -> None:
        if not 0.0 <= self.conformal_quantile <= 1.0:
            raise RankerError(
                f"conformal quantile {self.conformal_quantile} is outside [0, 1]"
            )
        if not 0.0 < self.alpha < 1.0:
            raise RankerError(f"alpha {self.alpha} is outside (0, 1)")
        if self.artifact.feature_schema_version != self.feature_schema_version:
            raise FeatureSchemaMismatchError(
                f"artefact was trained under feature schema "
                f"{self.artifact.feature_schema_version!r}, predictor declares "
                f"{self.feature_schema_version!r}"
            )

    @property
    def version_id(self) -> str:
        return self.artifact.version_id

    @property
    def confidence(self) -> float:
        """The nominal level every estimate is quoted at: ``1 - alpha``."""

        return 1.0 - self.alpha

    def predict(
        self, row: Sequence[float | None]
    ) -> tuple[PredictedOutcomes, UncertaintySummary]:
        """The five estimates and the uncertainty summary for one feature row."""

        if len(row) != self.artifact.n_features:
            raise RankerError(
                f"row has {len(row)} features, artefact was trained on "
                f"{self.artifact.n_features}"
            )
        estimates: dict[OutcomeHead, DistributionEstimate] = {}
        variances: dict[OutcomeHead, float] = {}
        for head in ORDERED_HEADS:
            calibrated = head in SUCCESS_HEADS
            values = self.artifact.ensemble(head).bag_values(
                row, self.calibrator if calibrated else None
            )
            mean = math.fsum(values) / len(values)
            variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
            variances[head] = variance
            estimates[head] = (
                self._conformal_estimate(mean)
                if calibrated
                else self._bag_estimate(mean, variance, bounded=head in UNIT_HEADS)
            )
        node_success = estimates[OutcomeHead.NODE_VERIFIED_SUCCESS]
        predicted = PredictedOutcomes(
            quality=estimates[OutcomeHead.QUALITY],
            cost=estimates[OutcomeHead.COST],
            latency=estimates[OutcomeHead.LATENCY],
            node_verified_success=node_success,
            run_verified_success=estimates[OutcomeHead.RUN_VERIFIED_SUCCESS],
        )
        uncertainty = UncertaintySummary(
            epistemic_uncertainty=variances[OutcomeHead.NODE_VERIFIED_SUCCESS],
            lower_confidence_success=node_success.lower_bound,
            calibration_version=self.calibrator.version,
        )
        return predicted, uncertainty

    def _conformal_estimate(self, mean: float) -> DistributionEstimate:
        """A probability with a conformal lower bound: the estimate less the grouped quantile."""

        centre = min(1.0, max(0.0, mean))
        return DistributionEstimate(
            mean=centre,
            lower_bound=lcb(centre, self.conformal_quantile),
            upper_bound=min(1.0, centre + self.conformal_quantile),
            confidence=self.confidence,
            method=CONFORMAL_METHOD,
        )

    def _bag_estimate(
        self, mean: float, variance: float, *, bounded: bool
    ) -> DistributionEstimate:
        """A magnitude with a two-standard-deviation bag band. Not a coverage guarantee — the
        method string says so — but the only honest interval a five-bag ensemble can offer.
        """

        centre = min(1.0, max(0.0, mean)) if bounded else max(0.0, mean)
        spread = _BAG_BAND_WIDTH * math.sqrt(variance)
        upper = centre + spread
        return DistributionEstimate(
            mean=centre,
            lower_bound=max(0.0, centre - spread),
            upper_bound=min(1.0, upper) if bounded else upper,
            confidence=self.confidence,
            method=BAG_BAND_METHOD,
        )

    def manifest(self) -> dict[str, Any]:
        """What the artefact directory is named after: the parts and their digests."""

        return {
            "schema_version": RANKER_SCHEMA_VERSION,
            "version_id": self.version_id,
            "feature_schema_version": self.feature_schema_version,
            "alpha": self.alpha,
            "conformal_quantile": self.conformal_quantile,
            "ranker_digest": hashlib.sha256(artifact_bytes(self.artifact)).hexdigest(),
            "calibration_digest": hashlib.sha256(
                canonical_json(self.calibrator.to_json())
            ).hexdigest(),
        }

    def save(self, directory: Path = DEFAULT_ARTIFACT_DIR) -> str:
        """Write the three files under ``directory/<digest>`` and return that digest.

        The digest is over the manifest's exact bytes, and the manifest carries the digest of each
        part, so a single flipped byte anywhere in the artefact is detected by :meth:`load`
        whichever file it landed in.
        """

        manifest_bytes = canonical_json(self.manifest())
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        target = directory / digest
        target.mkdir(parents=True, exist_ok=True)
        (target / _RANKER_NAME).write_bytes(artifact_bytes(self.artifact))
        (target / _CALIBRATION_NAME).write_bytes(canonical_json(self.calibrator.to_json()))
        (target / _MANIFEST_NAME).write_bytes(manifest_bytes)
        return digest

    @classmethod
    def load(cls, directory: Path, digest: str) -> LearnedOutcomePredictor:
        """Read back the artefact named by ``digest``, verifying every byte before parsing."""

        target = directory / digest
        manifest_bytes = _read(target / _MANIFEST_NAME)
        actual = hashlib.sha256(manifest_bytes).hexdigest()
        if actual != digest:
            raise ArtifactDigestMismatchError(
                f"manifest in {target} hashes to {actual}, not the requested {digest}"
            )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ArtifactDigestMismatchError(f"manifest in {target} is not an object")
        ranker_bytes = _read(target / _RANKER_NAME)
        calibration_bytes = _read(target / _CALIBRATION_NAME)
        _verify_part(ranker_bytes, manifest.get("ranker_digest"), _RANKER_NAME, target)
        _verify_part(
            calibration_bytes, manifest.get("calibration_digest"), _CALIBRATION_NAME, target
        )
        quantile = manifest.get("conformal_quantile")
        alpha = manifest.get("alpha")
        schema = manifest.get("feature_schema_version")
        if not isinstance(quantile, int | float) or isinstance(quantile, bool):
            raise RankerError(f"manifest in {target} has no numeric conformal quantile")
        if not isinstance(alpha, int | float) or isinstance(alpha, bool):
            raise RankerError(f"manifest in {target} has no numeric alpha")
        if not isinstance(schema, str):
            raise RankerError(f"manifest in {target} has no feature schema version")
        return cls(
            artifact=RankerArtifact.from_json(json.loads(ranker_bytes.decode("utf-8"))),
            calibrator=calibrator_from_json(json.loads(calibration_bytes.decode("utf-8"))),
            conformal_quantile=float(quantile),
            feature_schema_version=schema,
            alpha=float(alpha),
        )


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise ArtifactNotFoundError(f"no artefact file at {path}") from error


def _verify_part(payload: bytes, expected: object, name: str, target: Path) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    if not isinstance(expected, str) or actual != expected:
        raise ArtifactDigestMismatchError(
            f"{name} in {target} hashes to {actual}, manifest records {expected!r}"
        )
