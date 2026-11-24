"""
Root test configuration and fixtures.

No SQLite: the schema relies on Postgres-only types (JSONB, enums, partitions),
so ``get_db`` is never overridden. Unit tests must not touch the network;
integration tests use the real local Postgres/Redis from apps/fastapi/.env
(the ones bootstrap.dev.sh sets up) and are marked ``@pytest.mark.integration``.
"""

import os
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from faker import Faker
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("APP_ENV", "test")

from app.api_layer_main import app as serving_app  # noqa: E402
from app.core.settings import settings  # noqa: E402
from app.main import app as admin_app  # noqa: E402

fake = Faker()


@pytest.fixture(scope="session")
def app_settings():
    return settings


# ==========================================
# FastAPI client fixtures (lifespan NOT run: no DB/Redis needed for routing tests)
# ==========================================


@pytest.fixture(scope="function")
def test_client() -> Generator[TestClient, None, None]:
    """Sync client for the admin API."""
    client = TestClient(admin_app)
    yield client
    admin_app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def serving_client() -> Generator[TestClient, None, None]:
    """Sync client for the serving API."""
    client = TestClient(serving_app)
    yield client
    serving_app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async client for the admin API."""
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="http://test"
    ) as client:
        yield client
    admin_app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def async_serving_client() -> AsyncGenerator[AsyncClient, None]:
    """Async client for the serving API."""
    async with AsyncClient(
        transport=ASGITransport(app=serving_app), base_url="http://test"
    ) as client:
        yield client
    serving_app.dependency_overrides.clear()


# ==========================================
# Mock fixtures
# ==========================================


@pytest.fixture
def mock_redis():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=True)
    redis_mock.exists = AsyncMock(return_value=False)
    redis_mock.setex = AsyncMock(return_value=True)
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.ping = AsyncMock(return_value=True)
    return redis_mock


@pytest.fixture
def mock_celery():
    celery_mock = MagicMock()
    celery_mock.send_task = MagicMock(return_value=MagicMock(id="mock-task-id"))
    return celery_mock


@pytest.fixture
def faker_instance():
    return fake


@pytest.fixture(autouse=True)
def reset_app_state():
    yield
    admin_app.dependency_overrides.clear()
    serving_app.dependency_overrides.clear()
