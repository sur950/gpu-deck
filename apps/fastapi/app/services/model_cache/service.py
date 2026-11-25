"""
Warm model cache service: per-node cache view, prefetch (also for upcoming
layouts via ``prefetch(node, model_version)``), eviction by last use, pin/unpin,
and the inventory-driven state refresh.

Commands go through the agent hub port (``prefetch_model`` / ``evict_cache``);
``node_model_cache`` rows mirror the agent's view and are reconciled from the
``inventory`` message (``apply_inventory``) and ``cache_changed`` events.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import (
    ModelFiles,
    Models,
    ModelVersions,
    Nodes,
)
from app.db.postgres.repositories.model_repository import (
    ModelFileRepository,
    ModelRepository,
    ModelVersionRepository,
)
from app.db.postgres.repositories.node_model_cache_repository import (
    NodeModelCacheRepository,
)
from app.schemas.model_cache_schema import (
    CacheItem,
    CacheSummary,
    CommandRefs,
    EvictRequest,
    PrefetchRequest,
)
from app.services.catalog.secrets import model_credential_id, resolve_source_token
from app.services.deployments.agent_hub_port import AgentHubPort, get_agent_hub
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class ModelCacheService:
    """Model cache views and agent cache commands."""

    def __init__(
        self,
        session: AsyncSession,
        cache_repo: NodeModelCacheRepository,
        models: ModelRepository,
        versions: ModelVersionRepository,
        files: ModelFileRepository,
        hub: Optional[AgentHubPort] = None,
    ) -> None:
        self.session = session
        self.cache_repo = cache_repo
        self.models = models
        self.versions = versions
        self.files = files
        self._hub = hub

    @property
    def hub(self) -> AgentHubPort:
        if self._hub is None:
            self._hub = get_agent_hub(self.session)
        return self._hub

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "ModelCacheService":
        return ModelCacheService.for_session(db)

    @classmethod
    def for_session(
        cls, db: AsyncSession, hub: Optional[AgentHubPort] = None
    ) -> "ModelCacheService":
        return cls(
            db,
            NodeModelCacheRepository(db),
            ModelRepository(db),
            ModelVersionRepository(db),
            ModelFileRepository(db),
            hub,
        )

    # ------------------------------------------------------------------ views

    async def list_items(self, **filters) -> Tuple[List[CacheItem], int]:
        rows, total = await self.cache_repo.list_items(**filters)
        return [CacheItem.model_validate(r) for r in rows], total

    async def summary(self, cluster_id: Optional[uuid.UUID] = None) -> List[CacheSummary]:
        rows = await self.cache_repo.summary_rows(cluster_id)
        return [CacheSummary.model_validate(r) for r in rows]

    async def get_item(self, item_id: uuid.UUID) -> CacheItem:
        row = await self.cache_repo.get_by_id(item_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cache item")
        items, _ = await self.cache_repo.list_items(
            page=1,
            page_size=1000,
            node_id=row.node_id,
            model_version_id=row.model_version_id,
        )
        for item in items:
            if item["id"] == item_id:
                return CacheItem.model_validate(item)
        raise ResourceNotFoundError(resource_type="Cache item")

    # --------------------------------------------------------------- prefetch

    async def prefetch_many(self, req: PrefetchRequest) -> CommandRefs:
        ids = []
        for node_id in req.node_ids:
            cmd = await self.prefetch(
                node_id, req.model_version_id, pin=req.pin, pin_reason=req.pin_reason
            )
            if cmd:
                ids.append(cmd)
        return CommandRefs(command_ids=ids)

    async def prefetch(
        self,
        node: "uuid.UUID | Nodes",
        model_version: "uuid.UUID | ModelVersions",
        *,
        pin: bool = False,
        pin_reason: Optional[str] = None,
    ) -> Optional[uuid.UUID]:
        """
        Queue a download of ``model_version`` on ``node`` (the swap manager calls this for
        upcoming layouts). Returns the command id, or None when every file is
        already ready on the node (pin flag still applied).
        """
        node_row = node if isinstance(node, Nodes) else await self.cache_repo.get_node(node)
        if node_row is None:
            raise ResourceNotFoundError(resource_type="Node")
        version = (
            model_version
            if isinstance(model_version, ModelVersions)
            else await self.versions.get_active(model_version)
        )
        if version is None:
            raise ResourceNotFoundError(resource_type="Model version")
        if version.status == "blocked":
            raise ConflictError("Model version is blocked (hash mismatch or pickle policy)")
        model = await self.models.get_active(version.model_id)
        if model is None:
            raise ResourceNotFoundError(resource_type="Model")
        files = await self.files.list_for_version(version.id)
        required = [f for f in files if f.is_required]
        if not required:
            raise ValidationError("Model version has no files to prefetch")
        cache_dir = node_row.cache_dir or _default_cache_dir(node_row)
        ready = await self.cache_repo.ready_file_ids(node_row.id, version.id)
        await self.cache_repo.upsert_queued(
            node_row.id, version.id, required, cache_dir, pin, pin_reason
        )
        missing = [f for f in required if f.id not in ready]
        if not missing:
            if pin:
                await self.cache_repo.set_pinned_for_version(
                    node_row.id, version.id, True, pin_reason
                )
            return None
        source = await self.model_source(model, version)
        args = {
            "model_version_id": str(version.id),
            "files": [file_ref(f) for f in missing],
            "cache_dir": cache_dir,
            "source": source,
            "pin": pin,
            "pin_reason": pin_reason,
            "allow_pickle": bool(model.allow_pickle),
            "max_bandwidth_bytes_per_s": None,
        }
        await self.cache_repo.release_connection()
        cmd_id = await self.hub.send_command(node_row.id, "prefetch_model", args)
        logger.info(
            f"prefetch queued node={node_row.name} model={model.name} version={version.version} "
            f"files={len(missing)} command={cmd_id}"
        )
        return cmd_id

    async def model_source(self, model: Models, version: ModelVersions) -> Optional[Dict[str, Any]]:
        """``ModelSource`` for commands (token resolved from the vault, never logged)."""
        if model.source == "local_path":
            return {
                "kind": "local_path",
                "repo_id": (version.config or {}).get("local_root") or model.repo_id,
                "revision": None,
                "endpoint": None,
                "token": None,
            }
        # The model's own token credential (metadata.credential_id), else the default token.
        try:
            token = await resolve_source_token(
                self.session,
                model.source,
                model_credential_id(model),
                purpose="model download",
            )
        except Exception as exc:  # noqa: BLE001 - expired / revoked pick: fall back
            logger.warning(f"model {model.name}: token credential unusable ({exc}); default token")
            token = await resolve_source_token(self.session, model.source, purpose="model download")
        if model.source == "huggingface":
            endpoint = settings.HF_ENDPOINT
        else:
            endpoint = settings.MODELSCOPE_ENDPOINT
        return {
            "kind": model.source,
            "repo_id": model.repo_id,
            "revision": version.revision,
            "endpoint": endpoint,
            "token": token,
        }

    # ------------------------------------------------------------------ evict

    async def evict(self, req: EvictRequest) -> CommandRefs:
        selectors = [
            req.model_version_id is not None,
            bool(req.cache_item_ids),
            req.unpinned_older_than_seconds is not None,
            req.target_free_bytes is not None,
        ]
        if sum(selectors) != 1:
            raise ValidationError(
                "Exactly one of model_version_id, cache_item_ids, unpinned_older_than_seconds, "
                "target_free_bytes is required"
            )
        ids = []
        for node_id in req.node_ids:
            node = await self.cache_repo.get_node(node_id)
            if node is None:
                raise ResourceNotFoundError(resource_type="Node")
            args: Dict[str, Any] = {"unpin": req.unpin_only, "force": req.force}
            instance_id = None
            if req.model_version_id is not None:
                if not req.force and not req.unpin_only:
                    in_use = await self.cache_repo.in_use_versions([node_id])
                    if (node_id, req.model_version_id) in in_use:
                        raise ConflictError("CACHE_IN_USE: a running instance uses this version")
                args["model_version_id"] = str(req.model_version_id)
                if req.unpin_only:
                    await self.cache_repo.set_pinned_for_version(
                        node_id, req.model_version_id, False, None
                    )
                else:
                    await self.cache_repo.set_state_for_version(
                        node_id, req.model_version_id, "evicting"
                    )
            elif req.cache_item_ids:
                rows = [await self.cache_repo.get_by_id(i) for i in req.cache_item_ids]
                rows = [r for r in rows if r is not None and r.node_id == node_id]
                if not rows:
                    continue
                args["model_file_ids"] = [str(r.model_file_id) for r in rows]
                if req.unpin_only:
                    for r in rows:
                        await self.cache_repo.set_pinned(r.id, False, None)
                else:
                    await self.cache_repo.set_state_for_ids([r.id for r in rows], "evicting")
            elif req.unpinned_older_than_seconds is not None:
                args["unpinned_older_than_seconds"] = req.unpinned_older_than_seconds
            else:
                args["target_free_bytes"] = req.target_free_bytes
            await self.cache_repo.release_connection()
            ids.append(
                await self.hub.send_command(node_id, "evict_cache", args, instance_id=instance_id)
            )
        return CommandRefs(command_ids=ids)

    async def evict_item(self, item_id: uuid.UUID, force: bool = False) -> CommandRefs:
        row = await self.cache_repo.get_by_id(item_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cache item")
        if not force:
            in_use = await self.cache_repo.in_use_versions([row.node_id])
            if (row.node_id, row.model_version_id) in in_use:
                raise ConflictError("CACHE_IN_USE: a running instance uses this file")
        await self.cache_repo.set_state_for_ids([row.id], "evicting")
        await self.cache_repo.release_connection()
        cmd = await self.hub.send_command(
            row.node_id,
            "evict_cache",
            {
                "model_file_ids": [str(row.model_file_id)],
                "force": force,
                "unpin": False,
            },
        )
        return CommandRefs(command_ids=[cmd])

    async def evict_version_everywhere(
        self, model_version_id: uuid.UUID, force: bool = True
    ) -> List[uuid.UUID]:
        nodes = await self.cache_repo.nodes_with_version_ready(model_version_id)
        ids = []
        for node_id in nodes:
            await self.cache_repo.set_state_for_version(node_id, model_version_id, "evicting")
            await self.cache_repo.release_connection()
            ids.append(
                await self.hub.send_command(
                    node_id,
                    "evict_cache",
                    {
                        "model_version_id": str(model_version_id),
                        "force": force,
                        "unpin": False,
                    },
                )
            )
        return ids

    async def lru_sweep(self, node_id: uuid.UUID) -> Optional[uuid.UUID]:
        """
        Evict the least recently used unpinned, unused versions on a node until
        the cache is under ``MODEL_CACHE_LOW_WATER_PCT`` of its limit. Returns
        the command id or None when nothing has to go.
        """
        node = await self.cache_repo.get_node(node_id)
        if node is None or not node.cache_limit_bytes:
            return None
        rows = await self.cache_repo.summary_rows()
        used = next((r["used_bytes"] for r in rows if r["node_id"] == node_id), 0)
        high = node.cache_limit_bytes * settings.MODEL_CACHE_HIGH_WATER_PCT / 100
        if used <= high:
            return None
        low = node.cache_limit_bytes * settings.MODEL_CACHE_LOW_WATER_PCT / 100
        to_free = int(used - low)
        chosen = select_lru_victims(await self.cache_repo.lru_candidates(node_id), to_free)
        if not chosen:
            return None
        for vid in chosen:
            await self.cache_repo.set_state_for_version(node_id, vid, "evicting")
        await self.cache_repo.release_connection()
        cmd = await self.hub.send_command(
            node_id,
            "evict_cache",
            {
                "target_free_bytes": int(node.cache_limit_bytes - low),
                "force": False,
                "unpin": False,
            },
        )
        logger.info(
            f"cache LRU sweep node={node.name} versions={len(chosen)} free_target={to_free}"
        )
        return cmd

    # -------------------------------------------------------------- pin/unpin

    async def pin(self, item_id: uuid.UUID, reason: Optional[str]) -> CacheItem:
        row = await self.cache_repo.get_by_id(item_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cache item")
        await self.cache_repo.set_pinned(item_id, True, reason)
        files = [
            f
            for f in await self.files.list_for_version(row.model_version_id)
            if f.id == row.model_file_id
        ]
        if files:
            node = await self.cache_repo.get_node(row.node_id)
            await self.cache_repo.release_connection()
            try:
                await self.hub.send_command(
                    row.node_id,
                    "prefetch_model",
                    {
                        "model_version_id": str(row.model_version_id),
                        "files": [file_ref(f) for f in files],
                        "cache_dir": (node.cache_dir if node else None) or _default_cache_dir(node),
                        "source": None,
                        "pin": True,
                        "pin_reason": reason,
                        "allow_pickle": False,
                        "max_bandwidth_bytes_per_s": None,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - the DB flag is authoritative
                logger.warning(f"pin push to agent failed for item {item_id}: {exc}")
        return await self.get_item(item_id)

    async def unpin(self, item_id: uuid.UUID) -> CacheItem:
        row = await self.cache_repo.get_by_id(item_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cache item")
        await self.cache_repo.set_pinned(item_id, False, None)
        await self.cache_repo.release_connection()
        try:
            await self.hub.send_command(
                row.node_id,
                "evict_cache",
                {
                    "model_file_ids": [str(row.model_file_id)],
                    "unpin": True,
                    "force": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"unpin push to agent failed for item {item_id}: {exc}")
        return await self.get_item(item_id)

    # ------------------------------------------------ hooks for agent inventory

    async def apply_inventory(self, node_id: uuid.UUID, items: Sequence[Dict[str, Any]]) -> int:
        """
        Reconcile cache rows from an ``inventory.cache.items`` list (the agent hub calls
        this). Rows the agent no longer reports become ``evicted``. Returns the
        number of rows touched.
        """
        reported = {}
        for it in items:
            try:
                reported[uuid.UUID(str(it["model_file_id"]))] = it
            except (KeyError, ValueError):
                continue
        touched = 0
        existing = await self.cache_repo.get_all(filters={"node_id": node_id}, limit=100000)
        seen = set()
        for row in existing:
            it = reported.get(row.model_file_id)
            if it is None:
                if row.state not in ("evicted", "queued"):
                    await self.cache_repo.update(row.id, state="evicted")
                    touched += 1
                continue
            seen.add(row.model_file_id)
            await self.cache_repo.update(
                row.id,
                local_path=it.get("local_path", row.local_path),
                size_bytes=int(it.get("size_bytes", row.size_bytes) or 0),
                state=it.get("state", row.state),
                progress_pct=float(it.get("progress_pct", row.progress_pct) or 0),
                sha256_verified=bool(it.get("sha256_verified", row.sha256_verified)),
                pinned=bool(it.get("pinned", row.pinned)) or bool(row.pinned),
                error_message=it.get("error_message"),
                last_used_at=_parse_ts(it.get("last_used_at")) or row.last_used_at,
            )
            touched += 1
        for file_id, it in reported.items():
            if file_id in seen:
                continue
            try:
                await self.cache_repo.create(
                    node_id=node_id,
                    model_version_id=uuid.UUID(str(it["model_version_id"])),
                    model_file_id=file_id,
                    local_path=it.get("local_path", ""),
                    size_bytes=int(it.get("size_bytes") or 0),
                    state=it.get("state", "ready"),
                    progress_pct=float(it.get("progress_pct") or 0),
                    sha256_verified=bool(it.get("sha256_verified")),
                    pinned=bool(it.get("pinned")),
                    error_message=it.get("error_message"),
                    last_used_at=_parse_ts(it.get("last_used_at")),
                )
                touched += 1
            except Exception as exc:  # noqa: BLE001 - unknown file ids are skipped
                logger.warning(
                    f"inventory cache item skipped (file {file_id}): {type(exc).__name__}"
                )
        return touched

    async def apply_cache_item(self, node_id: uuid.UUID, item: Dict[str, Any]) -> None:
        """``cache_changed`` event handler (the agent hub calls this)."""
        await self.apply_inventory_partial(node_id, [item])

    async def apply_inventory_partial(
        self, node_id: uuid.UUID, items: Sequence[Dict[str, Any]]
    ) -> None:
        for it in items:
            try:
                file_id = uuid.UUID(str(it["model_file_id"]))
                version_id = uuid.UUID(str(it["model_version_id"]))
            except (KeyError, ValueError):
                continue
            rows = [
                r
                for r in await self.cache_repo.rows_for_version(node_id, version_id)
                if r.model_file_id == file_id
            ]
            if rows:
                await self.cache_repo.update(
                    rows[0].id,
                    state=it.get("state", rows[0].state),
                    progress_pct=float(it.get("progress_pct", rows[0].progress_pct) or 0),
                    size_bytes=int(it.get("size_bytes", rows[0].size_bytes) or 0),
                    sha256_verified=bool(it.get("sha256_verified", rows[0].sha256_verified)),
                    error_message=it.get("error_message"),
                    local_path=it.get("local_path", rows[0].local_path),
                    last_used_at=_parse_ts(it.get("last_used_at")) or rows[0].last_used_at,
                )
            else:
                await self.apply_inventory_create(node_id, it)

    async def apply_inventory_create(self, node_id: uuid.UUID, it: Dict[str, Any]) -> None:
        try:
            await self.cache_repo.create(
                node_id=node_id,
                model_version_id=uuid.UUID(str(it["model_version_id"])),
                model_file_id=uuid.UUID(str(it["model_file_id"])),
                local_path=it.get("local_path", ""),
                size_bytes=int(it.get("size_bytes") or 0),
                state=it.get("state", "ready"),
                progress_pct=float(it.get("progress_pct") or 0),
                sha256_verified=bool(it.get("sha256_verified")),
                pinned=bool(it.get("pinned")),
                error_message=it.get("error_message"),
                last_used_at=_parse_ts(it.get("last_used_at")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cache item create skipped: {type(exc).__name__}")

    async def mark_used(self, node_id: uuid.UUID, model_version_id: uuid.UUID) -> None:
        """Called when an instance loads a version (last-use bookkeeping)."""
        await self.cache_repo.touch_last_used(node_id, model_version_id)


# ------------------------------------------------------------------ helpers


def file_ref(f: ModelFiles) -> Dict[str, Any]:
    """``common.json#/$defs/ModelFileRef``."""
    return {
        "model_file_id": str(f.id),
        "path": f.path,
        "format": f.format,
        "size_bytes": int(f.size_bytes or 0),
        "sha256": f.sha256,
        "is_required": bool(f.is_required),
    }


def select_lru_victims(candidates: List[Dict[str, Any]], bytes_to_free: int) -> List[uuid.UUID]:
    """Oldest-first greedy selection until ``bytes_to_free`` is covered (pure)."""
    chosen: List[uuid.UUID] = []
    freed = 0
    for cand in candidates:  # already ordered oldest first
        if freed >= bytes_to_free:
            break
        chosen.append(cand["model_version_id"])
        freed += int(cand.get("size_bytes") or 0)
    return chosen


def _default_cache_dir(node: Optional[Nodes]) -> str:
    if node is not None and node.os == "macos":
        return "/usr/local/var/gpudeck/cache"
    return "/var/lib/gpudeck/cache"


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def stale_cutoff(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)
