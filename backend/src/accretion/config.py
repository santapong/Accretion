from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_workspace_roots() -> list[Path]:
    roots = [Path.home(), Path.cwd()]
    return list(dict.fromkeys(path.resolve() for path in roots))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACCRETION_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    data_dir: Path = Field(default_factory=lambda: user_data_path("accretion", ensure_exists=False))
    workspace_roots: list[Path] = Field(default_factory=default_workspace_roots)
    codex_command: str = "codex"
    claude_projects_dir: Path = Field(default_factory=lambda: Path.home() / ".claude" / "projects")
    frontend_dist: Path | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "accretion.db"

    def normalized_roots(self) -> list[Path]:
        return [root.expanduser().resolve() for root in self.workspace_roots]

    def validate_workspace(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Workspace does not exist or is not a directory: {path}")
        if not any(path.is_relative_to(root) for root in self.normalized_roots()):
            allowed = ", ".join(str(root) for root in self.normalized_roots())
            raise ValueError(f"Workspace is outside configured roots ({allowed})")
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
