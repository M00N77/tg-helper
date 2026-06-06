"""Проверка конфигурации: переменные из .env загружаются корректно."""

from src.config import Settings, settings


def test_settings_singleton():
    assert settings is not None
    assert isinstance(settings, Settings)


def test_bot_token_present():
    assert settings.bot_token is not None
    assert len(settings.bot_token) > 0
    assert ":" in settings.bot_token


def test_owner_telegram_id_present():
    assert settings.owner_telegram_id is not None
    assert isinstance(settings.owner_telegram_id, int)
    assert settings.owner_telegram_id > 0


def test_encryption_key_present():
    assert settings.encryption_key is not None
    assert len(settings.encryption_key) > 0
    from cryptography.fernet import Fernet
    Fernet(settings.encryption_key.encode())


def test_database_url_present():
    assert settings.database_url is not None
    assert "postgresql+asyncpg://" in settings.database_url
    assert "@" in settings.database_url


def test_data_dir():
    path = settings.data_dir
    assert path.exists()
    assert path.is_dir()
