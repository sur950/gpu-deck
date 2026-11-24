"""
Read-only access to the latest-sample cache (``app.services.metrics.live_cache``)
for deployment views: observed GPU memory (scheduler), engine samples (routing table,
instance ``live``) and per-slot sessions. Tolerates both the hash API
(``get_many(ids, kind=...)``) and the older per-key API; returns nothing when
the metrics package or Redis is unavailable.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Iterable, List, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)


def _module() -> Any:
    try:
        from app.services.metrics import live_cache

        return live_cache
    except Exception:  # noqa: BLE001 - metrics package absent
        return None


async def samples(kind: str, ids: Iterable[Any]) -> List[Optional[Dict[str, Any]]]:
    """Latest samples of ``kind`` (gpu | engine | sessions | node) for ``ids``, in order."""
    ids = [str(i) for i in ids]
    lc = _module()
    if lc is None or not ids:
        return [None] * len(ids)
    try:
        if "kind" in inspect.signature(lc.get_many).parameters:
            return list(await lc.get_many(ids, kind=kind))
        key_fn = getattr(lc, f"{kind}_key")
        return list(await lc.get_many([key_fn(i) for i in ids]))
    except Exception as exc:  # noqa: BLE001 - live data is best effort
        logger.debug(f"live {kind} samples unavailable: {exc}")
        return [None] * len(ids)


def is_fresh(sample: Optional[Dict[str, Any]]) -> bool:
    lc = _module()
    if lc is None or not sample:
        return False
    try:
        return bool(lc.is_fresh(sample))
    except Exception:  # noqa: BLE001
        return False


def instance_live(
    sample: Optional[Dict[str, Any]], rollup: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    lc = _module()
    if lc is None:
        return {}
    try:
        return dict(lc.instance_live(sample, rollup))
    except Exception:  # noqa: BLE001
        return {}
