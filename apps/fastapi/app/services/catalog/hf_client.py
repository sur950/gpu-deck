"""
Hugging Face Hub client (public REST API, optional bearer token).

Endpoints used (all under ``HF_ENDPOINT``):

* ``GET /api/models?search=&author=&filter=&sort=&direction=-1&limit=`` — search
* ``GET /api/models/{repo}/revision/{rev}`` — model info (sha, siblings, config)
* ``GET /api/models/{repo}/tree/{rev}?recursive=true`` — files with sizes and LFS sha256
* ``GET /{repo}/resolve/{rev}/config.json`` — raw config

Only ``httpx`` is used (no ``huggingface_hub`` dependency) so the agent and the
control plane stay in sync on what "a file" means. A custom ``transport`` can
be injected for recorded-response tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.core.logger import get_logger
from app.services.catalog import detect

logger = get_logger(__name__)

_SORT_FIELDS = {
    "downloads": "downloads",
    "likes": "likes",
    "last_modified": "lastModified",
    "relevance": None,
}


@dataclass
class RemoteFile:
    path: str
    size_bytes: int
    format: str
    sha256: Optional[str] = None
    is_required: bool = True
    lfs: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "sha256": self.sha256,
            "is_required": self.is_required,
            "lfs": self.lfs,
        }


@dataclass
class RemoteRepo:
    source: str
    repo_id: str
    revision: Optional[str]
    files: List[RemoteFile]
    config: Dict[str, Any] = field(default_factory=dict)
    pipeline_tag: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    gated: bool = False
    private: bool = False
    library: Optional[str] = None

    @property
    def total_size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def formats(self) -> List[str]:
        seen = []
        for f in self.files:
            if f.format != "other" and f.format not in seen:
                seen.append(f.format)
        return seen

    @property
    def has_pickle_only_weights(self) -> bool:
        return detect.has_pickle_only_weights(f.format for f in self.files)

    def suggested(self) -> Dict[str, Any]:
        family = detect.detect_family(self.repo_id, architecture=self.config.get("model_type"))
        mtype = detect.detect_type(
            pipeline_tag=self.pipeline_tag,
            tags=self.tags,
            name=self.repo_id,
            config=self.config or None,
        )
        return {
            "name": detect.suggested_name(self.repo_id),
            "family": family,
            "type": mtype,
            "tool_parser": detect.default_tool_parser(family, "vllm"),
            "context_size": detect.context_from_config(self.config),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "files": [f.to_dict() for f in self.files],
            "total_size_bytes": self.total_size_bytes,
            "has_pickle_only_weights": self.has_pickle_only_weights,
            "config": detect.shape_from_hf_config(self.config) if self.config else {},
            "suggested": self.suggested(),
        }


def _has_shape(config: Dict[str, Any]) -> bool:
    src = config.get("text_config") if isinstance(config.get("text_config"), dict) else config
    return any(k in src for k in ("num_hidden_layers", "n_layer", "num_layers"))


class HuggingFaceClient:
    """Thin async client for the HF hub API."""

    source = "huggingface"

    def __init__(
        self,
        endpoint: str = "https://huggingface.co",
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

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> Any:
        try:
            resp = await client.get(path, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            logger.warning(f"HF request failed: {type(exc).__name__}")
            raise ExternalServiceError(service="Hugging Face") from exc
        if resp.status_code == 404:
            raise ResourceNotFoundError(resource_type="Hugging Face repo")
        if resp.status_code in (401, 403):
            raise ExternalServiceError(
                detail="Hugging Face denied access (gated/private repo or invalid token)"
            )
        if resp.status_code >= 400:
            raise ExternalServiceError(detail=f"Hugging Face returned {resp.status_code}")
        return resp.json()

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
        """Search models; returns ``CatalogSearchResult`` dicts (without registration info)."""
        filters: List[str] = []
        if fmt == "gguf":
            filters.append("gguf")
        elif fmt == "safetensors":
            filters.append("safetensors")
        elif fmt == "mlx":
            filters.append("mlx")
        params: Dict[str, Any] = {
            "search": query,
            "limit": min(max(limit, 1), 100),
            "author": author,
            "filter": ",".join(filters) if filters else None,
        }
        sort_field = _SORT_FIELDS.get(sort, "downloads")
        if sort_field:
            params["sort"] = sort_field
            params["direction"] = -1
        async with self._client() as client:
            rows = await self._get(client, "/api/models", **params)
        results = []
        for row in rows or []:
            tags = list(row.get("tags") or [])
            detected = detect.detect_type(
                pipeline_tag=row.get("pipeline_tag"), tags=tags, name=row.get("id")
            )
            if model_type and detected != model_type:
                continue
            formats = [t for t in ("gguf", "safetensors", "mlx") if t in tags]
            if "pytorch" in tags and not formats:
                formats.append("pickle")
            if fmt and fmt not in formats:
                continue
            repo_id = row.get("id") or row.get("modelId")
            results.append(
                {
                    "source": self.source,
                    "repo_id": repo_id,
                    "name": repo_id.split("/")[-1],
                    "author": row.get("author")
                    or (repo_id.split("/")[0] if "/" in repo_id else None),
                    "pipeline_tag": row.get("pipeline_tag"),
                    "detected_type": detected,
                    "tags": tags,
                    "downloads": row.get("downloads"),
                    "likes": row.get("likes"),
                    "last_modified": row.get("lastModified"),
                    "gated": bool(row.get("gated")),
                    "private": bool(row.get("private")),
                    "formats": formats,
                    "library": row.get("library_name"),
                    "total_size_bytes": None,
                    "already_registered_model_id": None,
                }
            )
        return results

    async def repo(self, repo_id: str, revision: Optional[str] = None) -> RemoteRepo:
        """Files (with LFS sha256), resolved commit and config of a repo."""
        rev = revision or "main"
        enc = quote(repo_id, safe="/")
        async with self._client() as client:
            info = await self._get(client, f"/api/models/{enc}/revision/{quote(rev, safe='')}")
            tree = await self._get(
                client,
                f"/api/models/{enc}/tree/{quote(rev, safe='')}",
                recursive="true",
            )
            config = info.get("config") if isinstance(info.get("config"), dict) else None
            # The info endpoint only carries a summary config; fetch the raw file
            # when the layer/head shape (needed for KV estimates) is missing.
            if not config or not _has_shape(config):
                has_config = any(
                    (e.get("path") == "config.json") for e in (tree or []) if isinstance(e, dict)
                )
                if has_config:
                    try:
                        resp = await client.get(f"/{enc}/resolve/{quote(rev, safe='')}/config.json")
                        if resp.status_code == 200:
                            config = resp.json()
                    except (httpx.HTTPError, ValueError):
                        config = config or {}
        files = []
        for entry in tree or []:
            if not isinstance(entry, dict) or entry.get("type") != "file":
                continue
            path = entry["path"]
            fmt = detect.detect_format(path)
            lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else None
            files.append(
                RemoteFile(
                    path=path,
                    size_bytes=int((lfs or {}).get("size") or entry.get("size") or 0),
                    format=fmt,
                    sha256=(lfs or {}).get("oid"),
                    is_required=detect.is_required_file(path, fmt),
                    lfs=lfs is not None,
                )
            )
        files.sort(key=lambda f: f.path)
        return RemoteRepo(
            source=self.source,
            repo_id=repo_id,
            revision=info.get("sha") or rev,
            files=files,
            config=config or {},
            pipeline_tag=info.get("pipeline_tag"),
            tags=list(info.get("tags") or []),
            gated=bool(info.get("gated")),
            private=bool(info.get("private")),
            library=info.get("library_name"),
        )
