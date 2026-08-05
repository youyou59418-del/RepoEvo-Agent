"""Shared environment-backed settings for API and Worker processes."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RepoEvoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    repoevo_data_root: Path = Path(".artifacts/runtime")
    repoevo_artifact_root: Path = Path(".artifacts/files")
    checkpoint_dsn: str | None = None
    redis_url: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    worker_id: str = "worker-1"

    @field_validator("checkpoint_dsn", "redis_url", mode="before")
    @classmethod
    def normalize_blank_optional_service_urls(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value
