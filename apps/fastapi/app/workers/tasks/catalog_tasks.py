"""
Catalog and model-cache jobs:

* ``hash_local_version`` (Celery, queue ``catalog``) — sha256 of local model
  files that have none yet (``CatalogService.compute_local_hashes``).
* ``model_cache.lru_sweep`` (APScheduler) — evict least-recently-used,
  unpinned, unused versions on nodes above the cache high-water mark.
"""

import asyncio

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "catalog"


async def _hash_local_version(version_id: str) -> int:
    import uuid

    from app.db.postgres.connection import get_session_maker
    from app.services.catalog.service import CatalogService

    async with get_session_maker()() as session:
        hashed = await CatalogService.for_session(session).compute_local_hashes(
            uuid.UUID(version_id)
        )
        await session.commit()
    return hashed


@celery_app.task(
    name="app.workers.tasks.catalog_tasks.hash_local_version",
    base=BaseTaskWithRetry,
    bind=True,
)
def hash_local_version(self, version_id: str) -> int:
    """Hash the files of one local model version."""
    return run_async(_hash_local_version(version_id))


def enqueue_hash_local_version(version_id: str) -> None:
    """Queue local hashing. Never raises."""
    try:
        hash_local_version.apply_async(kwargs={"version_id": version_id}, queue=QUEUE)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_hash_local_version failed: {exc}")


async def _lru_sweep() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.models.generated_models import Nodes
    from app.services.model_cache.service import ModelCacheService
    from sqlalchemy import select

    swept = 0
    async with get_session_maker()() as session:
        node_ids = list(
            (
                await session.execute(
                    select(Nodes.id).where(
                        Nodes.deleted_at.is_(None),
                        Nodes.status == "online",
                        Nodes.cache_limit_bytes.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        svc = ModelCacheService.for_session(session)
        for node_id in node_ids:
            try:
                if await svc.lru_sweep(node_id):
                    swept += 1
            except Exception as exc:  # noqa: BLE001 - one node must not stop the sweep
                logger.warning(f"cache sweep failed for node {node_id}: {exc}")
        await session.commit()
    return swept


@scheduled_job(
    "model_cache.lru_sweep",
    "interval",
    minutes=settings.MODEL_CACHE_LRU_SWEEP_INTERVAL_MINUTES,
    job_opts={"max_instances": 1, "coalesce": True},
)
def model_cache_lru_sweep() -> None:
    """Evict LRU cache entries on nodes over the high-water mark."""
    try:
        swept = run_async(_lru_sweep())
        if swept:
            logger.info(f"model cache LRU sweep sent evictions to {swept} node(s)")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"model cache sweep failed: {exc}")


async def _apply_node_hashes(version_id: str, command_id: str) -> int:
    """Wait for a node ``hash_files`` command and store its sha256 values."""
    import uuid

    from app.db.postgres.connection import get_session_maker
    from app.services.agent_hub.service import AgentHub
    from app.services.catalog.service import CatalogService

    outcome = await AgentHub.wait_for(uuid.UUID(command_id), 6 * 3600)
    vid = uuid.UUID(version_id)
    # Registration sends hash_files before its own transaction commits; a fast
    # node can answer first, so wait (briefly) until the version is visible.
    for _ in range(30):
        async with get_session_maker()() as session:
            if await CatalogService.for_session(session).versions.get_active(vid) is not None:
                break
        await asyncio.sleep(1)
    async with get_session_maker()() as session:
        svc = CatalogService.for_session(session)
        files = await svc.files.list_for_version(vid)
        if outcome.state != "succeeded":
            await svc.versions.set_status(
                vid, "missing", outcome.error_message or f"hash_files {outcome.state}"
            )
            await session.commit()
            return 0
        mismatches = await svc.apply_node_hashes(files, outcome.result or {})
        if mismatches:
            await svc.versions.set_status(
                vid,
                "blocked",
                f"{len(mismatches)} file(s) missing or changed on the node",
            )
        else:
            await svc.versions.set_status(vid, "ready", None)
        await session.commit()
    return len(files)


@celery_app.task(
    name="app.workers.tasks.catalog_tasks.apply_node_hashes",
    base=BaseTaskWithRetry,
    bind=True,
)
def apply_node_hashes(self, version_id: str, command_id: str) -> int:
    """Apply the result of a node-side hash_files command to a model version."""
    return run_async(_apply_node_hashes(version_id, command_id))


def enqueue_apply_node_hashes(version_id: str, command_id: str) -> None:
    """Queue the node hash follow-up. Never raises."""
    try:
        apply_node_hashes.apply_async(
            kwargs={"version_id": version_id, "command_id": command_id}, queue=QUEUE
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_apply_node_hashes failed: {exc}")
