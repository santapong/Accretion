"""Amendment 1 is frozen: its digest is pinned beside the pre-registration's, and a corpus that
pins it registers the pooling rule it introduces.

An amended protocol is two documents. The locked-test runner checks both before it reads, so
this file proves three things about the second one: every corpus pins the digest of the page on
disk; the two corpora the amendment applies to register the rate reading for both gates and the
development corpus registers nothing; and a runner handed a corpus whose amendment pin no longer
matches the page refuses before any read, with both digests in the refusal.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accretion.router_benchmark import CORPUS_ROOT, RouterBenchmarkCorpus
from accretion.routing.locked_test import (
    AMENDMENT_1_PATH,
    LockedTestRunner,
    PreregistrationDrift,
    amendment_1_digest,
)

LOCKED_ROOT = CORPUS_ROOT / "locked"
DRIFT_ROOT = CORPUS_ROOT / "drift"


def test_every_corpus_pins_the_amendment_page_on_disk() -> None:
    on_disk = hashlib.sha256(AMENDMENT_1_PATH.read_bytes()).hexdigest()
    assert amendment_1_digest() == on_disk
    for root in (CORPUS_ROOT, LOCKED_ROOT, DRIFT_ROOT):
        config = RouterBenchmarkCorpus.load(root).config
        assert config.amendment_1_sha256 == on_disk, root
        assert config.preregistration_sha256 is not None, (
            "the amendment sits beside the registration"
        )


def test_the_two_frozen_corpora_register_the_rate_reading_and_the_dev_corpus_does_not() -> (
    None
):
    for root in (LOCKED_ROOT, DRIFT_ROOT):
        pooling = RouterBenchmarkCorpus.load(root).config.pooling
        assert pooling is not None, root
        assert (pooling.verified, pooling.false_accept) == ("rate", "rate")
    assert RouterBenchmarkCorpus.load(CORPUS_ROOT).config.pooling is None


def test_the_amendment_page_says_it_is_frozen() -> None:
    text = AMENDMENT_1_PATH.read_text(encoding="utf-8")
    assert "**Status: FROZEN 2026-09-07**" in text
    assert "PROPOSED" not in text.splitlines()[0]


def test_a_corpus_whose_amendment_pin_no_longer_matches_is_refused_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner reads only the two real corpus roots, so the mutation is on the page: an
    amendment copy that differs by one line no longer hashes to the pin the locked corpus
    carries, and the refusal names both digests and writes no access row."""

    amended = tmp_path / "amendment-1.md"
    amended.write_text(AMENDMENT_1_PATH.read_text(encoding="utf-8") + "\n(one more line)\n")
    log = tmp_path / "access-log.jsonl"
    log.write_text("")
    monkeypatch.setenv("ACCRETION_ROUTER_LOCKED_TEST", "1")
    runner = LockedTestRunner(LOCKED_ROOT, access_log_path=log, amendment_path=amended)
    pinned = json.loads((LOCKED_ROOT / "config.v1.json").read_text())["amendment_1_sha256"]
    with pytest.raises(PreregistrationDrift) as refusal:
        runner.run(
            ["M0"],
            principal="test",
            reason="the pin must refuse",
            accessed_at=datetime.now(UTC),
        )
    assert pinned in str(refusal.value)
    assert hashlib.sha256(amended.read_bytes()).hexdigest() in str(refusal.value)
    assert log.read_text() == "", "a refused read is not an access"
