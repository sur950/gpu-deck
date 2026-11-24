"""
Settings aggregation: every app/core/settings/<domain>.py is merged into one
Settings class, env names are UPPER_SNAKE, and the Redis key prefix helper
honours the shared-Redis ACL.
"""

import pytest
from app.core.settings import Settings, discover_settings_classes, settings
from app.core.settings.base import DomainSettings


@pytest.mark.unit
def test_every_domain_module_contributes_a_class():
    names = {cls.__name__ for cls in discover_settings_classes()}
    assert {
        "AppSettings",
        "DatabaseSettings",
        "RedisSettings",
        "SecuritySettings",
        "BrandingSettings",
        "RequestLimitSettings",
        "ObservabilitySettings",
    } <= names
    assert all(issubclass(cls, DomainSettings) for cls in discover_settings_classes())


@pytest.mark.unit
def test_merged_settings_exposes_fields_from_all_domains():
    for field in (
        "APP_NAME",
        "POSTGRES_DB",
        "REDIS_KEY_PREFIX",
        "SECRET_KEY",
        "BRAND_NAME",
    ):
        assert field in Settings.model_fields
    assert settings.API_V1_PREFIX == "/api/v1"


@pytest.mark.unit
def test_field_names_are_upper_snake():
    for name in Settings.model_fields:
        assert name == name.upper(), name
        assert " " not in name and "-" not in name


@pytest.mark.unit
def test_redis_key_helper_uses_prefix():
    key = settings.redis_key("session", "abc")
    assert key.startswith(settings.REDIS_KEY_PREFIX)
    assert key == f"{settings.REDIS_KEY_PREFIX}session:abc"


@pytest.mark.unit
def test_database_urls_use_the_right_drivers():
    assert settings.get_database_url().startswith("postgresql+asyncpg://")
    assert settings.get_sync_database_url().startswith("postgresql+psycopg://")


@pytest.mark.unit
def test_empty_optional_env_values_become_none(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "")
    fresh = Settings()
    assert fresh.SESSION_COOKIE_DOMAIN is None
    assert fresh.SESSION_COOKIE_SECURE is None


@pytest.mark.unit
def test_placeholder_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "CHANGE-ME-min-32-chars-random-hex-xxxxxxxxx")
    with pytest.raises(Exception):
        Settings()
