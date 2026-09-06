"""A deterministic gradient-boosted decision-tree learner, in-repo and dependency-free.

OQ-401 asks for a gradient-boosted ranking baseline before any neural model, and the obvious
way to get one is scikit-learn. The dependency-weight check says no: ``pyproject.toml`` has no
numeric dependency at all today, scikit-learn drags numpy, scipy, joblib and threadpoolctl —
tens of megabytes of compiled wheels — through ``uv lock --check`` and the clean-checkout job,
and the data it would be handed in v0.4 is hundreds to low thousands of rows by roughly eighty
features. That is a workload pure Python can carry. So the learner lives here, behind the
:class:`Learner` and :class:`Model` protocols, and a library implementation can be swapped in
later without any caller learning about it.

**The model is a pure function of (rows, targets, weights, hyper-parameters).** Not "almost":
byte-for-byte, which is what makes :func:`artifact_digest` worth storing on a
``RouterModelVersion``. Three things buy that property and each is easy to lose:

* ``fit`` sorts its rows into a canonical order before it looks at them, so a caller that
  shuffles its training set — or reads it back from a store with a different index — trains the
  same trees. Drop that sort and the artefact digest becomes a function of iteration order.
* Split candidates are enumerated in ascending ``(feature index, threshold)`` order and a new
  best split must be *strictly* better, so an exact tie resolves to the lowest feature index and
  the lowest threshold rather than to whichever the loop happened to reach last.
* The only randomness is feature subsampling, drawn from a ``random.Random(seed)`` created in
  ``fit`` and used in a fixed order. Nothing iterates a ``set`` or a ``dict``.

**Missing values are first class.** A row is a positional ``list[float | None]`` in the feature
schema's order (the schema itself belongs to another module; this one knows only how wide a row
is). ``None`` means "not observed", and every split learns a *default direction* for it by
scoring both placements and keeping the better one, so a feature that is missing for a reason
carries that reason into the tree. A float ``NaN`` is treated as ``None`` on the way in, because
a caller who computed a ratio with a zero denominator meant "unknown" and not "poison"; ``inf``
is refused outright, since a threshold that cannot be written as canonical JSON could not be
sealed into an artefact.

Complexity is ``O(trees x depth x rows x features)``, and the constant is a Python interpreter
loop rather than a BLAS call, so the envelope is worth stating in measured numbers instead of
adjectives. On one core, with the shipped defaults and five per cent of cells missing: 250 rows
by 80 features with 100 trees of depth 3 takes about 9 s, 500 by 80 about 18 s, and the cost is
close to linear in rows and features, which puts 5,000 rows by 80 features with 100 trees in the
*minutes* — several of them — not the seconds. That is the honest shape of the dependency-weight
argument: at the workload v0.4 actually has (hundreds of rows, offline, once per router version,
against a training job measured in minutes anyway) this is comfortably cheap, and it is at low
thousands of rows that a compiled learner behind the :class:`Learner` protocol starts to earn its
wheels. The tests here train on a few hundred rows with a dozen trees and stay under two seconds.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from accretion.contracts.canonical import canonical_json

Loss = Literal["logistic", "squared"]
"""The two objectives the router needs: a probability head and a magnitude head."""

FeatureRow = Sequence[float | None]
"""One positional feature vector. ``None`` is "not observed", not "zero"."""

ARTIFACT_SCHEMA_VERSION = "1.0"
"""Version of the serialized tree format, carried inside every artefact."""

MAX_TREES = 200
"""Ceiling from the M4 plan. More trees is more artefact, not more signal, at these row counts."""

MAX_DEPTH = 4
"""Ceiling from the M4 plan. Depth 4 already spans sixteen leaves per tree."""

_MIN_GAIN = 1e-12
"""A split must beat this to be taken, so floating-point noise cannot manufacture structure."""

_MIN_HESSIAN = 1e-12
"""Floor on a logistic second derivative, so a saturated row cannot zero out a leaf solve."""

_LOGIT_CLAMP = 40.0
"""``sigmoid`` is flat past this; clamping keeps ``exp`` away from overflow."""

_PROBABILITY_FLOOR = 1e-6
"""Keeps the base score finite when a training split is all ones or all zeros."""


class GBDTError(ValueError):
    """Base class for every rejection this module raises.

    A ``ValueError`` and not a bare ``Exception``: every one of these is a caller handing the
    learner data or hyper-parameters it cannot honour, which is what ``ValueError`` means.
    """


class TrainingDataError(GBDTError):
    """Rows, targets and weights do not describe a trainable problem."""


class ModelDecodeError(GBDTError):
    """A serialized artefact is not a model this module can rebuild."""


@runtime_checkable
class Model(Protocol):
    """A fitted predictor: score a row, write it down, read it back."""

    def predict(self, row: FeatureRow) -> float:
        """The model's raw score for ``row`` (a logit under logistic loss)."""

    def to_json(self) -> dict[str, Any]:
        """A JSON-safe dict; canonical bytes come from :func:`artifact_bytes`."""

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> Model:
        """Rebuild the model written by :meth:`to_json`."""


@runtime_checkable
class Learner(Protocol):
    """Something that turns training data into a :class:`Model`.

    The seam OQ-401 asks for: a scikit-learn wrapper implementing this protocol would be a drop-in
    for :class:`GBDT`, and nothing above it would change.
    """

    def fit(
        self,
        rows: Sequence[FeatureRow],
        targets: Sequence[float],
        weights: Sequence[float] | None = None,
    ) -> Model:
        """Fit on ``rows``/``targets``, optionally weighted per row."""


@dataclass(frozen=True, slots=True)
class TreeNode:
    """One node of one tree, in the flat array layout the artefact serializes.

    A leaf is spelled ``feature == -1``: one shape for both kinds keeps the JSON uniform, which
    keeps the canonical bytes uniform, which is the whole point of the exercise.
    """

    feature: int
    threshold: float
    missing_left: bool
    left: int
    right: int
    value: float

    @property
    def is_leaf(self) -> bool:
        return self.feature < 0


@dataclass(frozen=True, slots=True)
class GBDTModel:
    """A fitted additive ensemble: ``base_score`` plus one shrunk leaf value per tree.

    The learning rate is folded into the stored leaf values rather than kept beside them, so
    prediction is a plain sum and a model reloaded from JSON cannot be scored under a different
    shrinkage than it was fitted with.
    """

    loss: Loss
    n_features: int
    base_score: float
    trees: tuple[tuple[TreeNode, ...], ...]

    def predict(self, row: FeatureRow) -> float:
        """The raw additive score: a logit under logistic loss, the value under squared loss."""

        if len(row) != self.n_features:
            raise TrainingDataError(
                f"row has {len(row)} features, model was fitted on {self.n_features}"
            )
        total = self.base_score
        for tree in self.trees:
            node = tree[0]
            while node.feature >= 0:
                value = row[node.feature]
                if value is None or math.isnan(value):
                    node = tree[node.left if node.missing_left else node.right]
                elif value < node.threshold:
                    node = tree[node.left]
                else:
                    node = tree[node.right]
            total += node.value
        return total

    def predict_probability(self, row: FeatureRow) -> float:
        """The squashed score. Logistic models only — a squared head has no probability."""

        if self.loss != "logistic":
            raise GBDTError(
                f"predict_probability is defined for logistic models, not {self.loss!r}"
            )
        return sigmoid(self.predict(row))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "loss": self.loss,
            "n_features": self.n_features,
            "base_score": self.base_score,
            "trees": [
                [
                    {
                        "feature": node.feature,
                        "threshold": node.threshold,
                        "missing_left": node.missing_left,
                        "left": node.left,
                        "right": node.right,
                        "value": node.value,
                    }
                    for node in tree
                ]
                for tree in self.trees
            ],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> GBDTModel:
        """Rebuild a model, refusing anything this module did not write.

        Every field is checked rather than trusted: an artefact is read back from disk, and a
        malformed one must fail here with a name rather than three frames later as a ``KeyError``
        or, worse, as a silently wrong prediction.
        """

        version = payload.get("schema_version")
        if version != ARTIFACT_SCHEMA_VERSION:
            raise ModelDecodeError(
                f"artefact schema version {version!r} is not {ARTIFACT_SCHEMA_VERSION!r}"
            )
        loss = payload.get("loss")
        if loss not in ("logistic", "squared"):
            raise ModelDecodeError(f"unknown loss {loss!r}")
        n_features = payload.get("n_features")
        if not isinstance(n_features, int) or isinstance(n_features, bool) or n_features < 1:
            raise ModelDecodeError(f"n_features {n_features!r} is not a positive integer")
        base_score = payload.get("base_score")
        if not isinstance(base_score, int | float) or isinstance(base_score, bool):
            raise ModelDecodeError(f"base_score {base_score!r} is not a number")
        raw_trees = payload.get("trees")
        if not isinstance(raw_trees, list):
            raise ModelDecodeError("trees must be a list")
        trees: list[tuple[TreeNode, ...]] = []
        for index, raw_tree in enumerate(raw_trees):
            if not isinstance(raw_tree, list) or not raw_tree:
                raise ModelDecodeError(f"tree {index} is not a non-empty list of nodes")
            trees.append(tuple(_node_from_json(node, index) for node in raw_tree))
        return cls(
            loss=loss,
            n_features=n_features,
            base_score=float(base_score),
            trees=tuple(trees),
        )


def _node_from_json(payload: object, tree_index: int) -> TreeNode:
    if not isinstance(payload, Mapping):
        raise ModelDecodeError(f"tree {tree_index} contains a node that is not an object")
    expected = {"feature", "threshold", "missing_left", "left", "right", "value"}
    keys = set(payload)
    if keys != expected:
        raise ModelDecodeError(
            f"tree {tree_index} node keys {sorted(keys)} are not {sorted(expected)}"
        )
    feature = payload["feature"]
    left = payload["left"]
    right = payload["right"]
    missing_left = payload["missing_left"]
    structure = (feature, left, right)
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in structure):
        raise ModelDecodeError(f"tree {tree_index} node has non-integer structure fields")
    if not isinstance(missing_left, bool):
        raise ModelDecodeError(f"tree {tree_index} node missing_left is not a boolean")
    threshold = payload["threshold"]
    value = payload["value"]
    if not all(
        isinstance(item, int | float) and not isinstance(item, bool)
        for item in (threshold, value)
    ):
        raise ModelDecodeError(f"tree {tree_index} node has non-numeric threshold or value")
    return TreeNode(
        feature=feature,
        threshold=float(threshold),
        missing_left=missing_left,
        left=left,
        right=right,
        value=float(value),
    )


def sigmoid(value: float) -> float:
    """The logistic function, written so that it cannot overflow at either tail."""

    clamped = max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, value))
    if clamped >= 0.0:
        return 1.0 / (1.0 + math.exp(-clamped))
    exponential = math.exp(clamped)
    return exponential / (1.0 + exponential)


def artifact_bytes(model: Model) -> bytes:
    """The canonical JSON bytes of ``model`` (ADR-056), which are what gets hashed and stored."""

    return canonical_json(model.to_json())


def artifact_digest(model: Model) -> str:
    """The SHA-256 hex digest of :func:`artifact_bytes`."""

    return hashlib.sha256(artifact_bytes(model)).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class GBDT:
    """Hyper-parameters for one fit. Keyword-only, because seven positional numbers is a trap.

    Bounds are enforced at construction rather than at fit time: ``n_trees`` and ``max_depth`` are
    capped by the M4 plan, and ``l2`` must be strictly positive so that the leaf solve
    ``-G / (H + l2)`` is total even when every hessian in a leaf has underflowed.
    """

    loss: Loss
    n_trees: int = 100
    max_depth: int = 3
    learning_rate: float = 0.1
    l2: float = 1.0
    feature_subsample: float = 1.0
    min_samples_leaf: int = 1
    seed: int

    def __post_init__(self) -> None:
        if self.loss not in ("logistic", "squared"):
            raise GBDTError(f"unknown loss {self.loss!r}")
        if not 1 <= self.n_trees <= MAX_TREES:
            raise GBDTError(f"n_trees {self.n_trees} outside 1..{MAX_TREES}")
        if not 1 <= self.max_depth <= MAX_DEPTH:
            raise GBDTError(f"max_depth {self.max_depth} outside 1..{MAX_DEPTH}")
        if not 0.0 < self.learning_rate <= 1.0:
            raise GBDTError(f"learning_rate {self.learning_rate} outside (0, 1]")
        if self.l2 <= 0.0:
            raise GBDTError(f"l2 {self.l2} must be positive so the leaf solve is total")
        if not 0.0 < self.feature_subsample <= 1.0:
            raise GBDTError(f"feature_subsample {self.feature_subsample} outside (0, 1]")
        if self.min_samples_leaf < 1:
            raise GBDTError(f"min_samples_leaf {self.min_samples_leaf} must be at least 1")

    def fit(
        self,
        rows: Sequence[FeatureRow],
        targets: Sequence[float],
        weights: Sequence[float] | None = None,
    ) -> GBDTModel:
        """Fit the ensemble. The result depends on the *contents* of ``rows``, never their order."""

        prepared_rows, prepared_targets, prepared_weights = _prepare(rows, targets, weights)
        n_rows = len(prepared_rows)
        n_features = len(prepared_rows[0])
        columns = _presorted_columns(prepared_rows, n_features)
        base_score = self._base_score(prepared_targets, prepared_weights)
        scores = [base_score] * n_rows
        rng = random.Random(self.seed)
        trees: list[tuple[TreeNode, ...]] = []
        for _ in range(self.n_trees):
            gradients, hessians = self._derivatives(scores, prepared_targets, prepared_weights)
            features = self._sample_features(rng, n_features)
            nodes, leaf_of_row = self._grow_tree(
                prepared_rows, columns, features, gradients, hessians
            )
            for index in range(n_rows):
                scores[index] += nodes[leaf_of_row[index]].value
            trees.append(tuple(nodes))
        return GBDTModel(
            loss=self.loss,
            n_features=n_features,
            base_score=base_score,
            trees=tuple(trees),
        )

    def _sample_features(self, rng: random.Random, n_features: int) -> list[int]:
        """The feature indices this tree may split on, always ascending.

        Ascending because the split search breaks ties on the first feature it sees, so an
        unsorted draw would make the tie-break a function of ``random``'s internal ordering.
        """

        wanted = max(1, min(n_features, round(self.feature_subsample * n_features)))
        if wanted == n_features:
            return list(range(n_features))
        return sorted(rng.sample(range(n_features), wanted))

    def _base_score(self, targets: Sequence[float], weights: Sequence[float]) -> float:
        total_weight = math.fsum(weights)
        mean = math.fsum(w * t for w, t in zip(weights, targets, strict=True)) / total_weight
        if self.loss == "squared":
            return mean
        bounded = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, mean))
        return math.log(bounded / (1.0 - bounded))

    def _derivatives(
        self,
        scores: Sequence[float],
        targets: Sequence[float],
        weights: Sequence[float],
    ) -> tuple[list[float], list[float]]:
        if self.loss == "squared":
            gradients = [w * (s - t) for s, t, w in zip(scores, targets, weights, strict=True)]
            return gradients, list(weights)
        gradients = []
        hessians = []
        for score, target, weight in zip(scores, targets, weights, strict=True):
            probability = sigmoid(score)
            gradients.append(weight * (probability - target))
            hessians.append(weight * max(probability * (1.0 - probability), _MIN_HESSIAN))
        return gradients, hessians

    def _grow_tree(
        self,
        rows: Sequence[list[float | None]],
        columns: Sequence[list[tuple[float, int]]],
        features: Sequence[int],
        gradients: Sequence[float],
        hessians: Sequence[float],
    ) -> tuple[list[TreeNode], list[int]]:
        """Grow one tree level by level, returning its nodes and each row's terminal node.

        Level-wise and not depth-wise for a reason that is about cost, not shape: one pass over a
        pre-sorted column updates the running sums of *every* node on the level at once, so a
        level costs ``O(rows x features)`` however many nodes it holds.
        """

        n_rows = len(rows)
        l2 = self.l2
        node_feature = [-1]
        node_threshold = [0.0]
        node_missing_left = [False]
        node_left = [-1]
        node_right = [-1]
        node_of_row = [0] * n_rows
        active = [0]
        for _ in range(self.max_depth):
            if not active:
                break
            n_nodes = len(node_feature)
            is_active = bytearray(n_nodes)
            for node in active:
                is_active[node] = 1
            total_g = [0.0] * n_nodes
            total_h = [0.0] * n_nodes
            total_c = [0] * n_nodes
            for index in range(n_rows):
                node = node_of_row[index]
                if is_active[node]:
                    total_g[node] += gradients[index]
                    total_h[node] += hessians[index]
                    total_c[node] += 1
            best_gain = [_MIN_GAIN] * n_nodes
            best_feature = [-1] * n_nodes
            best_threshold = [0.0] * n_nodes
            best_missing_left = [False] * n_nodes
            parent_score = [
                total_g[node] * total_g[node] / (total_h[node] + l2) for node in range(n_nodes)
            ]
            minimum = self.min_samples_leaf
            for feature in features:
                column = columns[feature]
                # First pass: the observed totals per node, which are what say how much of each
                # node is *missing* this feature. A split cannot be scored without that number,
                # and it is only known once the column has been walked.
                seen_g = [0.0] * n_nodes
                seen_h = [0.0] * n_nodes
                seen_c = [0] * n_nodes
                for _, index in column:
                    node = node_of_row[index]
                    if is_active[node]:
                        seen_g[node] += gradients[index]
                        seen_h[node] += hessians[index]
                        seen_c[node] += 1
                missing_g = [total_g[node] - seen_g[node] for node in range(n_nodes)]
                missing_h = [total_h[node] - seen_h[node] for node in range(n_nodes)]
                missing_c = [total_c[node] - seen_c[node] for node in range(n_nodes)]
                # Second pass: running prefix sums, scoring a candidate every time the value
                # changes. The threshold *is* the value on the right, so the split is exact and
                # never invents a midpoint that floating point might round onto a real value.
                run_g = [0.0] * n_nodes
                run_h = [0.0] * n_nodes
                run_c = [0] * n_nodes
                previous: list[float | None] = [None] * n_nodes
                for value, index in column:
                    node = node_of_row[index]
                    if not is_active[node]:
                        continue
                    if previous[node] is not None and value != previous[node]:
                        parent_g = total_g[node]
                        parent_h = total_h[node]
                        parent_c = total_c[node]
                        base = parent_score[node]
                        left_g = run_g[node]
                        left_h = run_h[node]
                        left_c = run_c[node]
                        gone = missing_c[node]
                        # ``True`` first, so that a tie — including the common case of a
                        # feature with nothing missing — sends unobserved rows left by
                        # convention rather than by loop order.
                        for missing_left in (True,) if gone == 0 else (True, False):
                            if missing_left:
                                side_g = left_g + missing_g[node]
                                side_h = left_h + missing_h[node]
                                side_c = left_c + gone
                            else:
                                side_g = left_g
                                side_h = left_h
                                side_c = left_c
                            other_c = parent_c - side_c
                            if side_c < minimum or other_c < minimum:
                                continue
                            other_g = parent_g - side_g
                            other_h = parent_h - side_h
                            gain = 0.5 * (
                                side_g * side_g / (side_h + l2)
                                + other_g * other_g / (other_h + l2)
                                - base
                            )
                            if gain > best_gain[node]:
                                best_gain[node] = gain
                                best_feature[node] = feature
                                best_threshold[node] = value
                                best_missing_left[node] = missing_left
                    run_g[node] += gradients[index]
                    run_h[node] += hessians[index]
                    run_c[node] += 1
                    previous[node] = value
            next_active: list[int] = []
            for node in active:
                if best_feature[node] < 0:
                    continue
                left = len(node_feature)
                right = left + 1
                node_feature.extend((-1, -1))
                node_threshold.extend((0.0, 0.0))
                node_missing_left.extend((False, False))
                node_left.extend((-1, -1))
                node_right.extend((-1, -1))
                node_feature[node] = best_feature[node]
                node_threshold[node] = best_threshold[node]
                node_missing_left[node] = best_missing_left[node]
                node_left[node] = left
                node_right[node] = right
                next_active.extend((left, right))
            if not next_active:
                break
            for index in range(n_rows):
                node = node_of_row[index]
                if not is_active[node] or node_feature[node] < 0:
                    continue
                observed = rows[index][node_feature[node]]
                if observed is None:
                    node_of_row[index] = (
                        node_left[node] if node_missing_left[node] else node_right[node]
                    )
                elif observed < node_threshold[node]:
                    node_of_row[index] = node_left[node]
                else:
                    node_of_row[index] = node_right[node]
            active = next_active
        n_nodes = len(node_feature)
        leaf_g = [0.0] * n_nodes
        leaf_h = [0.0] * n_nodes
        for index in range(n_rows):
            node = node_of_row[index]
            leaf_g[node] += gradients[index]
            leaf_h[node] += hessians[index]
        nodes = [
            TreeNode(
                feature=node_feature[node],
                threshold=node_threshold[node],
                missing_left=node_missing_left[node],
                left=node_left[node],
                right=node_right[node],
                value=(
                    0.0
                    if node_feature[node] >= 0
                    else -self.learning_rate * leaf_g[node] / (leaf_h[node] + l2)
                ),
            )
            for node in range(n_nodes)
        ]
        return nodes, node_of_row


def _prepare(
    rows: Sequence[FeatureRow],
    targets: Sequence[float],
    weights: Sequence[float] | None,
) -> tuple[list[list[float | None]], list[float], list[float]]:
    """Validate, clean and canonically sort the training set.

    The sort is the load-bearing line in this module. Rows are ordered by their feature values
    (unobserved before observed, then ascending), then by target, then by weight, so two callers
    holding the same multiset of training examples in different orders fit the same trees and
    write the same artefact bytes.
    """

    n_rows = len(rows)
    if n_rows == 0:
        raise TrainingDataError("cannot fit on an empty training set")
    if len(targets) != n_rows:
        raise TrainingDataError(f"{n_rows} rows but {len(targets)} targets")
    if weights is not None and len(weights) != n_rows:
        raise TrainingDataError(f"{n_rows} rows but {len(weights)} weights")
    n_features = len(rows[0])
    if n_features == 0:
        raise TrainingDataError("cannot fit on rows with no features")
    cleaned: list[list[float | None]] = []
    for index, row in enumerate(rows):
        if len(row) != n_features:
            raise TrainingDataError(
                f"row {index} has {len(row)} features, row 0 has {n_features}"
            )
        cleaned.append([_clean(value, index) for value in row])
    prepared_weights = [1.0] * n_rows if weights is None else [float(item) for item in weights]
    for index, weight in enumerate(prepared_weights):
        if not math.isfinite(weight) or weight < 0.0:
            raise TrainingDataError(
                f"weight {weight!r} at row {index} is not finite and non-negative"
            )
    if math.fsum(prepared_weights) <= 0.0:
        raise TrainingDataError("total training weight is zero")
    prepared_targets = []
    for index, target in enumerate(targets):
        value = float(target)
        if not math.isfinite(value):
            raise TrainingDataError(f"target {target!r} at row {index} is not finite")
        prepared_targets.append(value)
    keys = [row_sort_key(row) for row in cleaned]
    order = sorted(
        range(n_rows),
        key=lambda index: (keys[index], prepared_targets[index], prepared_weights[index]),
    )
    return (
        [cleaned[index] for index in order],
        [prepared_targets[index] for index in order],
        [prepared_weights[index] for index in order],
    )


def _clean(value: float | None, row_index: int) -> float | None:
    """``None`` and ``NaN`` both mean "not observed"; an infinity means the caller has a bug."""

    if value is None:
        return None
    number = float(value)
    if math.isnan(number):
        return None
    if math.isinf(number):
        raise TrainingDataError(
            f"row {row_index} holds {number!r}: an infinite feature has no canonical threshold"
        )
    return number


def row_sort_key(row: Sequence[float | None]) -> list[tuple[int, float]]:
    """A total order over rows that survives ``None``: unobserved sorts before every number.

    Public because it is the *definition* of canonical row order, and every caller that has to
    sort rows before handing them over — the bagged ranker, for one — has to sort them the same
    way or the artefact stops being a function of the training set.
    """

    return [
        (0, 0.0) if value is None or math.isnan(value) else (1, float(value)) for value in row
    ]


def _presorted_columns(
    rows: Sequence[Sequence[float | None]], n_features: int
) -> list[list[tuple[float, int]]]:
    """One ascending ``(value, row)`` list per feature, built once and reused by every tree."""

    columns: list[list[tuple[float, int]]] = []
    for feature in range(n_features):
        column: list[tuple[float, int]] = []
        for index, row in enumerate(rows):
            value = row[feature]
            if value is not None:
                column.append((value, index))
        # Keyed on the value alone: the sort is stable, so equal values keep ascending row order.
        column.sort(key=_column_value)
        columns.append(column)
    return columns


def _column_value(entry: tuple[float, int]) -> float:
    return entry[0]
