"""
Database models package.

``generated_models.py`` is produced by ``app/db/postgres/scripts/generate_models.sh``
(sqlacodegen against the migrated database) and is committed — never hand-edit
it, and regenerate it after every migration.

Import model classes by module path so this file stays untouched when
domains are added::

    from app.db.postgres.models.generated_models import Nodes
"""

try:  # pragma: no cover - depends on a generated file
    from app.db.postgres.models import generated_models  # noqa: F401

    GENERATED_MODELS_AVAILABLE = True
except ImportError:
    GENERATED_MODELS_AVAILABLE = False

__all__ = ["GENERATED_MODELS_AVAILABLE"]
