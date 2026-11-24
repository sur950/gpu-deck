"""
PostgreSQL repositories package.

One ``<entity>_repository.py`` per table, each subclassing BaseRepository.
Domains import their repositories by module path
(``from app.db.postgres.repositories.node_repository import NodeRepository``);
do not add re-exports here, so adding a repository never touches this file.
"""

from app.db.postgres.repositories.base_repository import BaseRepository

__all__ = ["BaseRepository"]
