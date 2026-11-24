"""
Pydantic schemas for the engine registry (admin.yaml tag "Engines").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.schemas.catalog_schema import ModelFileFormat, ModelType
from pydantic import BaseModel, ConfigDict, Field


class EngineName(str, Enum):
    LLAMA_CPP = "llama_cpp"
    VLLM = "vllm"
    VLLM_OMNI = "vllm_omni"
    SGLANG = "sglang"
    SGLANG_DIFFUSION = "sglang_diffusion"
    TENSORRT_LLM = "tensorrt_llm"
    MLX = "mlx"
    MLX_AUDIO = "mlx_audio"
    WHISPER_CPP = "whisper_cpp"
    STABLE_DIFFUSION_CPP = "stable_diffusion_cpp"
    CUSTOM = "custom"


class EngineRuntime(str, Enum):
    NATIVE = "native"
    CONTAINER = "container"


class EngineVersionStatus(str, Enum):
    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class DeviceVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    HYGON = "hygon"
    METAX = "metax"
    MOORETHREADS = "moorethreads"
    THEAD = "thead"
    APPLE = "apple"


class NodeOs(str, Enum):
    LINUX = "linux"
    MACOS = "macos"


class CompatResult(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class DeploymentPreset(str, Enum):
    LOW_LATENCY = "low_latency"
    HIGH_THROUGHPUT = "high_throughput"


class EngineVersion(BaseModel):
    id: uuid.UUID
    engine_id: uuid.UUID
    engine_name: EngineName
    version: str
    status: EngineVersionStatus
    image: Optional[str] = None
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    launch_template: Dict[str, Any] = Field(default_factory=dict)
    default_launch_flags: Dict[str, Any] = Field(default_factory=dict)
    health_config: Dict[str, Any] = Field(default_factory=dict)
    metrics_config: Dict[str, Any] = Field(default_factory=dict)
    port_range: List[int] = Field(default_factory=lambda: [10240, 10340])
    min_driver_version: Optional[str] = None
    requirements: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EngineVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., min_length=1, max_length=100)
    image: Optional[str] = Field(None, max_length=512)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    launch_template: Dict[str, Any] = Field(default_factory=dict)
    default_launch_flags: Dict[str, Any] = Field(default_factory=dict)
    health_config: Dict[str, Any] = Field(default_factory=dict)
    metrics_config: Dict[str, Any] = Field(default_factory=dict)
    port_range: Optional[List[int]] = None
    min_driver_version: Optional[str] = Field(None, max_length=50)
    requirements: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    status: EngineVersionStatus = EngineVersionStatus.AVAILABLE


class EngineVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Optional[str] = Field(None, min_length=1, max_length=100)
    image: Optional[str] = Field(None, max_length=512)
    artifacts: Optional[Dict[str, Any]] = None
    launch_template: Optional[Dict[str, Any]] = None
    default_launch_flags: Optional[Dict[str, Any]] = None
    health_config: Optional[Dict[str, Any]] = None
    metrics_config: Optional[Dict[str, Any]] = None
    port_range: Optional[List[int]] = None
    min_driver_version: Optional[str] = Field(None, max_length=50)
    requirements: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None
    status: Optional[EngineVersionStatus] = None


class Engine(BaseModel):
    id: uuid.UUID
    name: EngineName
    display_name: str
    description: Optional[str] = None
    runtime: EngineRuntime
    supported_model_types: List[ModelType] = Field(default_factory=list)
    supported_vendors: List[DeviceVendor] = Field(default_factory=list)
    supported_os: List[NodeOs] = Field(default_factory=list)
    supported_formats: List[ModelFileFormat] = Field(default_factory=list)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    is_builtin: bool = False
    is_enabled: bool = True
    versions: List[EngineVersion] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EngineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    is_enabled: Optional[bool] = None
    supported_model_types: Optional[List[ModelType]] = None
    supported_vendors: Optional[List[DeviceVendor]] = None
    supported_formats: Optional[List[ModelFileFormat]] = None
    capabilities: Optional[Dict[str, Any]] = None


class CompatReason(BaseModel):
    code: str
    message: str
    severity: str = "info"


class CompatCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: uuid.UUID
    engine_version_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    device_vendor: Optional[DeviceVendor] = None
    context_size: Optional[int] = Field(None, ge=1)
    gpu_count: int = Field(1, ge=1)


class CompatCheck(BaseModel):
    id: uuid.UUID
    model_version_id: uuid.UUID
    engine_version_id: uuid.UUID
    engine_name: Optional[EngineName] = None
    engine_version: Optional[str] = None
    node_id: Optional[uuid.UUID] = None
    device_vendor: Optional[DeviceVendor] = None
    result: CompatResult
    score: int
    reasons: List[CompatReason] = Field(default_factory=list)
    estimated_vram_bytes: Optional[int] = None
    checked_at: datetime


class AutoPickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: uuid.UUID
    node_id: Optional[uuid.UUID] = None
    cluster_id: Optional[uuid.UUID] = None
    gpu_count: int = Field(1, ge=1)
    context_size: Optional[int] = Field(None, ge=1)
    preset: Optional[DeploymentPreset] = None


class AutoPickResult(BaseModel):
    engine_version: Optional[EngineVersion] = None
    check: Optional[CompatCheck] = None
    alternatives: List[CompatCheck] = Field(default_factory=list)
    suggested_launch_flags: Dict[str, Any] = Field(default_factory=dict)
    suggested_tool_parser: Optional[str] = None
