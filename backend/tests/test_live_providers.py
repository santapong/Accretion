from __future__ import annotations

import os
from pathlib import Path

import pytest
from accretion.config import Settings
from accretion.service import AccretionService


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("ACCRETION_RUN_LIVE_TESTS") != "1",
    reason="set ACCRETION_RUN_LIVE_TESTS=1 to inspect installed provider CLIs",
)
async def test_installed_provider_health_and_history(tmp_path: Path) -> None:
    service = AccretionService(Settings(data_dir=tmp_path / "data", workspace_roots=[Path.home()]))
    try:
        await service.initialize()
        health = await service.provider_health()
        assert all(provider.available for provider in health)
        assert await service.list_sessions(limit=1)
    finally:
        await service.close()
