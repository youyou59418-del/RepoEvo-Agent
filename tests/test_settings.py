from __future__ import annotations

import pytest

from repoevo.settings import RepoEvoSettings


def test_settings_use_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOEVO_DATA_ROOT", raising=False)
    monkeypatch.delenv("CHECKPOINT_DSN", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = RepoEvoSettings(_env_file=None)
    assert str(settings.repoevo_data_root) == ".artifacts/runtime"
    assert settings.redis_url is None


def test_blank_optional_service_urls_use_sqlite_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHECKPOINT_DSN", "  ")
    monkeypatch.setenv("REDIS_URL", "")

    settings = RepoEvoSettings(_env_file=None)

    assert settings.checkpoint_dsn is None
    assert settings.redis_url is None
