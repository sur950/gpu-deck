"""
ModelScope client (public REST API used by the ``modelscope`` SDK, optional token).

Endpoints under ``MODELSCOPE_ENDPOINT``:

* ``PUT /api/v1/dolphin/models`` with ``{"Name": q, "PageNumber", "PageSize", "SortBy"}`` — search
* ``GET /api/v1/models/{owner}/{name}`` — model info (tasks, downloads, revision)
* ``GET /api/v1/models/{owner}/{name}/repo/files?Revision=&Recursive=True`` — files + sha256
* ``GET /api/v1/models/{owner}/{name}/repo?Revision=&FilePath=config.json`` — raw config

Every response is ``{"Code": 200, "Data": ...}``; anything else is surfaced as
``ExternalServiceError``. A custom transport can be injected for tests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.core.logger import get_logger
from app.services.catalog import detect
from app.services.catalog.hf_client import RemoteFile, RemoteRepo

logger = get_logger(__name__)

_TASK_TO_PIPELINE = {
    "text-generation": "text-generation",
    "chat": "text-generation",
    "image-text-to-text": "image-text-to-text",
    "multi-modal-dialogue": "image-text-to-text",
    "sentence-embedding": "feature-extraction",
    "text-embedding": "feature-extraction",
    "text-ranking": "text-ranking",
    "auto-speech-recognition": "automatic-speech-recognition",
    "text-to-speech": "text-to-speech",
    "text-to-image-synthesis": "text-to-image",
    "text-to-video-synthesis": "text-to-video",
}
_SORT = {
    "downloads": "DownloadsCount",
    "likes": "StarsCount",
    "last_modified": "GmtModified",
}


class ModelScopeClient:
    """Thin async client for the ModelScope hub API."""

    source = "modelscope"

    def __init__(
        self,
        endpoint: str = "https://www.modelscope.cn",
        token: Optional[str] = None,
        timeout: float = 20.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json", "User-Agent": "gpu-deck/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return httpx.AsyncClient(
            base_url=self.endpoint,
            headers=headers,
            timeout=self.timeout,
            transport=self._transport,
            follow_redirects=True,
        )

    @staticmethod
    def _data(resp: httpx.Response) -> Any:
        if resp.status_code == 404:
            raise ResourceNotFoundError(resource_type="ModelScope repo")
        if resp.status_code >= 400:
            raise ExternalServiceError(detail=f"ModelScope returned {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ExternalServiceError(detail="ModelScope returned a non-JSON body") from exc
        if isinstance(body, dict) and "Code" in body:
            if body.get("Code") == 404 or (
                body.get("Code") != 200 and "not" in str(body.get("Message", "")).lower()
            ):
                raise ResourceNotFoundError(resource_type="ModelScope repo")
            if body.get("Code") != 200:
                raise ExternalServiceError(
                    detail=f"ModelScope error: {body.get('Message') or body.get('Code')}"
                )
            return body.get("Data")
        return body

    async def search(
        self,
        query: str,
        *,
        limit: int = 25,
        author: Optional[str] = None,
        sort: str = "downloads",
        model_type: Optional[str] = None,
        fmt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {
            "Name": query,
            "PageNumber": 1,
            "PageSize": min(max(limit, 1), 100),
            "SortBy": _SORT.get(sort, "Default"),
            "Target": "",
            "SingleCriterion": [],
        }
        if author:
            body["Owner"] = author
        try:
            async with self._client() as client:
                data = self._data(await client.put("/api/v1/dolphin/models", json=body))
        except httpx.HTTPError as exc:
            logger.warning(f"ModelScope search failed: {type(exc).__name__}")
            raise ExternalServiceError(service="ModelScope") from exc
        rows = []
        if isinstance(data, dict):
            rows = (data.get("Model") or {}).get("Models") or data.get("Models") or []
        results = []
        for row in rows:
            owner = row.get("Path") or row.get("Owner") or ""
            name = row.get("Name") or ""
            repo_id = f"{owner}/{name}" if owner else name
            if author and owner != author:
                continue
            tasks = [
                t.get("Name") if isinstance(t, dict) else str(t) for t in (row.get("Tasks") or [])
            ]
            tags = [str(t) for t in (row.get("Tags") or [])] + [t for t in tasks if t]
            pipeline = next((_TASK_TO_PIPELINE[t] for t in tasks if t in _TASK_TO_PIPELINE), None)
            detected = detect.detect_type(pipeline_tag=pipeline, tags=tags, name=repo_id)
            if model_type and detected != model_type:
                continue
            lower_tags = [t.lower() for t in tags]
            formats = [
                f for f in ("gguf", "safetensors", "mlx") if f in lower_tags or f in name.lower()
            ]
            if fmt and fmt not in formats:
                continue
            results.append(
                {
                    "source": self.source,
                    "repo_id": repo_id,
                    "name": name,
                    "author": owner or None,
                    "pipeline_tag": pipeline,
                    "detected_type": detected,
                    "tags": tags,
                    "downloads": row.get("Downloads"),
                    "likes": row.get("Stars"),
                    "last_modified": _ts(row.get("LastUpdatedTime")),
                    "gated": False,
                    "private": bool(row.get("Visibility") == 1),
                    "formats": formats,
                    "library": (
                        row.get("Libraries") if isinstance(row.get("Libraries"), str) else None
                    ),
                    "total_size_bytes": None,
                    "already_registered_model_id": None,
                }
            )
        return results

    async def repo(self, repo_id: str, revision: Optional[str] = None) -> RemoteRepo:
        rev = revision or "master"
        enc = quote(repo_id, safe="/")
        try:
            async with self._client() as client:
                info = self._data(await client.get(f"/api/v1/models/{enc}")) or {}
                files_data = self._data(
                    await client.get(
                        f"/api/v1/models/{enc}/repo/files",
                        params={"Revision": rev, "Recursive": "True"},
                    )
                )
                config: Dict[str, Any] = {}
                raw_files = (
                    (files_data or {}).get("Files") if isinstance(files_data, dict) else files_data
                )
                if any(f.get("Path") == "config.json" for f in (raw_files or [])):
                    resp = await client.get(
                        f"/api/v1/models/{enc}/repo",
                        params={"Revision": rev, "FilePath": "config.json"},
                    )
                    if resp.status_code == 200:
                        try:
                            config = resp.json()
                        except ValueError:
                            config = {}
        except httpx.HTTPError as exc:
            logger.warning(f"ModelScope repo request failed: {type(exc).__name__}")
            raise ExternalServiceError(service="ModelScope") from exc
        files = []
        for entry in raw_files or []:
            if entry.get("Type") not in (None, "blob", "file"):
                continue
            path = entry.get("Path") or entry.get("Name")
            fmt = detect.detect_format(path)
            files.append(
                RemoteFile(
                    path=path,
                    size_bytes=int(entry.get("Size") or 0),
                    format=fmt,
                    sha256=entry.get("Sha256") or None,
                    is_required=detect.is_required_file(path, fmt),
                    lfs=bool(entry.get("IsLFS")),
                )
            )
        files.sort(key=lambda f: f.path)
        tasks = [
            t.get("Name") if isinstance(t, dict) else str(t) for t in (info.get("Tasks") or [])
        ]
        pipeline = next((_TASK_TO_PIPELINE[t] for t in tasks if t in _TASK_TO_PIPELINE), None)
        resolved = None
        for f in raw_files or []:
            if f.get("Revision"):
                resolved = f.get("Revision")
                break
        return RemoteRepo(
            source=self.source,
            repo_id=repo_id,
            revision=resolved or rev,
            files=files,
            config=config if isinstance(config, dict) else {},
            pipeline_tag=pipeline,
            tags=[t for t in tasks if t],
            gated=False,
            private=bool(info.get("Visibility") == 1),
            library=None,
        )


def _ts(value: Any) -> Optional[str]:
    """ModelScope epoch seconds -> RFC3339."""
    if value in (None, "", 0):
        return None
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None
