from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from accretion.benchmark import AcrArchRunner
from accretion.contracts import BenchmarkCategory, Provider
from accretion.live_sample import expected_artifact, select_live_sample, verify_artifact


def test_live_sample_is_balanced_and_stratified_over_frozen_tasks() -> None:
    assignments = select_live_sample(AcrArchRunner().tasks())

    assert len(assignments) == 10
    assert Counter(item.category for item in assignments) == {
        category: 2 for category in BenchmarkCategory
    }
    assert Counter(item.provider for item in assignments) == {
        Provider.CODEX: 5,
        Provider.CLAUDE: 5,
    }
    for category in BenchmarkCategory:
        assert {
            item.provider for item in assignments if item.category is category
        } == {Provider.CODEX, Provider.CLAUDE}


def test_live_sample_verifier_requires_the_exact_object(tmp_path: Path) -> None:
    assignment = select_live_sample(AcrArchRunner().tasks())[0]
    expected = expected_artifact(assignment)
    artifact = tmp_path / "result.json"
    artifact.write_text(json.dumps(expected))

    assert len(verify_artifact(artifact, expected)) == 64

    artifact.write_text(json.dumps({**expected, "unexpected": True}))
    with pytest.raises(ValueError, match="exact expected object"):
        verify_artifact(artifact, expected)
