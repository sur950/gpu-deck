"""
Pydantic schemas for the warm model cache (admin.yaml tag "Model Cache").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CacheState(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    READY = "ready"
    EVICTING = "evicting"
    EVICTED = "evicted"
    ERROR = "error"


class CacheItem(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    node_name: Optional[str] = None
    model_id: Optional[uuid.UUID] = None
    model_name: Optional[str] = None
    model_version_id: uuid.UUID
    version: Optional[str] = None
    model_file_id: uuid.UUID
    path: Optional[str] = None
    local_path: str
    size_bytes: int = 0
    state: CacheState
    progress_pct: float = 0
    sha256_verified: bool = False
    pinned: bool = False
    pinned_reason: Optional[str] = None
    in_use: bool = False
    error_message: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CacheSummaryModel(BaseModel):
    model_id: uuid.UUID
    model_name: str
    model_version_id: uuid.UUID
    version: str
    size_bytes: int
    state: CacheState
    pinned: bool = False
    in_use: bool = False
    last_used_at: Optional[datetime] = None


class CacheSummary(BaseModel):
    node_id: uuid.UUID
    node_name: Optional[str] = None
    cache_dir: Optional[str] = None
    limit_bytes: Optional[int] = None
    used_bytes: int = 0
    free_disk_bytes: Optional[int] = None
    pinned_bytes: int = 0
    item_count: int = 0
    by_model: List[CacheSummaryModel] = Field(default_factory=list)


class PrefetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: List[uuid.UUID] = Field(..., min_length=1)
    model_version_id: uuid.UUID
    pin: bool = False
    pin_reason: Optional[str] = Field(None, max_length=255)


class EvictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: List[uuid.UUID] = Field(..., min_length=1)
    model_version_id: Optional[uuid.UUID] = None
    cache_item_ids: List[uuid.UUID] = Field(default_factory=list)
    unpinned_older_than_seconds: Optional[int] = Field(None, ge=0)
    target_free_bytes: Optional[int] = Field(None, ge=0)
    unpin_only: bool = False
    force: bool = False


class PinRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=255)


class CommandRefs(BaseModel):
    command_ids: List[uuid.UUID] = Field(default_factory=list)
    swap_operation_ids: List[uuid.UUID] = Field(default_factory=list)
