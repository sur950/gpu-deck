"""
Pydantic schemas for the model catalog (admin.yaml tags Catalog / models).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[A-Za-z0-9._:-]+$"


class ModelSource(str, Enum):
    HUGGINGFACE = "huggingface"
    MODELSCOPE = "modelscope"
    LOCAL_PATH = "local_path"


class RemoteSource(str, Enum):
    HUGGINGFACE = "huggingface"
    MODELSCOPE = "modelscope"


class ModelType(str, Enum):
    CHAT = "chat"
    THINKING = "thinking"
    VISION = "vision"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    STT = "stt"
    TTS = "tts"
    IMAGE = "image"
    VIDEO = "video"


class ModelPriority(str, Enum):
    ALWAYS_ON = "always_on"
    NORMAL = "normal"
    EVICTABLE = "evictable"


class ModelFileFormat(str, Enum):
    GGUF = "gguf"
    SAFETENSORS = "safetensors"
    MLX = "mlx"
    PICKLE = "pickle"
    OTHER = "other"


class ModelVersionStatus(str, Enum):
    REGISTERED = "registered"
    VERIFYING = "verifying"
    READY = "ready"
    MISSING = "missing"
    BLOCKED = "blocked"


class CatalogSort(str, Enum):
    DOWNLOADS = "downloads"
    LIKES = "likes"
    LAST_MODIFIED = "last_modified"
    RELEVANCE = "relevance"


# ---------------------------------------------------------------- catalog


class CatalogSearchResult(BaseModel):
    source: ModelSource
    repo_id: str
    name: str
    author: Optional[str] = None
    pipeline_tag: Optional[str] = None
    detected_type: Optional[ModelType] = None
    tags: List[str] = Field(default_factory=list)
    downloads: Optional[int] = None
    likes: Optional[int] = None
    last_modified: Optional[str] = None
    gated: bool = False
    private: bool = False
    formats: List[ModelFileFormat] = Field(default_factory=list)
    library: Optional[str] = None
    total_size_bytes: Optional[int] = None
    already_registered_model_id: Optional[uuid.UUID] = None


class CatalogRemoteFile(BaseModel):
    path: str
    size_bytes: int
    sha256: Optional[str] = None
    format: ModelFileFormat
    is_required: bool = True
    lfs: bool = False


class SuggestedSettings(BaseModel):
    name: Optional[str] = None
    family: Optional[str] = None
    type: Optional[ModelType] = None
    tool_parser: Optional[str] = None
    quantization: Optional[str] = None
    context_size: Optional[int] = None
    parameter_count: Optional[int] = None
    ollama_name: Optional[str] = None
    lmstudio_name: Optional[str] = None
    publisher: Optional[str] = None
    repo_id: Optional[str] = None
    revision: Optional[str] = None


class CatalogRemoteFiles(BaseModel):
    source: ModelSource
    repo_id: str
    revision: Optional[str] = None
    files: List[CatalogRemoteFile]
    total_size_bytes: int
    has_pickle_only_weights: bool = False
    config: Dict[str, Any] = Field(default_factory=dict)
    suggested: SuggestedSettings = Field(default_factory=SuggestedSettings)


class LocalScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    node_id: Optional[uuid.UUID] = None
    recursive: bool = True
    max_depth: int = Field(default=4, ge=1, le=10)


class LocalScanKind(str, Enum):
    PLAIN = "plain"
    OLLAMA_BLOB = "ollama_blob"
    LMSTUDIO = "lmstudio"
    HF_CACHE = "hf_cache"
    MLX_DIR = "mlx_dir"


class LocalScanCandidate(BaseModel):
    path: str
    kind: LocalScanKind
    format: ModelFileFormat
    files: List[CatalogRemoteFile]
    total_size_bytes: int
    suggested: SuggestedSettings = Field(default_factory=SuggestedSettings)
    node_id: Optional[uuid.UUID] = Field(
        None,
        description="Set when the path is on a node (scan_path); register with "
        "metadata.node_id so versions are scanned/hashed there and deployments pinned to it",
    )


# ------------------------------------------------------------------ models


class ModelFile(BaseModel):
    id: uuid.UUID
    model_version_id: uuid.UUID
    path: str
    format: ModelFileFormat
    size_bytes: int
    sha256: Optional[str] = None
    is_required: bool = True
    shard_index: Optional[int] = None
    shard_count: Optional[int] = None
    cached_on_nodes: int = 0


class ModelVersion(BaseModel):
    id: uuid.UUID
    model_id: uuid.UUID
    version: str
    revision: Optional[str] = None
    quantization: Optional[str] = None
    format: ModelFileFormat
    status: ModelVersionStatus
    status_message: Optional[str] = None
    total_size_bytes: int = 0
    parameter_count: Optional[int] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    file_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None


class ModelVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., min_length=1, max_length=255)
    revision: Optional[str] = Field(None, max_length=255)
    quantization: Optional[str] = Field(None, max_length=50)
    files: List[str] = Field(default_factory=list)
    is_default: bool = False
    local_path: Optional[str] = None


class ModelVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Optional[str] = Field(None, min_length=1, max_length=255)
    quantization: Optional[str] = Field(None, max_length=50)
    is_default: Optional[bool] = None


class Model(BaseModel):
    id: uuid.UUID
    name: str
    display_name: Optional[str] = None
    source: ModelSource
    repo_id: str
    family: Optional[str] = None
    type: ModelType
    priority: ModelPriority
    description: Optional[str] = None
    tool_parser_default: Optional[str] = None
    trust_remote_code: bool = False
    allow_pickle: bool = False
    default_context_size: Optional[int] = None
    default_launch_flags: Dict[str, Any] = Field(default_factory=dict)
    aliases: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    default_version: Optional[ModelVersion] = None
    version_count: int = 0
    deployment_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255, pattern=NAME_PATTERN)
    display_name: Optional[str] = Field(None, max_length=255)
    source: ModelSource
    repo_id: str = Field(..., min_length=1, max_length=1024)
    family: Optional[str] = Field(None, max_length=100)
    type: ModelType
    priority: ModelPriority = ModelPriority.NORMAL
    description: Optional[str] = Field(None, max_length=4096)
    tool_parser_default: Optional[str] = Field(None, max_length=100)
    trust_remote_code: bool = False
    allow_pickle: bool = False
    default_context_size: Optional[int] = Field(None, ge=1)
    default_launch_flags: Dict[str, Any] = Field(default_factory=dict)
    aliases: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    initial_version: Optional[ModelVersionCreate] = None


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255, pattern=NAME_PATTERN)
    display_name: Optional[str] = Field(None, max_length=255)
    family: Optional[str] = Field(None, max_length=100)
    type: Optional[ModelType] = None
    priority: Optional[ModelPriority] = None
    description: Optional[str] = Field(None, max_length=4096)
    tool_parser_default: Optional[str] = Field(None, max_length=100)
    trust_remote_code: Optional[bool] = None
    allow_pickle: Optional[bool] = None
    default_context_size: Optional[int] = Field(None, ge=1)
    default_launch_flags: Optional[Dict[str, Any]] = None
    aliases: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class VerifyResult(BaseModel):
    model_version_id: uuid.UUID
    status: ModelVersionStatus
    files_checked: int
    mismatches: List[ModelFile] = Field(default_factory=list)
    pickle_blocked: bool = False
