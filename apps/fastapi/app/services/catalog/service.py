"""
Model catalog service: three sources (Hugging Face, ModelScope, local path),
models, versions, files, hash recording and security defaults.

Rules enforced here:

* ``trust_remote_code`` is off unless the admin turns it on per model.
* A version whose only weights are pickle based (.bin/.pt/.pth/.ckpt) is
  ``blocked`` unless ``allow_pickle`` is true on the model.
* sha256 is recorded at registration (HF LFS oid, ModelScope ``Sha256``,
  Ollama blob digest) and computed for other local files by the catalog
  worker; nodes verify the hash after download.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import ModelFiles, Models, ModelVersions
from app.db.postgres.repositories.model_repository import (
    ModelFileRepository,
    ModelRepository,
    ModelVersionRepository,
)
from app.db.postgres.repositories.node_model_cache_repository import (
    NodeModelCacheRepository,
)
from app.schemas.catalog_schema import (
    CatalogRemoteFiles,
    CatalogSearchResult,
    LocalScanCandidate,
    LocalScanRequest,
    Model,
    ModelCreate,
    ModelFile,
    ModelUpdate,
    ModelVersion,
    ModelVersionCreate,
    ModelVersionUpdate,
    VerifyResult,
)
from app.services.catalog import detect
from app.services.catalog.hf_client import HuggingFaceClient, RemoteRepo
from app.services.catalog.local_scanner import (
    LocalScanner,
    ScanCandidate,
    ScannedFile,
    complete_suggestions,
)
from app.services.catalog.modelscope_client import ModelScopeClient
from app.services.catalog.secrets import (
    model_credential_id,
    require_source_credential,
    resolve_source_token,
)
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_HASH_CHUNK = 8 * 1024 * 1024


class CatalogService:
    """Catalog sources + models/versions/files CRUD."""

    def __init__(
        self,
        session: AsyncSession,
        models: ModelRepository,
        versions: ModelVersionRepository,
        files: ModelFileRepository,
        cache_repo: NodeModelCacheRepository,
        hf_client: Optional[HuggingFaceClient] = None,
        ms_client: Optional[ModelScopeClient] = None,
    ) -> None:
        self.session = session
        self.models = models
        self.versions = versions
        self.files = files
        self.cache_repo = cache_repo
        self._hf = hf_client
        self._ms = ms_client

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "CatalogService":
        return CatalogService(
            db,
            ModelRepository(db),
            ModelVersionRepository(db),
            ModelFileRepository(db),
            NodeModelCacheRepository(db),
        )

    @classmethod
    def for_session(cls, db: AsyncSession) -> "CatalogService":
        """Build without FastAPI (workers, other services)."""
        return cls(
            db,
            ModelRepository(db),
            ModelVersionRepository(db),
            ModelFileRepository(db),
            NodeModelCacheRepository(db),
        )

    # ------------------------------------------------------------ remote sources

    async def _client(self, source: str, credential_id: Optional[uuid.UUID] = None):
        """Remote source client; ``credential_id`` picks the token credential (vault),
        else the default token (see catalog/secrets.py)."""
        if source not in ("huggingface", "modelscope"):
            raise ValidationError(f"Unsupported remote source {source!r}")
        if credential_id is not None:
            token = await resolve_source_token(self.session, source, credential_id)
            return self._make_client(source, token)
        if source == "huggingface":
            if self._hf is None:
                self._hf = self._make_client(
                    source, await resolve_source_token(self.session, source)
                )
            return self._hf
        if self._ms is None:
            self._ms = self._make_client(source, await resolve_source_token(self.session, source))
        return self._ms

    @staticmethod
    def _make_client(source: str, token: Optional[str]):
        if source == "huggingface":
            return HuggingFaceClient(
                settings.HF_ENDPOINT, token, settings.CATALOG_HTTP_TIMEOUT_SECONDS
            )
        return ModelScopeClient(
            settings.MODELSCOPE_ENDPOINT, token, settings.CATALOG_HTTP_TIMEOUT_SECONDS
        )

    async def search(
        self,
        source: str,
        query: str,
        *,
        model_type: Optional[str] = None,
        fmt: Optional[str] = None,
        author: Optional[str] = None,
        sort: str = "downloads",
        limit: int = 25,
        credential_id: Optional[uuid.UUID] = None,
    ) -> List[CatalogSearchResult]:
        digest = hashlib.sha1(
            json.dumps(
                [query, model_type, fmt, author, sort, limit, str(credential_id or "")]
            ).encode()
        ).hexdigest()
        cache_key = f"catalog:search:{source}:{digest}"
        rows = await self._cache_get(cache_key)
        if rows is None:
            client = await self._client(source, credential_id)
            await self.models.release_connection()
            rows = await client.search(
                query,
                limit=limit,
                author=author,
                sort=sort,
                model_type=model_type,
                fmt=fmt,
            )
            await self._cache_set(cache_key, rows)
        registered = await self.models.registered_ids_for_repos(
            source, [r["repo_id"] for r in rows]
        )
        out = []
        for row in rows:
            row = dict(row)
            row["already_registered_model_id"] = registered.get(row["repo_id"])
            out.append(CatalogSearchResult.model_validate(row))
        return out

    async def remote_files(
        self,
        source: str,
        repo_id: str,
        revision: Optional[str] = None,
        credential_id: Optional[uuid.UUID] = None,
    ) -> CatalogRemoteFiles:
        repo = await self._fetch_repo(source, repo_id, revision, credential_id)
        return CatalogRemoteFiles.model_validate(repo.to_dict())

    async def _fetch_repo(
        self,
        source: str,
        repo_id: str,
        revision: Optional[str],
        credential_id: Optional[uuid.UUID] = None,
    ) -> RemoteRepo:
        cache_key = f"catalog:repo:{source}:{repo_id}:{revision or ''}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return _repo_from_cache(cached)
        client = await self._client(source, credential_id)
        await self.models.release_connection()
        repo = await client.repo(repo_id, revision)
        await self._cache_set(cache_key, _repo_to_cache(repo))
        return repo

    async def scan_local(self, req: LocalScanRequest) -> List[LocalScanCandidate]:
        if req.node_id is not None:
            cands = await self._scan_on_node(req.node_id, req.path, req.recursive, req.max_depth)
            return [
                LocalScanCandidate.model_validate({**c.to_dict(), "node_id": req.node_id})
                for c in cands
            ]
        try:
            cands = await asyncio.to_thread(
                LocalScanner(req.max_depth, settings.LOCAL_SCAN_MAX_FILES).scan,
                os.path.expanduser(req.path),
                req.recursive,
            )
        except FileNotFoundError:
            raise ResourceNotFoundError(detail=f"Path not found: {req.path}")
        except PermissionError:
            raise ValidationError(f"Path not readable: {req.path}")
        return [LocalScanCandidate.model_validate(c.to_dict()) for c in cands]

    async def _scan_on_node(
        self, node_id: uuid.UUID, path: str, recursive: bool = True, max_depth: int = 4
    ) -> List[ScanCandidate]:
        """``scan_path`` on the node; ``~`` is expanded by the agent."""
        from app.services.nodes import files as node_files

        await self.models.release_connection()
        out = await node_files.run_file_command(
            node_id,
            "scan_path",
            node_files.scan_args(path, recursive, max_depth, settings.LOCAL_SCAN_MAX_FILES),
            wait_seconds=NODE_SCAN_WAIT_SECONDS,
        )
        if out["state"] != "succeeded":
            code = out.get("error_code") or out["state"]
            message = out.get("error_message") or f"scan_path {out['state']}"
            if code == "path_not_found":
                raise ResourceNotFoundError(detail=f"Path not found on node: {path}")
            if out["state"] in ("pending", "sent", "accepted", "running"):
                raise ConflictError(
                    f"scan still running on the node (command {out['command_id']}); "
                    "poll GET /nodes/{node_id}/commands/{command_id}"
                )
            raise ValidationError(f"node scan failed ({code}): {message}")
        return [_candidate_from_agent(c) for c in (out.get("result") or {}).get("candidates", [])]

    # ------------------------------------------------------------------ models

    async def list_models(self, **filters) -> Tuple[List[Model], int]:
        rows, total = await self.models.list_models(**filters)
        return await self._models_out(rows), total

    async def get_model(self, model_id: uuid.UUID) -> Model:
        row = await self._model_or_404(model_id)
        return (await self._models_out([row]))[0]

    async def create_model(
        self, data: ModelCreate, created_by: Optional[uuid.UUID] = None
    ) -> Model:
        if await self.models.get_by_name(data.name):
            raise ConflictError(f"A model named {data.name!r} already exists")
        await self._check_credential(data.source.value, (data.metadata or {}).get("credential_id"))
        repo_id = data.repo_id
        if data.source.value == "local_path" and not (data.metadata or {}).get("node_id"):
            # Node-local paths (metadata.node_id) are expanded by the agent on that node.
            repo_id = str(Path(os.path.expanduser(repo_id)))
            if not os.path.isabs(repo_id):
                raise ValidationError("local_path repo_id must be an absolute path")
        row = await self.models.create(
            name=data.name,
            display_name=data.display_name,
            source=data.source.value,
            repo_id=repo_id,
            family=data.family,
            type=data.type.value,
            priority=data.priority.value,
            description=data.description,
            tool_parser_default=data.tool_parser_default,
            trust_remote_code=data.trust_remote_code,
            allow_pickle=data.allow_pickle,
            default_context_size=data.default_context_size,
            default_launch_flags=data.default_launch_flags or {},
            aliases=list(data.aliases or []),
            tags=list(data.tags or []),
            metadata_=data.metadata or {},
            created_by=created_by,
        )
        initial = data.initial_version or ModelVersionCreate(version="main", is_default=True)
        initial.is_default = True
        try:
            await self.add_version(row.id, initial, model_row=row)
        except Exception:
            logger.warning(f"initial version registration failed for model {row.id}")
            raise
        # Fill family / tool parser / context from what the version revealed.
        refreshed = await self.models.get_active(row.id)
        return (await self._models_out([refreshed]))[0]

    async def _check_credential(self, source: str, credential_id: Any) -> None:
        """metadata.credential_id must be an active token credential of the model's source."""
        if not credential_id:
            return
        try:
            cred = uuid.UUID(str(credential_id))
        except ValueError:
            raise ValidationError("metadata.credential_id must be a credential id") from None
        await require_source_credential(self.session, source, cred)

    async def update_model(self, model_id: uuid.UUID, data: ModelUpdate) -> Model:
        row = await self._model_or_404(model_id)
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"].lower() != row.name.lower():
            if await self.models.get_by_name(changes["name"]):
                raise ConflictError(f"A model named {changes['name']!r} already exists")
        if "metadata" in changes:
            changes["metadata_"] = changes.pop("metadata") or {}
            await self._check_credential(row.source, changes["metadata_"].get("credential_id"))
        for key in ("type", "priority"):
            if key in changes and changes[key] is not None:
                changes[key] = (
                    changes[key].value if hasattr(changes[key], "value") else changes[key]
                )
        allow_pickle_changed = (
            "allow_pickle" in changes and changes["allow_pickle"] != row.allow_pickle
        )
        await self.models.update(model_id, **changes)
        if allow_pickle_changed:
            await self._reapply_pickle_policy(model_id, changes["allow_pickle"])
        return await self.get_model(model_id)

    async def delete_model(self, model_id: uuid.UUID, evict_cache: bool = False) -> None:
        row = await self._model_or_404(model_id)
        if await self.models.deployment_count(model_id):
            raise ConflictError("MODEL_IN_USE: delete its deployments first")
        versions = await self.versions.list_for_model(model_id)
        if evict_cache and versions:
            from app.services.model_cache.service import ModelCacheService

            cache = ModelCacheService.for_session(self.session)
            for v in versions:
                try:
                    await cache.evict_version_everywhere(v.id)
                except Exception as exc:  # noqa: BLE001 - eviction is best effort on delete
                    logger.warning(f"evict on delete failed for version {v.id}: {exc}")
        for v in versions:
            await self.versions.soft_delete(v.id)
        await self.models.soft_delete(model_id)
        logger.info(f"model deleted id={model_id} name={row.name}")

    # ---------------------------------------------------------------- versions

    async def list_versions(self, model_id: uuid.UUID) -> List[ModelVersion]:
        await self._model_or_404(model_id)
        rows = await self.versions.list_for_model(model_id)
        return await self._versions_out(rows)

    async def get_version(self, model_id: uuid.UUID, version_id: uuid.UUID) -> ModelVersion:
        row = await self._version_or_404(model_id, version_id)
        return (await self._versions_out([row]))[0]

    async def add_version(
        self,
        model_id: uuid.UUID,
        data: ModelVersionCreate,
        model_row: Optional[Models] = None,
    ) -> ModelVersion:
        model = model_row or await self._model_or_404(model_id)
        if await self.versions.get_by_label(model_id, data.version):
            raise ConflictError(f"Version {data.version!r} already exists for this model")

        if model.source == "local_path":
            built = await self._build_local_version(model, data)
        else:
            built = await self._build_remote_version(model, data)
        files, config, fmt, status, message, size, params, revision = built

        is_default = data.is_default or not await self.versions.list_for_model(model_id)
        if is_default:
            await self.versions.clear_default(model_id)
        version = await self.versions.create(
            model_id=model_id,
            version=data.version,
            revision=revision,
            quantization=data.quantization or config.get("quantization"),
            format=fmt,
            status=status,
            status_message=message,
            total_size_bytes=size,
            parameter_count=params,
            config=config,
            is_default=is_default,
        )
        if files:
            await self.files.bulk_create(
                [
                    {
                        "id": uuid.uuid4(),
                        "model_version_id": version.id,
                        "path": f["path"],
                        "format": f["format"],
                        "size_bytes": int(f["size_bytes"]),
                        "sha256": f.get("sha256"),
                        "is_required": bool(f.get("is_required", True)),
                        "shard_index": f.get("shard_index"),
                        "shard_count": f.get("shard_count"),
                    }
                    for f in files
                ]
            )
        await self._backfill_model_defaults(model, config)
        if (
            model.source == "local_path"
            and status == "registered"
            and any(not f.get("sha256") for f in files)
        ):
            # Hash in the background; the version is ready once every file has a sha256.
            status = await self._start_local_hashing(model, version)
        logger.info(
            f"model version registered model={model.name} version={data.version} "
            f"format={fmt} status={status} files={len(files)}"
        )
        return (await self._versions_out([version]))[0]

    async def update_version(
        self, model_id: uuid.UUID, version_id: uuid.UUID, data: ModelVersionUpdate
    ) -> ModelVersion:
        await self._version_or_404(model_id, version_id)
        changes = data.model_dump(exclude_unset=True)
        if "version" in changes:
            other = await self.versions.get_by_label(model_id, changes["version"])
            if other and other.id != version_id:
                raise ConflictError(f"Version {changes['version']!r} already exists for this model")
        if changes.get("is_default"):
            await self.versions.clear_default(model_id)
        elif "is_default" in changes and changes["is_default"] is False:
            raise ValidationError("Set another version as default instead of clearing this one")
        await self.versions.update(version_id, **changes)
        return await self.get_version(model_id, version_id)

    async def delete_version(self, model_id: uuid.UUID, version_id: uuid.UUID) -> None:
        row = await self._version_or_404(model_id, version_id)
        if await self.versions.deployment_count(version_id):
            raise ConflictError("MODEL_VERSION_IN_USE: delete its deployments first")
        await self.versions.soft_delete(version_id)
        if row.is_default:
            remaining = await self.versions.list_for_model(model_id)
            if remaining:
                await self.versions.update(remaining[0].id, is_default=True)

    async def list_files(self, model_id: uuid.UUID, version_id: uuid.UUID) -> List[ModelFile]:
        await self._version_or_404(model_id, version_id)
        rows = await self.files.list_for_version(version_id)
        return await self._files_out(rows)

    async def list_version_cache(
        self, model_id: uuid.UUID, version_id: uuid.UUID
    ) -> List[Dict[str, Any]]:
        await self._version_or_404(model_id, version_id)
        from app.services.model_cache.service import ModelCacheService

        cache = ModelCacheService.for_session(self.session)
        items, _ = await cache.list_items(page=1, page_size=500, model_version_id=version_id)
        return items

    async def verify_version(self, model_id: uuid.UUID, version_id: uuid.UUID) -> VerifyResult:
        model = await self._model_or_404(model_id)
        version = await self._version_or_404(model_id, version_id)
        files = await self.files.list_for_version(version_id)
        formats = [f.format for f in files]
        pickle_blocked = detect.has_pickle_only_weights(formats) and not model.allow_pickle
        mismatches: List[ModelFiles] = []
        if pickle_blocked:
            status, message = "blocked", "pickle-only weights and allow_pickle is off"
        elif model.source == "local_path" and _model_node(model) is not None:
            status, message, mismatches = await self._verify_on_node(model, version, files)
        elif model.source == "local_path":
            root = Path(_local_root(model, version))
            missing = [f for f in files if f.is_required and not (root / f.path).exists()]
            size_mismatch = [
                f
                for f in files
                if f not in missing
                and (root / f.path).exists()
                and (root / f.path).stat().st_size != f.size_bytes
            ]
            mismatches = missing + size_mismatch
            if missing:
                status, message = (
                    "missing",
                    f"{len(missing)} required file(s) missing under {root}",
                )
            elif size_mismatch:
                status, message = (
                    "blocked",
                    "file size differs from the registered size",
                )
            else:
                unhashed = [f for f in files if not f.sha256 and f.format != "other"]
                if unhashed:
                    status, message = "verifying", f"hashing {len(unhashed)} file(s)"
                    self._enqueue_local_hashing(version_id)
                else:
                    status, message = "ready", None
        else:
            repo = await self._fetch_repo(
                model.source,
                model.repo_id,
                version.revision,
                model_credential_id(model),
            )
            remote = {f.path: f for f in repo.files}
            for f in files:
                r = remote.get(f.path)
                if r is None:
                    mismatches.append(f)
                    continue
                if f.sha256 and r.sha256 and f.sha256 != r.sha256:
                    mismatches.append(f)
                elif r.size_bytes and f.size_bytes and r.size_bytes != f.size_bytes:
                    mismatches.append(f)
                elif r.sha256 and not f.sha256:
                    await self.files.set_sha256(f.id, r.sha256)
            if mismatches:
                status, message = (
                    "blocked",
                    f"{len(mismatches)} file(s) differ from the source",
                )
            else:
                status, message = "ready", None
        # Cached copies that failed verification on a node flag the version too.
        if status == "ready":
            bad = await self.cache_repo.count_unverified_errors(version_id)
            if bad:
                message = f"{bad} cached copy(ies) failed hash verification"
        await self.versions.set_status(version_id, status, message)
        return VerifyResult(
            model_version_id=version_id,
            status=status,
            files_checked=len(files),
            mismatches=await self._files_out(mismatches),
            pickle_blocked=pickle_blocked,
        )

    # ------------------------------------------------------- worker entry points

    async def compute_local_hashes(self, version_id: uuid.UUID) -> int:
        """Hash local files lacking a sha256 (runs in the catalog worker). Returns count hashed."""
        version = await self.versions.get_active(version_id)
        if version is None:
            return 0
        model = await self.models.get_active(version.model_id)
        if model is None or model.source != "local_path":
            return 0
        root = Path(_local_root(model, version))
        hashed = 0
        for f in await self.files.list_for_version(version_id):
            if f.sha256 or f.format == "other":
                continue
            target = root / f.path
            if not target.is_file():
                continue
            digest = await asyncio.to_thread(_sha256_file, target)
            await self.files.set_sha256(f.id, digest)
            hashed += 1
        if version.status in ("verifying", "registered"):
            await self.versions.set_status(version_id, "ready", None)
        logger.info(f"hashed {hashed} local file(s) for version {version_id}")
        return hashed

    # ------------------------------------------------------------- internals

    async def _build_remote_version(self, model: Models, data: ModelVersionCreate):
        repo = await self._fetch_repo(
            model.source, model.repo_id, data.revision, model_credential_id(model)
        )
        wanted = set(data.files or [])
        chosen = [f for f in repo.files if not wanted or f.path in wanted or f.format == "other"]
        if wanted:
            missing = wanted - {f.path for f in repo.files}
            if missing:
                raise ValidationError(f"Files not in repo: {sorted(missing)}")
            # Keep side-car files (tokenizer/config) that engines need.
            chosen = [
                f for f in chosen if f.path in wanted or (f.format == "other" and f.is_required)
            ]
        if not chosen:
            raise ValidationError("The repo has no files to register")
        files = []
        for f in chosen:
            sidx, scount = detect.shard_info(f.path)
            files.append(
                {
                    "path": f.path,
                    "format": f.format,
                    "size_bytes": f.size_bytes,
                    "sha256": f.sha256,
                    "is_required": f.is_required,
                    "shard_index": sidx,
                    "shard_count": scount,
                }
            )
        formats = [f["format"] for f in files]
        is_mlx = detect.is_mlx_config(repo.config) or "mlx" in model.repo_id.lower()
        fmt = detect.version_format(formats, is_mlx=is_mlx)
        config = detect.shape_from_hf_config(repo.config) if repo.config else {}
        config["source_kind"] = model.source
        quant = data.quantization or detect.detect_quantization(
            *[f["path"] for f in files if f["format"] == "gguf"], model.repo_id
        )
        if quant:
            config["quantization"] = quant
        elif isinstance(config.get("quantization"), dict):
            q = config["quantization"]
            config["quantization"] = f"{q.get('bits')}bit" if q.get("bits") else None
        params = _params_from_hf_config(repo.config)
        status, message = _status_for(formats, model.allow_pickle, files)
        size = sum(f["size_bytes"] for f in files)
        return files, config, fmt, status, message, size, params, repo.revision

    async def _build_local_version(self, model: Models, data: ModelVersionCreate):
        node_id = _model_node(model)
        if node_id is not None:
            target = data.local_path or model.repo_id
            cands = await self._scan_on_node(node_id, target, True, 6)
            if cands and not os.path.isabs(target):
                target = cands[0].path if len(cands) == 1 else target
        else:
            target = str(Path(os.path.expanduser(data.local_path or model.repo_id)))
            try:
                cands = await asyncio.to_thread(
                    LocalScanner(6, settings.LOCAL_SCAN_MAX_FILES).scan, target, True
                )
            except FileNotFoundError:
                raise ResourceNotFoundError(detail=f"Path not found: {target}")
        cand = _pick_candidate(cands, target, data.files)
        if cand is None:
            raise ValidationError(f"No model files found under {target}")
        wanted = set(data.files or [])
        files = []
        for f in cand.files:
            if wanted and f.path not in wanted and f.format != "other":
                continue
            sidx, scount = detect.shard_info(f.path)
            files.append(
                {
                    "path": f.path,
                    "format": f.format,
                    "size_bytes": f.size_bytes,
                    "sha256": f.sha256,
                    "is_required": f.is_required,
                    "shard_index": sidx,
                    "shard_count": scount,
                }
            )
        formats = [f["format"] for f in files]
        config = dict(cand.config or {})
        config["source_kind"] = cand.kind
        if node_id is not None:
            root = (cand.config or {}).get("local_root") or cand.path
            config["node_id"] = str(node_id)
        else:
            root = cand.path if os.path.isdir(cand.path) else os.path.dirname(cand.path)
        config["local_root"] = root
        quant = data.quantization or cand.suggested.get("quantization")
        if quant:
            config["quantization"] = quant
        params = config.get("parameter_count") or cand.suggested.get("parameter_count")
        status, message = _status_for(formats, model.allow_pickle, files)
        size = sum(f["size_bytes"] for f in files)
        return files, config, cand.format, status, message, size, params, None

    async def _backfill_model_defaults(self, model: Models, config: Dict[str, Any]) -> None:
        changes: Dict[str, Any] = {}
        if not model.family:
            fam = detect.detect_family(
                model.name,
                model.repo_id,
                config.get("model_name"),
                architecture=config.get("architecture"),
            )
            if fam:
                changes["family"] = fam
        family = changes.get("family") or model.family
        if not model.tool_parser_default and family:
            parser = detect.default_tool_parser(family, "llama_cpp")
            if parser:
                changes["tool_parser_default"] = parser
        if not model.default_context_size and config.get("max_position_embeddings"):
            changes["default_context_size"] = int(config["max_position_embeddings"])
        if changes:
            await self.models.update(model.id, **changes)

    async def _reapply_pickle_policy(self, model_id: uuid.UUID, allow_pickle: bool) -> None:
        for v in await self.versions.list_for_model(model_id):
            files = await self.files.list_for_version(v.id)
            pickle_only = detect.has_pickle_only_weights(f.format for f in files)
            if pickle_only and not allow_pickle and v.status != "blocked":
                await self.versions.set_status(
                    v.id, "blocked", "pickle-only weights and allow_pickle is off"
                )
            elif pickle_only and allow_pickle and v.status == "blocked":
                await self.versions.set_status(
                    v.id, "registered", "pickle weights allowed by admin"
                )

    async def _verify_on_node(self, model: Models, version: ModelVersions, files):
        """``hash_files`` on the model's node; sizes and sha256 checked against the rows."""
        from app.services.nodes import files as node_files

        node_id = _model_node(model)
        root = _local_root(model, version)
        out = await node_files.run_file_command(
            node_id,
            "hash_files",
            node_files.hash_args([f.path for f in files], root),
            wait_seconds=NODE_HASH_WAIT_SECONDS,
        )
        if out["state"] != "succeeded":
            if out["state"] in ("pending", "sent", "accepted", "running"):
                self._enqueue_node_hash_apply(version.id, out["command_id"])
                return "verifying", f"hashing on node (command {out['command_id']})", []
            return (
                "missing",
                out.get("error_message") or f"hash_files {out['state']}",
                [],
            )
        mismatches = await self.apply_node_hashes(files, out.get("result") or {})
        if mismatches:
            return (
                "blocked",
                f"{len(mismatches)} file(s) missing or changed on the node",
                mismatches,
            )
        return "ready", None, []

    async def apply_node_hashes(self, files, result: Dict[str, Any]) -> List[ModelFiles]:
        """Store sha256 from a ``hash_files`` result; returns files that are missing/changed."""
        by_path = {f["path"]: f for f in result.get("files") or []}
        mismatches = []
        for f in files:
            item = by_path.get(f.path)
            sha = (item or {}).get("sha256")
            if item is None or sha is None:
                if f.is_required:
                    mismatches.append(f)
                continue
            size = item.get("size_bytes")
            if (f.sha256 and f.sha256 != sha) or (
                size is not None and f.size_bytes and size != f.size_bytes
            ):
                mismatches.append(f)
            elif not f.sha256:
                await self.files.set_sha256(f.id, sha)
        return mismatches

    def _enqueue_node_hash_apply(self, version_id: uuid.UUID, command_id: Any) -> None:
        try:
            from app.workers.tasks.catalog_tasks import enqueue_apply_node_hashes

            enqueue_apply_node_hashes(str(version_id), str(command_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"enqueue node hash apply failed for version {version_id}: {exc}")

    async def _start_local_hashing(self, model: Models, version: ModelVersions) -> str:
        """
        Node-local models are hashed on their node (``hash_files``, applied by the
        catalog worker); control-plane-local ones by the catalog worker itself.
        Either way the version is ``verifying`` until the hashes land, then ``ready``.
        """
        node_id = _model_node(model)
        if node_id is not None:
            from app.services.nodes import files as node_files

            try:
                files = await self.files.list_for_version(version.id)
                out = await node_files.run_file_command(
                    node_id,
                    "hash_files",
                    node_files.hash_args([f.path for f in files], _local_root(model, version)),
                    wait_seconds=0,
                )
            except Exception as exc:  # noqa: BLE001 - node offline etc.: verify later
                logger.warning(f"node hashing not started for version {version.id}: {exc}")
                return version.status
            self._enqueue_node_hash_apply(version.id, out["command_id"])
            message = f"hashing on node (command {out['command_id']})"
        else:
            self._enqueue_local_hashing(version.id)
            message = "hashing local files"
        await self.versions.set_status(version.id, "verifying", message)
        version.status, version.status_message = "verifying", message
        return "verifying"

    def _enqueue_local_hashing(self, version_id: uuid.UUID) -> None:
        try:
            from app.workers.tasks.catalog_tasks import enqueue_hash_local_version

            enqueue_hash_local_version(str(version_id))
        except Exception as exc:  # noqa: BLE001 - never fail a request on enqueue
            logger.warning(f"enqueue hash job failed for version {version_id}: {exc}")

    async def _model_or_404(self, model_id: uuid.UUID) -> Models:
        row = await self.models.get_active(model_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Model")
        return row

    async def _version_or_404(self, model_id: uuid.UUID, version_id: uuid.UUID) -> ModelVersions:
        row = await self.versions.get_active(version_id)
        if row is None or row.model_id != model_id:
            raise ResourceNotFoundError(resource_type="Model version")
        return row

    async def _models_out(self, rows: Sequence[Models]) -> List[Model]:
        ids = [r.id for r in rows]
        defaults = await self.versions.defaults_for_models(ids)
        vcounts = await self.versions.version_counts(ids)
        dcounts = await self.models.deployment_counts(ids)
        default_out = await self._versions_out(list(defaults.values()))
        default_by_model = {v.model_id: v for v in default_out}
        out = []
        for r in rows:
            out.append(
                Model(
                    id=r.id,
                    name=r.name,
                    display_name=r.display_name,
                    source=r.source,
                    repo_id=r.repo_id,
                    family=r.family,
                    type=r.type,
                    priority=r.priority,
                    description=r.description,
                    tool_parser_default=r.tool_parser_default,
                    trust_remote_code=r.trust_remote_code,
                    allow_pickle=r.allow_pickle,
                    default_context_size=r.default_context_size,
                    default_launch_flags=r.default_launch_flags or {},
                    aliases=list(r.aliases or []),
                    tags=list(r.tags or []),
                    metadata=r.metadata_ or {},
                    default_version=default_by_model.get(r.id),
                    version_count=vcounts.get(r.id, 0),
                    deployment_count=dcounts.get(r.id, 0),
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )
        return out

    async def _versions_out(self, rows: Sequence[ModelVersions]) -> List[ModelVersion]:
        counts = await self.files.file_counts([r.id for r in rows])
        return [
            ModelVersion(
                id=r.id,
                model_id=r.model_id,
                version=r.version,
                revision=r.revision,
                quantization=r.quantization,
                format=r.format,
                status=r.status,
                status_message=r.status_message,
                total_size_bytes=r.total_size_bytes or 0,
                parameter_count=r.parameter_count,
                config=r.config or {},
                is_default=bool(r.is_default),
                file_count=counts.get(r.id, 0),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def _files_out(self, rows: Sequence[ModelFiles]) -> List[ModelFile]:
        cached = await self.files.cached_node_counts([r.id for r in rows])
        return [
            ModelFile(
                id=r.id,
                model_version_id=r.model_version_id,
                path=r.path,
                format=r.format,
                size_bytes=r.size_bytes or 0,
                sha256=r.sha256,
                is_required=bool(r.is_required),
                shard_index=r.shard_index,
                shard_count=r.shard_count,
                cached_on_nodes=cached.get(r.id, 0),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ cache

    @staticmethod
    async def _cache_get(key: str):
        try:
            from app.db.redis.cache_service import get_cache_service

            cache = await get_cache_service()
            return await cache.get(key)
        except Exception:  # noqa: BLE001 - Redis down = no cache
            return None

    @staticmethod
    async def _cache_set(key: str, value: Any) -> None:
        try:
            from app.db.redis.cache_service import get_cache_service

            cache = await get_cache_service()
            await cache.set(key, value, ttl=settings.CATALOG_SEARCH_CACHE_SECONDS)
        except Exception:  # noqa: BLE001
            return


# ---------------------------------------------------------------- helpers


def _status_for(formats: List[str], allow_pickle: bool, files: List[Dict[str, Any]]):
    if detect.has_pickle_only_weights(formats):
        if not allow_pickle:
            return "blocked", "pickle-only weights and allow_pickle is off"
        return "registered", "pickle weights allowed by admin"
    weights = [f for f in files if f["format"] in ("gguf", "safetensors", "mlx", "pickle")]
    if not weights:
        return "registered", "no weight files selected"
    if all(f.get("sha256") for f in weights):
        return "ready", None
    return "registered", None


def _params_from_hf_config(config: Dict[str, Any]) -> Optional[int]:
    if not config:
        return None
    for key in ("num_parameters", "n_params", "parameter_count"):
        value = config.get(key)
        if isinstance(value, int):
            return value
    return None


NODE_SCAN_WAIT_SECONDS = 60
NODE_HASH_WAIT_SECONDS = 20


def _model_node(model: Models) -> Optional[uuid.UUID]:
    """Node a local_path model lives on (``models.metadata.node_id``), else None."""
    value = (getattr(model, "metadata_", None) or {}).get("node_id")
    if not value or model.source != "local_path":
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _candidate_from_agent(raw: Dict[str, Any]) -> ScanCandidate:
    """Agent ``ScanCandidate`` -> the control plane's scanner dataclass."""
    files = [
        ScannedFile(
            path=f["path"],
            size_bytes=int(f.get("size_bytes") or 0),
            format=f.get("format") or "other",
            sha256=f.get("sha256"),
            is_required=bool(f.get("is_required", True)),
            absolute_path=f.get("absolute_path"),
        )
        for f in raw.get("files") or []
    ]
    path = raw["path"]
    metadata = raw.get("metadata") or {}
    # Agents fill family / type / quantization / context; older ones left family / type null.
    suggested = {
        k: v
        for k, v in complete_suggestions(raw.get("suggested") or {}, path, metadata).items()
        if v is not None
    }
    # Single-file candidates (GGUF, Ollama blob) list the file name relative to its directory.
    single = len(files) == 1 and files[0].path == os.path.basename(path.rstrip("/"))
    root = os.path.dirname(path.rstrip("/")) if single else path
    config = {"local_root": root}
    for key in ("context_size", "architecture", "quantization", "parameter_count"):
        if suggested.get(key) is not None:
            config[key] = suggested[key]
    if metadata:
        config["metadata"] = metadata
    return ScanCandidate(
        path=path,
        kind=raw.get("kind") or "plain",
        format=raw.get("format") or "other",
        files=files,
        total_size_bytes=int(raw.get("total_size_bytes") or 0),
        suggested=suggested,
        config=config,
    )


def _local_root(model: Models, version: ModelVersions) -> str:
    root = (version.config or {}).get("local_root")
    if root:
        return root
    return model.repo_id if os.path.isdir(model.repo_id) else os.path.dirname(model.repo_id)


def _pick_candidate(
    cands: List[ScanCandidate], target: str, wanted: List[str]
) -> Optional[ScanCandidate]:
    if not cands:
        return None
    for c in cands:
        if c.path == target:
            return c
    if len(cands) == 1:
        return cands[0]
    if wanted:
        for c in cands:
            paths = {f.path for f in c.files}
            if set(wanted) <= paths:
                return c
    raise ValidationError(
        f"{len(cands)} model candidates found under {target}; register a more specific path"
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_to_cache(repo: RemoteRepo) -> Dict[str, Any]:
    return {
        "source": repo.source,
        "repo_id": repo.repo_id,
        "revision": repo.revision,
        "files": [f.to_dict() for f in repo.files],
        "config": repo.config,
        "pipeline_tag": repo.pipeline_tag,
        "tags": repo.tags,
        "gated": repo.gated,
        "private": repo.private,
        "library": repo.library,
    }


def _repo_from_cache(data: Dict[str, Any]) -> RemoteRepo:
    from app.services.catalog.hf_client import RemoteFile

    return RemoteRepo(
        source=data["source"],
        repo_id=data["repo_id"],
        revision=data.get("revision"),
        files=[RemoteFile(**f) for f in data.get("files", [])],
        config=data.get("config") or {},
        pipeline_tag=data.get("pipeline_tag"),
        tags=list(data.get("tags") or []),
        gated=bool(data.get("gated")),
        private=bool(data.get("private")),
        library=data.get("library"),
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
