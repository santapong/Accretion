"""The frozen document import must fail closed on mutation or added payloads."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mutation", ["none", "content", "missing", "extra", "manifest"])
def test_frozen_package_integrity(tmp_path: Path, mutation: str) -> None:
    validator = load_script("validate_future_sdd_package")
    package = tmp_path / "package"
    shutil.copytree(validator.PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
    if mutation == "content":
        (package / "START_HERE.md").write_text("Changed design scope\n")
    elif mutation == "missing":
        (package / "START_HERE.md").unlink()
    elif mutation == "extra":
        (package / "unreviewed-proposal.md").write_text("Unregistered addition\n")
    elif mutation == "manifest":
        (package / "MANIFEST.sha256").write_text("")
    if mutation == "none":
        assert validator.validate_package(package) == 187
    else:
        with pytest.raises((ValueError, OSError)):
            validator.validate_package(package)


def test_historical_link_exception_requires_exact_bytes(tmp_path: Path, monkeypatch) -> None:
    checker = load_script("check_docs")
    original, digest = next(iter(checker.FROZEN_REFERENCE_DOCUMENTS.items()))
    docs = tmp_path / "docs"
    docs.mkdir()
    hub = docs / "README.md"
    hub.write_text("# Docs\n")
    reference = tmp_path / "historical.md"
    reference.write_bytes(original.read_bytes())
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "DOCS", docs)
    monkeypatch.setattr(checker, "FROZEN_REFERENCE_DOCUMENTS", {reference: digest})
    assert checker.validate_markdown([hub, reference]) == []
    reference.write_bytes(reference.read_bytes() + b"\nChanged\n")
    assert any("identity changed" in error for error in checker.validate_markdown([hub, reference]))
    hub.write_text("[Missing live document](missing.md)\n")
    assert any("missing local target" in error for error in checker.validate_markdown([hub]))
