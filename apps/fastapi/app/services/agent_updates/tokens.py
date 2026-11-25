"""
Download tokens for staged agent updates (agent protocol §14).

``stage_update`` carries a random token bound to one node, version and target; the hub serves
``GET /agent/v1/updates/<version>/<target>`` only with it (``X-GPUDeck-Update-Token``). Only
the sha256 of the token is stored, in Redis under ``gpu-deck:agent_updates:token:<sha256>``,
with a TTL of ``AGENT_UPDATE_TOKEN_TTL_SECONDS``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.redis.client import get_redis, redis_key

logger = get_logger(__name__)

TOKEN_PREFIX = "gdu_"
HEADER = "x-gpudeck-update-token"


def _key(token: str) -> str:
    return redis_key("agent_updates", "token", hashlib.sha256(token.encode()).hexdigest())


async def issue(node_id: uuid.UUID, version: str, target: str) -> str:
    token = TOKEN_PREFIX + secrets.token_hex(24)
    client = await get_redis()
    await client.set(
        _key(token),
        json.dumps({"node_id": str(node_id), "version": version, "target": target}),
        ex=int(settings.AGENT_UPDATE_TOKEN_TTL_SECONDS),
    )
    return token


async def check(token: Optional[str], version: str, target: str) -> Optional[uuid.UUID]:
    """The node the token was issued to, when it matches ``version`` / ``target``."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
        return None
    try:
        client = await get_redis()
        raw = await client.get(_key(token))
    except Exception as exc:  # noqa: BLE001 - Redis down: no download
        logger.warning(f"update token lookup failed: {exc}")
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if data.get("version") != version or data.get("target") != target:
        return None
    try:
        return uuid.UUID(str(data.get("node_id")))
    except ValueError:
        return None
