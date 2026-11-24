"""
Azure GPU VMs via azure-mgmt-compute / azure-mgmt-network.

Credential (vault, provider ``azure``, type ``service_account``), JSON of a
service principal with the Virtual Machine Contributor + Network Contributor
roles on the target resource group (Reader on the subscription for SKUs and
usages)::

    {"tenant_id": "...", "client_id": "...", "client_secret": "...",
     "subscription_id": "..."}

Launch options (``clusters.config.cloud`` / request ``options``):
``resource_group`` and ``subnet_id`` (required; the subnet's NSG must allow
tcp/9443 from the API layer), ``public_ip`` (bool), ``admin_username``,
``ssh_public_key`` (a throwaway key is generated when absent — the node is
managed through its agent, not SSH), ``os_disk_type``.

Image: ``image`` as ``publisher:offer:sku:version`` or an image resource id;
default the Azure HPC Ubuntu 22.04 image with NVIDIA drivers
(``microsoft-dsvm:ubuntu-hpc:2204:latest``). The VM is created with
``deleteOption: Delete`` on the NIC, OS disk and public IP so terminating the
VM removes everything GPU Deck created. List prices come from the public
Azure Retail Prices API (Linux, pay-as-you-go).

The SDK models are ``MutableMapping``s in REST wire format: request bodies are
plain dicts and responses are read with ``as_dict()``.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

import httpx
from app.core.logger import get_logger
from app.services.cloud.base import (
    VM_PENDING,
    VM_RUNNING,
    VM_STOPPED,
    VM_STOPPING,
    VM_TERMINATING,
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
from app.services.cloud.gpu_catalog import AZURE_PARTIAL_GPU, azure_gpu

logger = get_logger(__name__)

DEFAULT_IMAGE = "microsoft-dsvm:ubuntu-hpc:2204:latest"
QUOTA_CODES = {"QuotaExceeded", "OperationNotAllowed", "ResourceQuotaExceeded"}
CAPACITY_CODES = {
    "SkuNotAvailable",
    "ZonalAllocationFailed",
    "AllocationFailed",
    "OverconstrainedAllocationRequest",
    "OverconstrainedZonalAllocationRequest",
}
AUTH_CODES = {
    "AuthorizationFailed",
    "InvalidAuthenticationToken",
    "InvalidAuthenticationTokenTenant",
    "AuthenticationFailed",
    "LinkedAuthorizationFailed",
}
POWER_STATES = {
    "PowerState/starting": VM_PENDING,
    "PowerState/running": VM_RUNNING,
    "PowerState/stopping": VM_STOPPING,
    "PowerState/stopped": VM_STOPPED,
    "PowerState/deallocating": VM_STOPPING,
    "PowerState/deallocated": VM_STOPPED,
}
GPU_FAMILY_MARKERS = ("NC", "ND", "NV", "NG")


def map_azure_error(exc: Exception) -> CloudError:
    if isinstance(exc, CloudError):
        return exc
    try:
        from azure.core.exceptions import (
            ClientAuthenticationError,
            HttpResponseError,
            ResourceNotFoundError,
            ServiceRequestError,
        )
    except ImportError:  # pragma: no cover
        return CloudError(str(exc), provider="azure")
    if isinstance(exc, ClientAuthenticationError):
        return CloudAuthError(f"Azure authentication failed: {_first_line(exc)}", provider="azure")
    if isinstance(exc, ResourceNotFoundError):
        return CloudNotFoundError(_first_line(exc), provider="azure", provider_code="NotFound")
    if isinstance(exc, ServiceRequestError):
        return CloudError(f"Azure endpoint unreachable: {_first_line(exc)}", provider="azure")
    if isinstance(exc, HttpResponseError):
        code = ""
        message = _first_line(exc)
        if exc.error is not None:
            code = str(getattr(exc.error, "code", "") or "")
            message = str(getattr(exc.error, "message", "") or message)
        status = getattr(exc, "status_code", None)
        low = message.lower()
        if code in QUOTA_CODES and ("quota" in low or code != "OperationNotAllowed"):
            return CloudQuotaError(message, provider="azure", provider_code=code)
        if code in CAPACITY_CODES:
            return CloudCapacityError(message, provider="azure", provider_code=code)
        if code in AUTH_CODES or status in (401, 403):
            return CloudAuthError(message, provider="azure", provider_code=code or str(status))
        if status == 404:
            return CloudNotFoundError(message, provider="azure", provider_code=code or "404")
        if status == 400 or code.startswith("Invalid"):
            return CloudInvalidRequestError(message, provider="azure", provider_code=code)
        return CloudError(message, provider="azure", provider_code=code or str(status))
    return CloudError(f"Azure call failed: {exc}", provider="azure")


def _first_line(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


class AzureProvider(CloudProvider):
    name = "azure"
    display_name = "Microsoft Azure"
    cluster_type = "cloud_azure"
    sdk = "azure-mgmt-compute + azure-mgmt-network"

    def __init__(
        self,
        credentials: Dict[str, Any],
        *,
        timeout: float = 60.0,
        token_credential: Any = None,
        prices_url: str = "https://prices.azure.com/api/retail/prices",
        http_transport: Optional[httpx.BaseTransport] = None,
    ):
        super().__init__(credentials, timeout=timeout)
        self._token_credential = token_credential
        self._prices_url = prices_url
        self._http_transport = http_transport
        self._compute = None
        self._network = None

    @staticmethod
    def parse_credentials(raw: Dict[str, Any]) -> Dict[str, Any]:
        missing = [
            k
            for k in ("tenant_id", "client_id", "client_secret", "subscription_id")
            if not raw.get(k)
        ]
        if missing:
            raise CloudCredentialError(
                f"Azure credential is missing {', '.join(missing)}", provider="azure"
            )
        return {k: raw[k] for k in ("tenant_id", "client_id", "client_secret", "subscription_id")}

    # ----- clients -----

    def _credential(self):
        if self._token_credential is None:
            from azure.identity import ClientSecretCredential

            c = self.credentials
            self._token_credential = ClientSecretCredential(
                c["tenant_id"], c["client_id"], c["client_secret"]
            )
        return self._token_credential

    @property
    def compute(self):
        if self._compute is None:
            from azure.mgmt.compute import ComputeManagementClient

            self._compute = ComputeManagementClient(
                self._credential(),
                self.credentials["subscription_id"],
                connection_timeout=min(self.timeout, 15),
                read_timeout=self.timeout,
                user_agent="gpu-deck",
            )
        return self._compute

    @property
    def network(self):
        if self._network is None:
            from azure.mgmt.network import NetworkManagementClient

            self._network = NetworkManagementClient(
                self._credential(),
                self.credentials["subscription_id"],
                connection_timeout=min(self.timeout, 15),
                read_timeout=self.timeout,
                user_agent="gpu-deck",
            )
        return self._network

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_azure_error(exc) from exc

    # ----- catalog -----

    def check_credentials(self) -> CredentialCheck:
        try:
            first = self._call(lambda: next(iter(self.compute.usage.list("eastus")), None))
        except CloudError as exc:
            return CredentialCheck(ok=False, detail=f"{exc.error_code}: {exc.detail}")
        detail = "usage readable" if first is not None else "no usage rows"
        return CredentialCheck(ok=True, account=self.credentials["subscription_id"], detail=detail)

    def _gpu_skus(self, region: Optional[str] = None) -> List[Dict[str, Any]]:
        kwargs = {"filter": f"location eq '{region}'"} if region else {}
        out = []
        try:
            for sku in self.compute.resource_skus.list(**kwargs):
                data = sku.as_dict()
                if data.get("resourceType") != "virtualMachines":
                    continue
                caps = {c.get("name"): c.get("value") for c in data.get("capabilities") or []}
                gpus = _num(caps.get("GPUs"))
                if not gpus:
                    continue
                data["_caps"] = caps
                out.append(data)
        except Exception as exc:  # noqa: BLE001
            raise map_azure_error(exc) from exc
        return out

    def list_regions(self) -> List[CloudRegion]:
        regions: Dict[str, CloudRegion] = {}
        for sku in self._gpu_skus():
            if _restricted(sku):
                continue
            for info in sku.get("locationInfo") or []:
                loc = str(info.get("location") or "").lower()
                if not loc:
                    continue
                region = regions.setdefault(loc, CloudRegion(id=loc, name=loc))
                for zone in info.get("zones") or []:
                    if zone not in region.zones:
                        region.zones.append(zone)
        for region in regions.values():
            region.zones.sort()
        return sorted(regions.values(), key=lambda r: r.id)

    def list_instance_types(self, region: str) -> List[CloudInstanceType]:
        items: List[CloudInstanceType] = []
        for sku in self._gpu_skus(region):
            if _restricted(sku, region):
                continue
            caps = sku["_caps"]
            name = sku.get("name") or ""
            model, vendor, vram = azure_gpu(name)
            gpus = _num(caps.get("GPUs")) or 0
            partial = AZURE_PARTIAL_GPU.match(name)
            zones: List[str] = []
            for info in sku.get("locationInfo") or []:
                zones.extend(info.get("zones") or [])
            items.append(
                CloudInstanceType(
                    id=name,
                    gpu_model=model,
                    gpu_vendor=vendor,
                    gpu_count=int(gpus),
                    gpu_vram_bytes=vram,
                    vcpus=int(_num(caps.get("vCPUs")) or 0) or None,
                    memory_bytes=gib(_num(caps.get("MemoryGB")) or 0) or None,
                    regions=[region],
                    family=sku.get("family"),
                    extra={"zones": sorted(set(zones)), "partial_gpu": bool(partial)},
                )
            )
        prices = self._prices(region, [i.id for i in items])
        for item in items:
            if item.id in prices:
                item.price_per_hour_usd = prices[item.id]
                item.price_source = "azure:retail-prices:consumption:linux"
        return sorted(items, key=lambda i: (i.gpu_model or "~", i.gpu_count, i.id))

    def _prices(self, region: str, sizes: List[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        if not sizes:
            return out
        try:
            with httpx.Client(timeout=self.timeout, transport=self._http_transport) as client:
                for start in range(0, len(sizes), 10):
                    chunk = sizes[start : start + 10]
                    skus = " or ".join(f"armSkuName eq '{s}'" for s in chunk)
                    flt = (
                        "serviceName eq 'Virtual Machines' and priceType eq 'Consumption' "
                        f"and armRegionName eq '{region}' and ({skus})"
                    )
                    url: Optional[str] = self._prices_url
                    params: Optional[Dict[str, str]] = {"$filter": flt}
                    pages = 0
                    while url and pages < 20:
                        resp = client.get(url, params=params)
                        resp.raise_for_status()
                        body = resp.json()
                        for row in body.get("Items") or []:
                            parsed = parse_retail_price(row)
                            if parsed and (parsed[0] not in out or parsed[1] < out[parsed[0]]):
                                out[parsed[0]] = parsed[1]
                        url, params, pages = body.get("NextPageLink"), None, pages + 1
        except Exception as exc:  # noqa: BLE001 - prices are best effort
            logger.warning(f"azure retail prices unavailable region={region}: {type(exc).__name__}")
        return out

    def list_quotas(self, region: str) -> List[CloudQuota]:
        quotas: List[CloudQuota] = []
        try:
            for usage in self.compute.usage.list(region):
                data = usage.as_dict()
                name = data.get("name") or {}
                value = str(name.get("value") or "")
                label = str(name.get("localizedValue") or value)
                is_gpu_family = value.startswith("standard") and any(
                    value[len("standard") :].upper().startswith(m) for m in GPU_FAMILY_MARKERS
                )
                if value == "cores" or is_gpu_family:
                    quotas.append(
                        CloudQuota(
                            name=label,
                            used=data.get("currentValue"),
                            limit=data.get("limit"),
                            unit="vCPU",
                            region=region,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            raise map_azure_error(exc) from exc
        return quotas

    # ----- lifecycle -----

    def create_vm(self, spec: VmSpec) -> CloudVm:
        opts = spec.options or {}
        rg = opts.get("resource_group")
        subnet = opts.get("subnet_id")
        if not rg or not subnet:
            raise CloudInvalidRequestError(
                "Azure needs options.resource_group and options.subnet_id "
                "(set them in the cluster config under cloud)",
                provider="azure",
            )
        name = azure_name(spec.name)
        tags = {k: str(v)[:256] for k, v in spec.tags.items()}
        ip_config: Dict[str, Any] = {
            "name": "ipconfig1",
            "properties": {
                "subnet": {"id": subnet},
                "privateIPAllocationMethod": "Dynamic",
            },
        }
        if opts.get("public_ip"):
            pip = self._call(
                self.network.public_ip_addresses.begin_create_or_update,
                rg,
                f"{name}-ip",
                {
                    "location": spec.region,
                    "tags": tags,
                    "sku": {"name": "Standard"},
                    "zones": [spec.zone] if spec.zone else None,
                    "properties": {
                        "publicIPAllocationMethod": "Static",
                        "deleteOption": "Delete",
                    },
                },
            ).result()
            ip_config["properties"]["publicIPAddress"] = {
                "id": pip.as_dict()["id"],
                "properties": {"deleteOption": "Delete"},
            }
        nic_body: Dict[str, Any] = {
            "location": spec.region,
            "tags": tags,
            "properties": {"ipConfigurations": [ip_config]},
        }
        if opts.get("network_security_group_id"):
            nic_body["properties"]["networkSecurityGroup"] = {
                "id": opts["network_security_group_id"]
            }
        nic = self._call(
            self.network.network_interfaces.begin_create_or_update,
            rg,
            f"{name}-nic",
            nic_body,
        ).result()
        nic_id = nic.as_dict()["id"]
        user = opts.get("admin_username") or "gpudeck"
        ssh_key = opts.get("ssh_public_key") or throwaway_ssh_public_key()
        vm_body: Dict[str, Any] = {
            "location": spec.region,
            "tags": tags,
            "properties": {
                "hardwareProfile": {"vmSize": spec.instance_type},
                "storageProfile": {
                    "imageReference": image_reference(spec.image or DEFAULT_IMAGE),
                    "osDisk": {
                        "createOption": "FromImage",
                        "diskSizeGB": int(spec.disk_gb),
                        "deleteOption": "Delete",
                        "managedDisk": {
                            "storageAccountType": opts.get("os_disk_type") or "Premium_LRS"
                        },
                    },
                },
                "osProfile": {
                    "computerName": name[:64],
                    "adminUsername": user,
                    "customData": base64.b64encode(spec.user_data.encode()).decode(),
                    "linuxConfiguration": {
                        "disablePasswordAuthentication": True,
                        "ssh": {
                            "publicKeys": [
                                {
                                    "path": f"/home/{user}/.ssh/authorized_keys",
                                    "keyData": ssh_key,
                                }
                            ]
                        },
                    },
                },
                "networkProfile": {
                    "networkInterfaces": [
                        {
                            "id": nic_id,
                            "properties": {"primary": True, "deleteOption": "Delete"},
                        }
                    ]
                },
            },
        }
        if spec.zone:
            vm_body["zones"] = [spec.zone]
        if opts.get("marketplace_plan"):
            # {"name","publisher","product"} for marketplace images that need a plan.
            vm_body["plan"] = opts["marketplace_plan"]
        # Do not wait for the LRO (polling=False): the lifecycle job polls the VM.
        try:
            self._call(
                self.compute.virtual_machines.begin_create_or_update,
                rg,
                name,
                vm_body,
                polling=False,
            )
        except CloudError:
            self._cleanup_network(rg, name, bool(opts.get("public_ip")))
            raise
        vm_id = (
            f"/subscriptions/{self.credentials['subscription_id']}/resourceGroups/{rg}"
            f"/providers/Microsoft.Compute/virtualMachines/{name}"
        )
        return CloudVm(id=vm_id, state=VM_PENDING, provider_state="Creating", zone=spec.zone)

    def _cleanup_network(self, rg: str, name: str, public_ip: bool) -> None:
        """Best effort: drop the NIC / public IP made for a VM whose create failed."""
        try:
            self.network.network_interfaces.begin_delete(rg, f"{name}-nic").result()
            if public_ip:
                self.network.public_ip_addresses.begin_delete(rg, f"{name}-ip", polling=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"azure cleanup of {name}-nic failed: {type(exc).__name__}")

    def get_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> Optional[CloudVm]:
        rg, name = split_vm_id(vm_id)
        try:
            vm = self.compute.virtual_machines.get(rg, name, expand="instanceView").as_dict()
        except Exception as exc:  # noqa: BLE001
            mapped = map_azure_error(exc)
            if isinstance(mapped, CloudNotFoundError):
                return None
            raise mapped from exc
        props = vm.get("properties") or {}
        provisioning = str(props.get("provisioningState") or "")
        statuses = [s.get("code") for s in (props.get("instanceView") or {}).get("statuses") or []]
        power = next((s for s in statuses if s and s.startswith("PowerState/")), None)
        if provisioning == "Deleting":
            state = VM_TERMINATING
        elif provisioning == "Failed":
            state = VM_UNKNOWN
        elif power:
            state = POWER_STATES.get(power, VM_UNKNOWN)
        else:
            state = VM_PENDING
        private_ip, public_ip = self._ips(vm)
        zones = vm.get("zones") or []
        return CloudVm(
            id=vm_id,
            state=state,
            provider_state=power or provisioning or None,
            private_ip=private_ip,
            public_ip=public_ip,
            zone=zones[0] if zones else zone,
            extra={"provisioning_state": provisioning},
        )

    def _ips(self, vm: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        nics = ((vm.get("properties") or {}).get("networkProfile") or {}).get(
            "networkInterfaces"
        ) or []
        if not nics:
            return None, None
        try:
            nic_rg, nic_name = _split_resource(nics[0]["id"])
            nic = self.network.network_interfaces.get(nic_rg, nic_name).as_dict()
            cfg = ((nic.get("properties") or {}).get("ipConfigurations") or [{}])[0]
            cprops = cfg.get("properties") or {}
            private_ip = cprops.get("privateIPAddress")
            public_ip = None
            pip_ref = cprops.get("publicIPAddress") or {}
            if pip_ref.get("id"):
                pip_rg, pip_name = _split_resource(pip_ref["id"])
                pip = self.network.public_ip_addresses.get(pip_rg, pip_name).as_dict()
                public_ip = (pip.get("properties") or {}).get("ipAddress")
            return private_ip, public_ip
        except Exception as exc:  # noqa: BLE001 - IPs are informative
            logger.debug(f"azure ip lookup failed: {type(exc).__name__}")
            return None, None

    def stop_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        rg, name = split_vm_id(vm_id)
        self._call(self.compute.virtual_machines.begin_deallocate, rg, name, polling=False)

    def start_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        rg, name = split_vm_id(vm_id)
        self._call(self.compute.virtual_machines.begin_start, rg, name, polling=False)

    def terminate_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        rg, name = split_vm_id(vm_id)
        try:
            self.compute.virtual_machines.begin_delete(rg, name, polling=False)
        except Exception as exc:  # noqa: BLE001
            mapped = map_azure_error(exc)
            if isinstance(mapped, CloudNotFoundError):
                return
            raise mapped from exc


# ==========================================
# Helpers (pure)
# ==========================================


def azure_name(name: str) -> str:
    """Azure VM names: letters, digits and '-' (1-64), not starting/ending with '-'."""
    cleaned = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in name).strip("-")
    return (cleaned or "gpudeck-node")[:64]


def image_reference(image: str) -> Dict[str, Any]:
    if image.startswith("/"):
        return {"id": image}
    parts = image.split(":")
    if len(parts) != 4:
        raise CloudInvalidRequestError(
            "Azure image must be publisher:offer:sku:version or an image resource id",
            provider="azure",
        )
    publisher, offer, sku, version = parts
    return {"publisher": publisher, "offer": offer, "sku": sku, "version": version}


def split_vm_id(vm_id: str) -> Tuple[str, str]:
    return _split_resource(vm_id)


def _split_resource(resource_id: str) -> Tuple[str, str]:
    parts = resource_id.strip("/").split("/")
    lowered = [p.lower() for p in parts]
    try:
        rg = parts[lowered.index("resourcegroups") + 1]
    except (ValueError, IndexError) as exc:
        raise CloudInvalidRequestError(
            f"not an Azure resource id: {resource_id}", provider="azure"
        ) from exc
    return rg, parts[-1]


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _restricted(sku: Dict[str, Any], region: Optional[str] = None) -> bool:
    """True when the subscription cannot deploy this size (anywhere / in ``region``)."""
    for r in sku.get("restrictions") or []:
        if r.get("reasonCode") != "NotAvailableForSubscription":
            continue
        if r.get("type") == "Location":
            values = [v.lower() for v in r.get("values") or []]
            if region is None or region.lower() in values:
                return True
    return False


def parse_retail_price(row: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """Azure Retail Prices item -> (armSkuName, usd/hour) for Linux pay-as-you-go."""
    sku = row.get("armSkuName")
    if not sku or row.get("type") not in (None, "Consumption"):
        return None
    if str(row.get("currencyCode") or "USD") != "USD":
        return None
    if "windows" in str(row.get("productName") or "").lower():
        return None
    sku_name = str(row.get("skuName") or "")
    if "spot" in sku_name.lower() or "low priority" in sku_name.lower():
        return None
    if str(row.get("unitOfMeasure") or "1 Hour") != "1 Hour":
        return None
    try:
        price = float(row.get("retailPrice") or row.get("unitPrice"))
    except (TypeError, ValueError):
        return None
    return (sku, price) if price > 0 else None


def throwaway_ssh_public_key() -> str:
    """An Ed25519 public key whose private half is discarded (Azure requires a login method)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate().public_key()
    raw = key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    return raw.decode() + " gpu-deck-unused"
