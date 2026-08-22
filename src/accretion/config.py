from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ACCRETION_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://accretion:accretion@localhost:5432/accretion"
    data_dir: Path = Path(".accretion")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    global_max_runs: int = 4
    provider_max_runs: int = 2
    project_max_runs: int = 2
    codex_command: str = "codex"
    claude_command: str = "claude"
    enable_live_providers: bool = False
    auto_resume_on_reconcile: bool = True
    operator_identity: str = "local-operator"
    capability_policy_id: str = "local-capability-policy"
    granted_permissions: list[str] = Field(default_factory=list)
    credential_env_map: dict[str, str] = Field(default_factory=dict)
    enable_dynamic_workflows: bool = False

    @property
    def worktree_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
