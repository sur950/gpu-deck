"""
Unit test configuration and fixtures (mocks only, no network).
"""

from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.add = Mock()
    session.add_all = Mock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    return session


@pytest.fixture
def mock_repository():
    repo = AsyncMock()
    repo.create = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_field = AsyncMock()
    repo.get_all = AsyncMock(return_value=[])
    repo.update = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    repo.count = AsyncMock(return_value=0)
    repo.release_connection = AsyncMock()
    return repo


@pytest.fixture
def mock_cache():
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=True)
    cache.delete = AsyncMock(return_value=True)
    cache.exists = AsyncMock(return_value=False)
    cache.incr = AsyncMock(return_value=1)
    return cache
