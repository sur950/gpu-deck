"""
Provider abstraction for cloud GPU VMs.

Every provider (AWS, Azure, GCP, DigitalOcean) implements ``CloudProvider``
with the official SDK (boto3, azure-mgmt-compute/network, google-cloud-compute)
or REST via httpx (DigitalOcean). Provider calls are blocking SDK calls for
three of the four, so the service runs them in a worker thread
(``asyncio.to_thread``) — implementations stay plain synchronous code.

Errors from providers are mapped to ``CloudError`` subclasses with stable
``error_code`` values so the UI can tell quota from capacity from auth
problems (``CLOUD_QUOTA_EXCEEDED``, ``CLOUD_CAPACITY_UNAVAILABLE``,
``CLOUD_AUTH_FAILED``, ``CLOUD_NOT_FOUND``, ``CLOUD_INVALID_REQUEST``,
``CLOUD_PROVIDER_ERROR``). Messages never contain credentials.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.core.exceptions import BaseAPIException
from app.core.logger import get_logger

logger = get_logger(__name__)

# Provider-neutral VM states (``CloudVm.state``).
VM_PENDING = "pending"
VM_RUNNING = "running"
VM_STOPPING = "stopping"
VM_STOPPED = "stopped"
VM_TERMINATING = "terminating"
VM_TERMINATED = "terminated"
VM_UNKNOWN = "unknown"

MANAGED_TAG = "gpu-deck:managed"
TAG_PREFIX = "gpu-deck:"


# ==========================================
# Errors
# ==========================================


class CloudError(BaseAPIException):
    """Base for provider failures; ``provider_code`` keeps the raw provider error code."""

    status_code_default = 502
    error_code_default = "CLOUD_PROVIDER_ERROR"

    def __init__(
        self,
        detail: str,
        *,
        provider: Optional[str] = None,
        provider_code: Optional[str] = None,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(
            status_code or self.status_code_default,
            detail,
            error_code=error_code or self.error_code_default,
        )
        self.provider = provider
        self.provider_code = provider_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.error_code,
            "message": self.detail,
            "provider": self.provider,
            "provider_code": self.provider_code,
        }


class CloudQuotaError(CloudError):
    status_code_default = 409
    error_code_default = "CLOUD_QUOTA_EXCEEDED"


class CloudCapacityError(CloudError):
    status_code_default = 409
    error_code_default = "CLOUD_CAPACITY_UNAVAILABLE"


class CloudAuthError(CloudError):
    status_code_default = 424
    error_code_default = "CLOUD_AUTH_FAILED"


class CloudNotFoundError(CloudError):
    status_code_default = 404
    error_code_default = "CLOUD_NOT_FOUND"


class CloudInvalidRequestError(CloudError):
    status_code_default = 422
    error_code_default = "CLOUD_INVALID_REQUEST"


class CloudCredentialError(CloudError):
    """The vault credential is missing or not in the provider's format."""

    status_code_default = 422
    error_code_default = "CLOUD_CREDENTIAL_INVALID"


# ==========================================
# Data shapes
# ==========================================


@dataclass
class CloudRegion:
    id: str
    name: str
    zones: List[str] = field(default_factory=list)
    available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CloudInstanceType:
    id: str
    gpu_model: Optional[str] = None
    gpu_vendor: Optional[str] = None
    gpu_count: int = 0
    gpu_vram_bytes: Optional[int] = None  # per GPU
    vcpus: Optional[int] = None
    memory_bytes: Optional[int] = None
    price_per_hour_usd: Optional[float] = None
    price_source: Optional[str] = None
    regions: List[str] = field(default_factory=list)
    family: Optional[str] = None
    description: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_vram_bytes(self) -> Optional[int]:
        if self.gpu_vram_bytes is None:
            return None
        return self.gpu_vram_bytes * max(self.gpu_count, 1)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["total_vram_bytes"] = self.total_vram_bytes
        return data


@dataclass
class CloudQuota:
    name: str
    used: Optional[float]
    limit: Optional[float]
    unit: str = "count"
    region: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VmSpec:
    """What to create. ``options`` holds provider launch options (never secrets)."""

    name: str
    region: str
    instance_type: str
    user_data: str
    tags: Dict[str, str]
    zone: Optional[str] = None
    image: Optional[str] = None
    disk_gb: int = 200
    client_token: Optional[str] = None  # idempotency key (our cloud_instances.id)
    gpu_count: int = 0
    gpu_model: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CloudVm:
    id: str
    state: str
    provider_state: Optional[str] = None
    public_ip: Optional[str] = None
    private_ip: Optional[str] = None
    zone: Optional[str] = None
    image: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CredentialCheck:
    ok: bool
    account: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==========================================
# Provider interface
# ==========================================


class CloudProvider(abc.ABC):
    """Synchronous provider API (run in a thread by the service)."""

    name: str = ""
    display_name: str = ""
    cluster_type: str = ""
    sdk: str = ""
    # Tag/label key normaliser (GCP labels cannot contain ':').
    supports_colon_tags: bool = True

    def __init__(self, credentials: Dict[str, Any], *, timeout: float = 60.0):
        self.credentials = credentials
        self.timeout = timeout

    # ----- catalog -----

    @abc.abstractmethod
    def check_credentials(self) -> CredentialCheck: ...

    @abc.abstractmethod
    def list_regions(self) -> List[CloudRegion]: ...

    @abc.abstractmethod
    def list_instance_types(self, region: str) -> List[CloudInstanceType]:
        """GPU instance types offered in ``region`` (prices attached when the API has them)."""

    @abc.abstractmethod
    def list_quotas(self, region: str) -> List[CloudQuota]: ...

    # ----- lifecycle -----

    @abc.abstractmethod
    def create_vm(self, spec: VmSpec) -> CloudVm: ...

    @abc.abstractmethod
    def get_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> Optional[CloudVm]:
        """None when the VM no longer exists."""

    @abc.abstractmethod
    def stop_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None: ...

    @abc.abstractmethod
    def start_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None: ...

    @abc.abstractmethod
    def terminate_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None: ...

    # ----- helpers -----

    def tag_key(self, key: str) -> str:
        if self.supports_colon_tags:
            return key
        return key.replace(":", "-").replace("/", "-").lower()

    def provider_tags(self, tags: Dict[str, str]) -> Dict[str, str]:
        return {self.tag_key(k): v for k, v in tags.items()}


def gib(value: float) -> int:
    return int(round(value * 1024**3))


def mib(value: float) -> int:
    return int(round(value * 1024**2))
