"""
Guard: every ``Depends(get_db ...)`` uses ``scope="function"``, so the request's transaction
commits before the response is sent (see ``app.db.postgres.connection.get_db``).
"""

import re
from pathlib import Path

from app.core.logger import get_logger

logger = get_logger(__name__)

APP_DIR = Path(__file__).resolve().parents[2] / "app"
DEPENDS_GET_DB = re.compile(r"Depends\(\s*get_db\b([^)]*)\)")


def test_get_db_dependencies_commit_before_the_response():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        for match in DEPENDS_GET_DB.finditer(path.read_text(encoding="utf-8")):
            if 'scope="function"' not in match.group(1):
                offenders.append(f"{path.relative_to(APP_DIR.parent)}: {match.group(0)}")
    assert not offenders, 'use Depends(get_db, scope="function"):\n' + "\n".join(offenders)
