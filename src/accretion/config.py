from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    opencode_command: str = "opencode"
    opencode_model: str = "opencode/x-preview-f-free"
    enable_live_providers: bool = False
    auto_resume_on_reconcile: bool = True
    operator_identity: str = "local-operator"
    capability_policy_id: str = "local-capability-policy"
    granted_permissions: list[str] = Field(default_factory=list)
    credential_env_map: dict[str, str] = Field(default_factory=dict)
    # Base64 32-byte master key for the token broker's envelope encryption. Kept
    # outside PostgreSQL (SDD 13.3); empty means the broker cannot store credentials.
    token_encryption_key: str = ""
    auth_mode: Literal["LOCAL_PRINCIPAL", "OIDC"] = "LOCAL_PRINCIPAL"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_url: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_scopes: str = "openid profile email"
    session_cookie_name: str = "accretion_session"
    session_ttl_seconds: int = 28_800
    enable_dynamic_workflows: bool = False
    enable_candidate_search: bool = False
    enable_experience_retrieval: bool = False

    @property
    def worktree_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
