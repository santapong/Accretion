"""Inherited v0.1 benchmark-versioning proof (V01-BENCH-005).

`V01-BENCH-005` requires that benchmark **configuration** and benchmark **task
environments** be versioned independently. The behaviour exists, but the
acceptance baseline records two real gaps in the evidence:

  * `config.v1.json` and `environments.v1.json` are unhashed and untested, while
    the task and trace corpora are pinned — so a silent edit to a scoring weight
    or a container image would change published utility and regret with no test
    failure; and
  * the P5/P7 fixture pins are tautological (`first.corpus_sha256 ==
    digest(runner.tasks_path)` hashes the file it just read). Only P6 asserts
    literal digests.

This file closes both for the ACR-ARCH suite: every one of the four fixtures is
pinned to a literal digest, and the three version axes are each bumped alone in
a `tmp_path` copy of the corpus to show they move independently rather than
being one version wearing three names.

Per ADR3-M8-004 this is tests only. Surfacing the config and environment digests
on `BenchmarkRun` is deliberately deferred to v0.4: the row is an immutable
persisted `StrictModel` seeded on every API start, so adding fields would mean a
migration, an id-derivation change and an immutability-comparison change during
a release freeze.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from accretion.benchmark import AcrArchRunner

# The four frozen ACR-ARCH fixtures, pinned literally. `tasks` and `traces`
# repeat the pins in tests/test_acr_arch.py on purpose: this test is about the
# *set* being complete, and a pin that lives only in another file is a pin that
# can be deleted without this criterion noticing.
CONFIG_SHA256 = "c5fcc1a976b05e9770567fa625b0221a183ad8420bf2b1c09fdd1b230ef80466"
ENVIRONMENTS_SHA256 = "4740e816ab32f47e42ffbc56b3463ee8f89930e9ce2a09db2bb18dc789164983"
TASKS_SHA256 = "9251bb918912e73a2dade20189f93cc26cd7bc217a0dea03713ef252843b9dd7"
TRACES_SHA256 = "2f62f87eaf079914d41f47bea57a4dd04ce469d0e54f1d6a38faa6de0dd6f051"

BASELINE_CONFIGURATION_VERSION = "1.0.0"
BASELINE_SUITE_VERSION = "1.0.0"
BASELINE_ENVIRONMENT_BUNDLE_VERSION = "1.0.0"
BASELINE_SELECTOR_VERSION = "selector-v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_copy(tmp_path: Path, name: str) -> Path:
    """A writable copy of the frozen ACR-ARCH corpus."""
    root = tmp_path / name
    shutil.copytree(AcrArchRunner().root, root)
    return root


def rewrite(path: Path, mutate) -> None:
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


@pytest.mark.acceptance("V01-BENCH-005")
def test_every_frozen_benchmark_fixture_is_pinned_including_config_and_environments() -> None:
    """All four fixtures are hashed, not just the two the corpus pins today."""
    runner = AcrArchRunner()

    assert digest(runner.config_path) == CONFIG_SHA256
    assert digest(runner.environments_path) == ENVIRONMENTS_SHA256
    assert digest(runner.tasks_path) == TASKS_SHA256
    assert digest(runner.traces_path) == TRACES_SHA256

    # The pins must be literals, not a re-hash of the file under test. Guard
    # against the tautology the baseline records by checking the four digests
    # are pairwise distinct and none is the digest of an empty file.
    pins = {CONFIG_SHA256, ENVIRONMENTS_SHA256, TASKS_SHA256, TRACES_SHA256}
    assert len(pins) == 4
    assert hashlib.sha256(b"").hexdigest() not in pins

    # The versions those fixtures declare are the ones the run publishes.
    run, metrics = runner.replay()
    assert run.configuration_version == BASELINE_CONFIGURATION_VERSION
    assert run.suite_version == BASELINE_SUITE_VERSION
    assert {metric.selector_version for metric in metrics} == {BASELINE_SELECTOR_VERSION}
    environments = json.loads(runner.environments_path.read_text())
    assert environments["environment_bundle_version"] == BASELINE_ENVIRONMENT_BUNDLE_VERSION


@pytest.mark.acceptance("V01-BENCH-005")
def test_configuration_version_moves_without_the_environment_bundle(tmp_path: Path) -> None:
    """Axis 1: rescoring the suite changes the configuration version alone."""
    root = corpus_copy(tmp_path, "configuration")
    config_path = root / "config.v1.json"

    def bump(payload: dict) -> None:
        payload["configuration_version"] = "1.1.0"
        payload["risk_weight"] = 0.40

    rewrite(config_path, bump)

    run, metrics = AcrArchRunner(root=root).replay()

    assert run.configuration_version == "1.1.0"
    # Independent axes are untouched.
    assert run.suite_version == BASELINE_SUITE_VERSION
    assert digest(root / "environments.v1.json") == ENVIRONMENTS_SHA256
    assert digest(root / "tasks.v1.json") == TASKS_SHA256
    assert {metric.selector_version for metric in metrics} == {BASELINE_SELECTOR_VERSION}

    # A reweighting is a real change: the published numbers must move with it,
    # otherwise `configuration_version` would be decorative.
    baseline_run, baseline_metrics = AcrArchRunner().replay()
    assert run.corpus_sha256 == baseline_run.corpus_sha256
    assert run.trace_sha256 == baseline_run.trace_sha256
    by_id = {metric.metric_id: metric for metric in baseline_metrics}
    assert any(metric.utility != by_id[metric.metric_id].utility for metric in metrics)


@pytest.mark.acceptance("V01-BENCH-005")
def test_environment_bundle_version_moves_without_the_configuration(tmp_path: Path) -> None:
    """Axis 2: re-imaging the environments leaves scoring configuration alone."""
    root = corpus_copy(tmp_path, "environments")
    environments_path = root / "environments.v1.json"

    def bump(payload: dict) -> None:
        payload["environment_bundle_version"] = "2.0.0"
        for environment in payload["environments"]:
            environment["container_image"] = "python:3.12.11-slim@sha256:fixture-v2"

    rewrite(environments_path, bump)

    run, metrics = AcrArchRunner(root=root).replay()

    assert json.loads(environments_path.read_text())["environment_bundle_version"] == "2.0.0"
    # Independent axes are untouched, and so are the published numbers: a new
    # image for the same environment *version* does not rescore a replay.
    assert run.configuration_version == BASELINE_CONFIGURATION_VERSION
    assert run.suite_version == BASELINE_SUITE_VERSION
    assert digest(root / "config.v1.json") == CONFIG_SHA256
    assert {metric.selector_version for metric in metrics} == {BASELINE_SELECTOR_VERSION}

    baseline_run, baseline_metrics = AcrArchRunner().replay()
    assert run.benchmark_run_id == baseline_run.benchmark_run_id
    by_id = {metric.metric_id: metric for metric in baseline_metrics}
    assert all(metric.utility == by_id[metric.metric_id].utility for metric in metrics)


@pytest.mark.acceptance("V01-BENCH-005")
def test_selector_version_moves_without_configuration_or_environments(tmp_path: Path) -> None:
    """Axis 3: reversioning the selector leaves the other two axes alone."""
    root = corpus_copy(tmp_path, "selector")
    tasks_path = root / "tasks.v1.json"

    def bump(payload: dict) -> None:
        for task in payload["tasks"]:
            task["selector_version"] = "selector-v2"

    rewrite(tasks_path, bump)

    run, metrics = AcrArchRunner(root=root).replay()

    assert {metric.selector_version for metric in metrics} == {"selector-v2"}
    assert run.configuration_version == BASELINE_CONFIGURATION_VERSION
    assert digest(root / "config.v1.json") == CONFIG_SHA256
    assert digest(root / "environments.v1.json") == ENVIRONMENTS_SHA256

    # A different corpus is a different run, even when the traces are identical.
    baseline_run = AcrArchRunner().replay()[0]
    assert run.corpus_sha256 != baseline_run.corpus_sha256
    assert run.trace_sha256 == baseline_run.trace_sha256
    assert run.benchmark_run_id != baseline_run.benchmark_run_id
    assert len(metrics) == baseline_run.scenario_count


@pytest.mark.acceptance("V01-BENCH-005")
def test_a_task_pinned_to_an_unpublished_environment_version_is_rejected(
    tmp_path: Path,
) -> None:
    """The two axes are cross-checked: independence is not permission to drift.

    A task may only reference an (environment_id, version) pair the bundle
    actually publishes, so bumping one axis without the other is caught rather
    than silently replayed against an environment that does not exist.
    """
    root = corpus_copy(tmp_path, "task-drift")

    def bump_task_environment(payload: dict) -> None:
        payload["tasks"][0]["environment_version"] = "9.9.9"

    rewrite(root / "tasks.v1.json", bump_task_environment)

    with pytest.raises(ValueError, match="unknown environment"):
        AcrArchRunner(root=root).replay()

    # The mirror case: the bundle moves the version out from under a task that
    # still pins the old one.
    root = corpus_copy(tmp_path, "bundle-drift")

    def bump_environment_versions(payload: dict) -> None:
        payload["environment_bundle_version"] = "2.0.0"
        for environment in payload["environments"]:
            environment["version"] = "2.0.0"

    rewrite(root / "environments.v1.json", bump_environment_versions)

    with pytest.raises(ValueError, match="unknown environment"):
        AcrArchRunner(root=root).replay()
