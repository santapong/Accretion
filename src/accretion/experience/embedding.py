from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from accretion.contracts import Task, TaskProfile
from accretion.experience.models import (
    EXPERIENCE_EMBEDDING_VERSION,
    EXPERIENCE_VECTOR_DIMENSIONS,
    TrajectorySegmentKind,
)
from accretion.redaction import redact_text

MANIFEST_NAMES = (
    "Cargo.toml",
    "Gemfile",
    "Makefile",
    "build.gradle",
    "go.mod",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.:/+-]*")


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: list[float]
    input_digest: str
    version: str = EXPERIENCE_EMBEDDING_VERSION


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def repository_manifests(repository: Path) -> list[str]:
    return [name for name in MANIFEST_NAMES if (repository / name).is_file()]


def manifest_digest(repository: Path, paths: list[str]) -> str:
    records = [
        {
            "path": path,
            "sha256": hashlib.sha256((repository / path).read_bytes()).hexdigest(),
        }
        for path in sorted(paths)
    ]
    return canonical_digest(records)


def task_family(task: Task, manifests: list[str]) -> str:
    ecosystem = "+".join(Path(item).name.lower() for item in manifests) or "no-manifest"
    return f"{task.envelope.task_type.value.lower()}:{ecosystem}"[:160]


def deterministic_embedding(
    task: Task,
    profile: TaskProfile,
    *,
    manifests: list[str],
    verifier_ids: list[str],
    segment_kinds: list[TrajectorySegmentKind] | None = None,
) -> EmbeddingResult:
    """Build the frozen, redacted, signed feature-hash vector for P7."""

    weighted: list[tuple[str, int]] = []
    family = task_family(task, manifests)
    weighted.extend(
        [
            (f"task-family:{family}", 3),
            (f"task-type:{task.envelope.task_type.value.lower()}", 3),
        ]
    )

    profile_values = {
        "complexity": profile.complexity,
        "structure": profile.structure_certainty,
        "feedback": profile.feedback_dependency,
        "dependency": profile.dependency_complexity,
        "parallelism": profile.parallelism_potential,
        "uncertainty": profile.uncertainty,
        "verifier-strength": profile.verifier_strength,
        "risk": profile.risk.value,
        "horizon": profile.expected_horizon.value,
    }
    for name, value in sorted(profile_values.items()):
        if isinstance(value, float):
            bucket = min(4, max(0, int(value * 5)))
            weighted.append((f"profile:{name}:b{bucket}", 2))
        elif value is not None:
            weighted.append((f"profile:{name}:{str(value).lower()}", 2))
    weighted.extend((f"verifier:{item}", 2) for item in sorted(set(verifier_ids)))
    weighted.extend((f"manifest:{item.lower()}", 2) for item in sorted(set(manifests)))
    weighted.extend(
        (f"output:{canonical_digest(item)}", 2)
        for item in sorted(
            task.envelope.required_outputs,
            key=lambda item: canonical_digest(item),
        )
    )
    weighted.extend(
        (f"transfer:skill:{item}", 2) for item in sorted(set(task.envelope.requested_skills))
    )
    weighted.extend(
        (f"transfer:capability:{item}", 2)
        for item in sorted(set(task.envelope.allowed_capabilities))
    )
    weighted.extend(
        (f"segment:{item.value.lower()}", 2) for item in sorted(set(segment_kinds or []))
    )

    text_parts = [
        task.envelope.objective,
        *task.envelope.constraints,
        *task.envelope.success_criteria,
    ]
    tokens = _tokens(" ".join(text_parts))
    weighted.extend((f"text:unigram:{item}", 1) for item in tokens)
    weighted.extend(
        (f"text:bigram:{left}|{right}", 1)
        for left, right in zip(tokens, tokens[1:], strict=False)
    )

    canonical = sorted(weighted)
    vector = [0.0] * EXPERIENCE_VECTOR_DIMENSIONS
    for feature, weight in canonical:
        location = hashlib.sha256(f"index:{feature}".encode()).digest()
        sign_digest = hashlib.sha256(f"sign:{feature}".encode()).digest()
        index = int.from_bytes(location[:8], "big") % EXPERIENCE_VECTOR_DIMENSIONS
        sign = 1.0 if sign_digest[0] & 1 else -1.0
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("deterministic experience embedding has no features")
    normalized = [value / norm for value in vector]
    return EmbeddingResult(
        vector=normalized,
        input_digest=canonical_digest(
            {"version": EXPERIENCE_EMBEDDING_VERSION, "features": canonical}
        ),
    )


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", redact_text(value)).lower()
    return _TOKEN.findall(normalized)
