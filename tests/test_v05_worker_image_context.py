"""Build-context file admission checks; no Docker build or optional simulator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[1] / "scripts/robotics/prepare_worker_image.py"
    spec = importlib.util.spec_from_file_location("worker_image_context", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_context_rejects_symbolic_files_and_symbolic_parent(builder, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "mesh.obj").write_bytes(b"mesh")
    (tmp_path / "alias").symlink_to(actual, target_is_directory=True)
    (tmp_path / "mesh.obj").symlink_to(actual / "mesh.obj")
    assert builder.read_regular(tmp_path, "actual/mesh.obj") == b"mesh"
    for name in ("alias/mesh.obj", "mesh.obj", "../mesh.obj", str(actual / "mesh.obj")):
        with pytest.raises(ValueError):
            builder.read_regular(tmp_path, name)


def test_existing_context_is_never_overwritten(builder, tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    evidence = context / "earlier-output"
    evidence.write_bytes(b"retained")
    with pytest.raises(ValueError, match="new directory"):
        builder.prepare(tmp_path, tmp_path, context)
    assert evidence.read_bytes() == b"retained"


def test_context_rejects_changed_model_without_copying_other_workspace_files(
    builder, tmp_path, monkeypatch
):
    root = tmp_path / "source"
    root.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    package = root / "src/accretion/robotics/adapters"
    package.mkdir(parents=True)
    manifest = package / "ur5e-model-files.json"
    manifest.write_text('{"model.xml": "' + "0" * 64 + '"}')
    (models / "model.xml").write_bytes(b"changed")
    for name in ("LICENSE", "pyproject.toml", "uv.lock"):
        (root / name).write_text("source")
    (root / ".env").write_text("must never be copied")
    relative = str(manifest.relative_to(root))
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *args, **kwargs: (relative + "\0").encode()
    )
    output = tmp_path / "context"
    with pytest.raises(ValueError, match="model digest mismatch"):
        builder.prepare(root, models, output)
    assert not list(output.iterdir())


def test_wave2_context_needs_only_ur5e_inventory_and_copies_only_its_models(
    builder, tmp_path, monkeypatch
):
    import hashlib
    import json

    root, models = tmp_path / "source", tmp_path / "models"
    root.mkdir()
    models.mkdir()
    package = root / "src/accretion/robotics/adapters"
    package.mkdir(parents=True)
    model = b"UR5E model construction fixture"
    digest = hashlib.sha256(model).hexdigest()
    (models / "ur5e.xml").write_bytes(model)
    (models / "panda.xml").write_bytes(b"parked model must not enter this context")
    manifest = package / "ur5e-model-files.json"
    manifest.write_text(json.dumps({"ur5e.xml": digest}))
    # The focused Wave2 source tree has no Panda module or manifest.
    for name in ("LICENSE", "pyproject.toml", "uv.lock"):
        (root / name).write_text("construction source")
    image = root / "scripts/robotics/image"
    image.mkdir(parents=True)
    for name in ("Dockerfile", "simulation-worker"):
        (image / name).write_text("construction recipe")
    (root / ".env").write_text("unrelated workspace data")

    def output(command, **kwargs):
        if command[:2] == ["git", "ls-files"]:
            return (str(manifest.relative_to(root)) + "\0").encode()
        if command[:2] == ["git", "rev-parse"]:
            return ("0" * 40 + "\n").encode()
        assert command[:2] == ["uv", "export"]
        return b"# injected locked-export construction fixture\n"

    monkeypatch.setattr(builder.subprocess, "check_output", output)
    destination = tmp_path / "context"
    result = builder.prepare(root, models, destination)
    assert result["worker_kind"] == "UR5E"
    assert result["model_files"] == {"ur5e.xml": digest}
    assert (destination / "models/ur5e.xml").read_bytes() == model
    assert not (destination / "models/panda.xml").exists()
    assert not (destination / ".env").exists()
    assert json.loads((destination / "source-manifest.json").read_text()) == result
