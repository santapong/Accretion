"""The §21 pre-registration is frozen: its digest is pinned and an edited page is refused.

A pre-registration that can be edited after the locked test set is read is a description of
the result, not a registration. The pin lives in ``config.v1.json`` because that file is
already part of ``corpus_sha256``; the digest therefore changes the run id of every benchmark
run, which is what makes a post-freeze amendment visible in every result that follows it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.router_benchmark import CORPUS_ROOT, RouterBenchmarkConfig, RouterBenchmarkCorpus

REPO = Path(__file__).resolve().parents[1]
PREREGISTRATION = REPO / "docs" / "research" / "v0.4" / "preregistration.md"
FIELD_HEADINGS = re.compile(r"^### (\d+)\. ", re.MULTILINE)


def test_the_pinned_digest_is_the_page_on_disk() -> None:
    corpus = RouterBenchmarkCorpus.load()
    pinned = corpus.config.preregistration_sha256
    assert pinned is not None, "the §21 fields are frozen; the config must carry the pin"
    assert pinned == hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()


def test_every_one_of_the_fifteen_fields_is_frozen() -> None:
    text = PREREGISTRATION.read_text(encoding="utf-8")
    numbers = [int(match.group(1)) for match in FIELD_HEADINGS.finditer(text)]
    assert numbers == list(range(1, 16))
    assert "Status: TBD" not in text
    assert len(re.findall(r"\*\*Status: FROZEN \d{4}-\d{2}-\d{2}\*\*", text)) == 15


def test_a_mutated_field_no_longer_matches_the_pin(tmp_path: Path) -> None:
    text = PREREGISTRATION.read_text(encoding="utf-8")
    amended = text.replace("`delta_min = 0.02`", "`delta_min = 0.05`", 1)
    assert amended != text, "the mutation must land on a frozen value"
    mutated = tmp_path / "preregistration.md"
    mutated.write_text(amended, encoding="utf-8")
    config = json.loads((CORPUS_ROOT / "config.v1.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(mutated.read_bytes()).hexdigest() != config["preregistration_sha256"]


def test_the_pin_is_a_sha256_or_absent() -> None:
    document = json.loads((CORPUS_ROOT / "config.v1.json").read_text(encoding="utf-8"))
    document["preregistration_sha256"] = "not-a-digest"
    with pytest.raises(ValidationError):
        RouterBenchmarkConfig.model_validate(document)
    document.pop("preregistration_sha256")
    assert RouterBenchmarkConfig.model_validate(document).preregistration_sha256 is None
