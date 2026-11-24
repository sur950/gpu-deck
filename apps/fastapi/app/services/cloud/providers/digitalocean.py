"""
DigitalOcean GPU Droplets via the REST API v2 with httpx.

Credential (vault, provider ``digitalocean``, type ``token``): a personal
access token with droplet read/write, or JSON ``{"token": "dop_v1_..."}``.

Instance types are the ``gpu-*`` sizes (``gpu_info`` gives model, count and
VRAM; ``price_hourly`` is the list price). Image: ``image`` slug/id or the
"AI/ML ready" GPU base image for the size (``gpu-h100x1-base``,
``gpu-h100x8-base``, ``gpu-amd-base``). Launch options: ``vpc_uuid`` (the
VPC firewall must allow tcp/9443 from the API layer), ``ssh_keys`` (ids or
fingerprints), ``monitoring``. DigitalOcean has no idempotency key, so a
create first looks for a droplet already tagged with our row id.

Note: a powered-off Droplet is still billed; stop exists for parity, the UI
should prefer terminate for cost.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from app.core.logger import get_logger
from app.services.cloud.base import (
    VM_PENDING,
    VM_RUNNING,
    VM_STOPPED,
    VM_UNKNOWN,
    CloudAuthError,
    CloudCapacityError,
    CloudCredentialError,
    CloudError,
    CloudInstanceType,
    CloudInvalidRequestError,
    CloudNotFoundError,
    CloudProvider,
    CloudQuota,
    CloudQuotaError,
    CloudRegion,
    CloudVm,
    CredentialCheck,
    VmSpec,
    gib,
)
from app.services.cloud.gpu_catalog import vendor_of

logger = get_logger(__name__)

STATUS_MAP = {
    "new": VM_PENDING,
    "active": VM_RUNNING,
    "off": VM_STOPPED,
    "archive": VM_STOPPED,
}


def do_tag(key: str, value: Any) -> str:
    """DigitalOcean tags are flat names: letters, digits, ':', '-', '_' (<= 255)."""
    raw = f"{key}:{value}"
    return "".join(ch if ch.isalnum() or ch in ":-_" else "-" for ch in raw)[:255]


def map_do_error(status: int, body: Dict[str, Any]) -> CloudError:
    code = str(body.get("id") or status)
    message = str(body.get("message") or f"DigitalOcean HTTP {status}")
    low = message.lower()
    if status in (401, 403):
        return CloudAuthError(message, provider="digitalocean", provider_code=code)
    if status == 404:
        return CloudNotFoundError(message, provider="digitalocean", provider_code=code)
    if status == 429:
        return CloudError(
            message,
            provider="digitalocean",
            provider_code=code,
            status_code=429,
            error_code="CLOUD_RATE_LIMITED",
        )
    if status == 422 and ("limit" in low or "quota" in low):
        return CloudQuotaError(message, provider="digitalocean", provider_code=code)
    if status == 422 and ("unavailable" in low or "not available" in low or "capacity" in low):
        return CloudCapacityError(message, provider="digitalocean", provider_code=code)
    if 400 <= status < 500:
        return CloudInvalidRequestError(message, provider="digitalocean", provider_code=code)
    return CloudError(message, provider="digitalocean", provider_code=code)


class DigitalOceanProvider(CloudProvider):
    name = "digitalocean"
    display_name = "DigitalOcean"
    cluster_type = "cloud_do"
    sdk = "httpx (REST v2)"

    def __init__(
        self,
        credentials: Dict[str, Any],
        *,
        timeout: float = 60.0,
        base_url: str = "https://api.digitalocean.com",
        transport: Optional[httpx.BaseTransport] = None,
    ):
        super().__init__(credentials, timeout=timeout)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {credentials['token']}",
                "Content-Type": "application/json",
                "User-Agent": "gpu-deck",
            },
        )

    @staticmethod
    def parse_credentials(raw: Dict[str, Any]) -> Dict[str, Any]:
        token = (
            raw.get("token") or raw.get("api_token") or raw.get("access_token") or raw.get("value")
        )
        if not token:
            raise CloudCredentialError(
                "DigitalOcean credential must be an API token", provider="digitalocean"
            )
        return {"token": str(token).strip()}

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CloudError(
                f"DigitalOcean API unreachable: {type(exc).__name__}",
                provider="digitalocean",
            ) from exc
        if resp.status_code == 204:
            return {}
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}
        if resp.status_code >= 400:
            raise map_do_error(resp.status_code, body if isinstance(body, dict) else {})
        return body

    def _paged(self, path: str, key: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        items: List[Any] = []
        query = dict(params or {})
        query.setdefault("per_page", 200)
        page = 1
        while page <= 50:
            body = self._request("GET", path, params={**query, "page": page})
            items.extend(body.get(key) or [])
            if not ((body.get("links") or {}).get("pages") or {}).get("next"):
                break
            page += 1
        return items

    # ----- catalog -----

    def check_credentials(self) -> CredentialCheck:
        try:
            account = self._request("GET", "/v2/account").get("account") or {}
        except CloudError as exc:
            return CredentialCheck(ok=False, detail=f"{exc.error_code}: {exc.detail}")
        return CredentialCheck(
            ok=account.get("status") in (None, "active"),
            account=account.get("email") or account.get("uuid"),
            detail=f"status={account.get('status')} droplet_limit={account.get('droplet_limit')}",
        )

    def list_regions(self) -> List[CloudRegion]:
        regions = self._paged("/v2/regions", "regions")
        gpu_sizes = {s["slug"] for s in self._paged("/v2/sizes", "sizes") if s.get("gpu_info")}
        out = []
        for r in regions:
            if not set(r.get("sizes") or []) & gpu_sizes:
                continue
            out.append(
                CloudRegion(
                    id=r["slug"],
                    name=r.get("name") or r["slug"],
                    available=bool(r.get("available")),
                )
            )
        return sorted(out, key=lambda r: r.id)

    def list_instance_types(self, region: str) -> List[CloudInstanceType]:
        items = []
        for size in self._paged("/v2/sizes", "sizes"):
            parsed = parse_size(size)
            if parsed is None or not size.get("available", True):
                continue
            if region and region not in (size.get("regions") or []):
                continue
            parsed.regions = [region] if region else list(size.get("regions") or [])
            items.append(parsed)
        return sorted(items, key=lambda i: (i.gpu_model or "~", i.gpu_count, i.id))

    def list_quotas(self, region: str) -> List[CloudQuota]:
        account = self._request("GET", "/v2/account").get("account") or {}
        droplets = self._request("GET", "/v2/droplets", params={"per_page": 1})
        total = ((droplets.get("meta") or {}).get("total")) if droplets else None
        return [
            CloudQuota(
                name="Droplets (account)",
                used=float(total) if total is not None else None,
                limit=account.get("droplet_limit"),
                unit="count",
                region=None,
            )
        ]

    # ----- lifecycle -----

    def create_vm(self, spec: VmSpec) -> CloudVm:
        opts = spec.options or {}
        tags = [do_tag(k, v) for k, v in spec.tags.items()]
        if spec.client_token and "gpu-deck:cloud-instance-id" not in spec.tags:
            tags.append(do_tag("gpu-deck:cloud-instance-id", spec.client_token))
        row_tag = spec.tags.get("gpu-deck:cloud-instance-id") or spec.client_token
        if row_tag:
            marker = do_tag("gpu-deck:cloud-instance-id", row_tag)
            existing = (
                self._request(
                    "GET", "/v2/droplets", params={"tag_name": marker, "per_page": 5}
                ).get("droplets")
                or []
            )
            if existing:
                logger.info(f"digitalocean create reused droplet {existing[0].get('id')}")
                return _vm_from(existing[0])
        body: Dict[str, Any] = {
            "name": droplet_name(spec.name),
            "region": spec.region,
            "size": spec.instance_type,
            "image": spec.image or default_image(spec.instance_type),
            "user_data": spec.user_data,
            "tags": tags,
            "monitoring": bool(opts.get("monitoring", True)),
            "ipv6": False,
        }
        if opts.get("vpc_uuid"):
            body["vpc_uuid"] = opts["vpc_uuid"]
        if opts.get("ssh_keys"):
            body["ssh_keys"] = list(opts["ssh_keys"])
        droplet = self._request("POST", "/v2/droplets", json=body).get("droplet") or {}
        return _vm_from(droplet)

    def get_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> Optional[CloudVm]:
        try:
            droplet = self._request("GET", f"/v2/droplets/{vm_id}").get("droplet") or {}
        except CloudNotFoundError:
            return None
        return _vm_from(droplet)

    def _action(self, vm_id: str, action: str) -> None:
        self._request("POST", f"/v2/droplets/{vm_id}/actions", json={"type": action})

    def stop_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        self._action(vm_id, "shutdown")

    def start_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        self._action(vm_id, "power_on")

    def terminate_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        try:
            self._request("DELETE", f"/v2/droplets/{vm_id}")
        except CloudNotFoundError:
            return


# ==========================================
# Helpers (pure)
# ==========================================


def parse_size(size: Dict[str, Any]) -> Optional[CloudInstanceType]:
    gpu = size.get("gpu_info") or {}
    if not gpu:
        return None
    vram = gpu.get("vram") or {}
    amount = vram.get("amount")
    unit = str(vram.get("unit") or "gib").lower()
    vram_bytes = None
    if amount:
        vram_bytes = gib(float(amount)) if unit in ("gib", "gb") else int(float(amount) * 1024**2)
    count = int(gpu.get("count") or 0)
    model = gpu.get("model")
    pretty = model.replace("_", " ").upper() if model else None
    vendor = vendor_of(model) or ("amd" if model and "mi" in model.lower() else "nvidia")
    return CloudInstanceType(
        id=size["slug"],
        gpu_model=pretty,
        gpu_vendor=vendor,
        gpu_count=count,
        gpu_vram_bytes=(vram_bytes // count) if (vram_bytes and count) else vram_bytes,
        vcpus=size.get("vcpus"),
        memory_bytes=int(size["memory"]) * 1024**2 if size.get("memory") else None,
        price_per_hour_usd=(float(size["price_hourly"]) if size.get("price_hourly") else None),
        price_source=("digitalocean:sizes:price_hourly" if size.get("price_hourly") else None),
        regions=list(size.get("regions") or []),
        family="gpu",
        description=size.get("description"),
        extra={"disk_gb": size.get("disk"), "vram_total_bytes": vram_bytes},
    )


def default_image(size_slug: str) -> str:
    slug = size_slug.lower()
    if "mi300" in slug or "amd" in slug:
        return "gpu-amd-base"
    if slug.endswith("x8"):
        return "gpu-h100x8-base"
    return "gpu-h100x1-base"


def droplet_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in name).strip("-.")
    return (cleaned or "gpudeck-node")[:255]


def _vm_from(droplet: Dict[str, Any]) -> CloudVm:
    status = droplet.get("status")
    public_ip = private_ip = None
    for net in (droplet.get("networks") or {}).get("v4") or []:
        if net.get("type") == "public" and not public_ip:
            public_ip = net.get("ip_address")
        elif net.get("type") == "private" and not private_ip:
            private_ip = net.get("ip_address")
    return CloudVm(
        id=str(droplet.get("id")),
        state=STATUS_MAP.get(status or "", VM_UNKNOWN),
        provider_state=status,
        public_ip=public_ip,
        private_ip=private_ip,
        zone=None,
        image=str((droplet.get("image") or {}).get("slug") or "") or None,
    )
