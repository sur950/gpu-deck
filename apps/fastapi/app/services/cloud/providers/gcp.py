"""
Google Cloud GPU VMs via google-cloud-compute.

Credential (vault, provider ``gcp``, type ``service_account``): the JSON key
of a service account with ``roles/compute.instanceAdmin.v1`` (plus
``roles/iam.serviceAccountUser`` when ``options.service_account_email`` is
set). ``project_id`` comes from the key unless ``options.project`` overrides.

Instance types: accelerator-optimised machine types (A2/A3/A4/G2/G4 — GPUs
are part of the machine type) and N1 machines with attachable GPUs, offered
as ``n1-standard-<cpus>+<accelerator>x<count>`` (e.g.
``n1-standard-8+nvidia-tesla-t4x1``). The Compute API has no prices, so GCP
types carry ``price_per_hour_usd = null`` (fill a price plan by hand).

Launch options: ``zone`` (else the first zone offering the type),
``network`` / ``subnetwork`` (firewall must allow tcp/9443 from the API layer
to instances tagged ``gpu-deck-node``), ``public_ip`` (bool), ``disk_type``,
``service_account_email``. Image: ``image`` (``projects/.../global/images/...``)
or the Deep Learning VM CUDA image family with ``install-nvidia-driver=True``.
The insert carries ``request_id`` (our row id) so a retried insert is
idempotent. GCP labels cannot contain ``:``: tags become ``gpu-deck-*`` labels.
The bootstrap script is the instance ``startup-script`` (it exits early on
reboots once the agent is installed).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.services.cloud.base import (
    VM_PENDING,
    VM_RUNNING,
    VM_STOPPED,
    VM_STOPPING,
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
    mib,
)
from app.services.cloud.gpu_catalog import gcp_accelerator, gcp_machine_accelerator

logger = get_logger(__name__)

DEFAULT_IMAGE = (
    "projects/deeplearning-platform-release/global/images/family/common-cu124-ubuntu-2204-py310"
)
# GCP "TERMINATED" means stopped (the VM still exists).
STATUS_MAP = {
    "PROVISIONING": VM_PENDING,
    "STAGING": VM_PENDING,
    "RUNNING": VM_RUNNING,
    "STOPPING": VM_STOPPING,
    "SUSPENDING": VM_STOPPING,
    "SUSPENDED": VM_STOPPED,
    "STOPPED": VM_STOPPED,
    "TERMINATED": VM_STOPPED,
    "REPAIRING": VM_PENDING,
}
N1_SHAPES = {
    1: "n1-standard-8",
    2: "n1-standard-16",
    4: "n1-standard-32",
    8: "n1-standard-64",
}
N1_ATTACHABLE = {
    "nvidia-tesla-t4",
    "nvidia-tesla-v100",
    "nvidia-tesla-p100",
    "nvidia-tesla-p4",
}
CAPACITY_MARKERS = (
    "ZONE_RESOURCE_POOL_EXHAUSTED",
    "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
    "does not have enough resources",
)
_COMBO = re.compile(r"^(?P<machine>[a-z0-9-]+)\+(?P<accel>[a-z0-9-]+)x(?P<count>\d+)$")


def map_gcp_error(exc: Exception) -> CloudError:
    if isinstance(exc, CloudError):
        return exc
    try:
        from google.api_core import exceptions as gexc
        from google.auth import exceptions as auth_exc
    except ImportError:  # pragma: no cover
        return CloudError(str(exc), provider="gcp")
    message = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, auth_exc.GoogleAuthError):
        return CloudAuthError(f"GCP authentication failed: {message}", provider="gcp")
    if any(m in message for m in CAPACITY_MARKERS):
        return CloudCapacityError(message, provider="gcp", provider_code=_reason(exc))
    reason = (_reason(exc) or "").lower()
    if isinstance(exc, gexc.TooManyRequests) or (
        isinstance(exc, (gexc.Forbidden, gexc.BadRequest, gexc.ResourceExhausted))
        and ("quota" in message.lower() or "quota" in reason)
    ):
        return CloudQuotaError(message, provider="gcp", provider_code=_reason(exc))
    if isinstance(exc, (gexc.Unauthorized, gexc.Forbidden, gexc.PermissionDenied)):
        return CloudAuthError(message, provider="gcp", provider_code=_reason(exc))
    if isinstance(exc, gexc.NotFound):
        return CloudNotFoundError(message, provider="gcp", provider_code="notFound")
    if isinstance(exc, (gexc.BadRequest, gexc.InvalidArgument)):
        return CloudInvalidRequestError(message, provider="gcp", provider_code=_reason(exc))
    if isinstance(exc, gexc.Conflict):
        return CloudInvalidRequestError(message, provider="gcp", provider_code="alreadyExists")
    return CloudError(f"GCP call failed: {message}", provider="gcp")


def _reason(exc: Exception) -> Optional[str]:
    reason = getattr(exc, "reason", None)
    if reason:
        return str(reason)
    code = getattr(exc, "code", None)
    return str(code) if code is not None else None


class GcpProvider(CloudProvider):
    name = "gcp"
    display_name = "Google Cloud"
    cluster_type = "cloud_gcp"
    sdk = "google-cloud-compute"
    supports_colon_tags = False

    def __init__(
        self,
        credentials: Dict[str, Any],
        *,
        timeout: float = 60.0,
        google_credentials: Any = None,
        project: Optional[str] = None,
    ):
        super().__init__(credentials, timeout=timeout)
        self._google_credentials = google_credentials
        self.project = project or credentials.get("project_id")
        self._clients: Dict[str, Any] = {}

    @staticmethod
    def parse_credentials(raw: Dict[str, Any]) -> Dict[str, Any]:
        if raw.get("type") != "service_account" or not raw.get("private_key"):
            raise CloudCredentialError(
                "GCP credential must be a service account JSON key", provider="gcp"
            )
        if not raw.get("project_id"):
            raise CloudCredentialError("GCP key has no project_id", provider="gcp")
        return dict(raw)

    def _creds(self):
        if self._google_credentials is None:
            from google.oauth2 import service_account

            self._google_credentials = service_account.Credentials.from_service_account_info(
                self.credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        return self._google_credentials

    def _client(self, name: str):
        if name not in self._clients:
            from google.cloud import compute_v1

            cls = getattr(compute_v1, name)
            self._clients[name] = cls(credentials=self._creds(), transport="rest")
        return self._clients[name]

    def _call(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_gcp_error(exc) from exc

    # ----- catalog -----

    def check_credentials(self) -> CredentialCheck:
        try:
            pager = self._call(
                self._client("RegionsClient").list,
                request={"project": self.project, "max_results": 1},
            )
            next(iter(pager), None)
        except CloudError as exc:
            return CredentialCheck(ok=False, detail=f"{exc.error_code}: {exc.detail}")
        return CredentialCheck(
            ok=True, account=self.project, detail=self.credentials.get("client_email")
        )

    def list_regions(self) -> List[CloudRegion]:
        out = []
        try:
            for region in self._client("RegionsClient").list(project=self.project):
                out.append(
                    CloudRegion(
                        id=region.name,
                        name=region.description or region.name,
                        zones=sorted(z.rsplit("/", 1)[-1] for z in region.zones),
                        available=region.status == "UP",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            raise map_gcp_error(exc) from exc
        return sorted(out, key=lambda r: r.id)

    def _region_zones(self, region: str) -> List[str]:
        reg = self._call(self._client("RegionsClient").get, project=self.project, region=region)
        return sorted(z.rsplit("/", 1)[-1] for z in reg.zones)

    def list_instance_types(self, region: str) -> List[CloudInstanceType]:
        found: Dict[str, CloudInstanceType] = {}
        try:
            for zone in self._region_zones(region):
                for mt in self._client("MachineTypesClient").list(project=self.project, zone=zone):
                    accels = list(mt.accelerators)
                    family_accel = gcp_machine_accelerator(mt.name)
                    if not accels and not family_accel:
                        continue
                    self._add_machine(found, mt, accels, family_accel, region, zone)
                for at in self._client("AcceleratorTypesClient").list(
                    project=self.project, zone=zone
                ):
                    if at.name not in N1_ATTACHABLE:
                        continue
                    model, vendor, vram = gcp_accelerator(at.name)
                    for count, machine in N1_SHAPES.items():
                        if count > (at.maximum_cards_per_instance or 1):
                            continue
                        cpus = int(machine.rsplit("-", 1)[-1])
                        type_id = f"{machine}+{at.name}x{count}"
                        item = found.get(type_id)
                        if item is None:
                            found[type_id] = CloudInstanceType(
                                id=type_id,
                                gpu_model=model,
                                gpu_vendor=vendor,
                                gpu_count=count,
                                gpu_vram_bytes=vram,
                                vcpus=cpus,
                                memory_bytes=mib(cpus * 3840),
                                regions=[region],
                                family="n1",
                                description=f"{machine} with {count} x {at.name}",
                                extra={"zones": [zone], "machine_type": machine},
                            )
                        elif zone not in item.extra["zones"]:
                            item.extra["zones"].append(zone)
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_gcp_error(exc) from exc
        return sorted(found.values(), key=lambda i: (i.gpu_model or "~", i.gpu_count, i.id))

    @staticmethod
    def _add_machine(found, mt, accels, family_accel, region: str, zone: str) -> None:
        item = found.get(mt.name)
        if item is not None:
            if zone not in item.extra["zones"]:
                item.extra["zones"].append(zone)
            return
        if accels:
            accel_type = accels[0].guest_accelerator_type
            count = sum(int(a.guest_accelerator_count or 0) for a in accels)
        else:
            accel_type, count = family_accel, 0
        model, vendor, vram = gcp_accelerator(accel_type)
        found[mt.name] = CloudInstanceType(
            id=mt.name,
            gpu_model=model,
            gpu_vendor=vendor,
            gpu_count=count,
            gpu_vram_bytes=vram,
            vcpus=int(mt.guest_cpus) if mt.guest_cpus else None,
            memory_bytes=mib(mt.memory_mb) if mt.memory_mb else None,
            regions=[region],
            family=mt.name.rsplit("-", 1)[0],
            description=mt.description or None,
            extra={"zones": [zone], "machine_type": mt.name},
        )

    def list_quotas(self, region: str) -> List[CloudQuota]:
        reg = self._call(self._client("RegionsClient").get, project=self.project, region=region)
        out = []
        for q in reg.quotas:
            metric = str(q.metric)
            if "GPUS" in metric or metric in ("CPUS", "INSTANCES"):
                out.append(
                    CloudQuota(
                        name=metric,
                        used=float(q.usage),
                        limit=float(q.limit),
                        unit="GPU" if "GPUS" in metric else "count",
                        region=region,
                    )
                )
        return out

    # ----- lifecycle -----

    def create_vm(self, spec: VmSpec) -> CloudVm:
        opts = spec.options or {}
        zone = spec.zone or opts.get("zone")
        if not zone:
            raise CloudInvalidRequestError("GCP needs a zone", provider="gcp")
        machine, accel, count = split_instance_type(spec.instance_type)
        name = gcp_name(spec.name)
        disk_type = opts.get("disk_type") or "pd-balanced"
        network_if: Dict[str, Any] = {}
        if opts.get("network"):
            network_if["network"] = opts["network"]
        if opts.get("subnetwork"):
            network_if["subnetwork"] = opts["subnetwork"]
        if opts.get("public_ip"):
            network_if["access_configs"] = [{"name": "External NAT", "type_": "ONE_TO_ONE_NAT"}]
        labels = {k: gcp_label_value(v) for k, v in self.provider_tags(spec.tags).items()}
        instance: Dict[str, Any] = {
            "name": name,
            "machine_type": f"zones/{zone}/machineTypes/{machine}",
            "labels": labels,
            "tags": {"items": ["gpu-deck-node"]},
            "disks": [
                {
                    "boot": True,
                    "auto_delete": True,
                    "initialize_params": {
                        "source_image": spec.image or DEFAULT_IMAGE,
                        "disk_size_gb": int(spec.disk_gb),
                        "disk_type": f"zones/{zone}/diskTypes/{disk_type}",
                        "labels": labels,
                    },
                }
            ],
            "network_interfaces": [network_if],
            "scheduling": {
                "on_host_maintenance": "TERMINATE",
                "automatic_restart": True,
            },
            "metadata": {
                "items": [
                    {"key": "startup-script", "value": spec.user_data},
                    {"key": "install-nvidia-driver", "value": "True"},
                    {"key": "block-project-ssh-keys", "value": "TRUE"},
                ]
            },
        }
        if opts.get("ssh_public_key"):
            # Instance-level key (the cluster's vault SSH key); project keys stay blocked.
            user = opts.get("admin_username") or "gpudeck"
            instance["metadata"]["items"].append(
                {
                    "key": "ssh-keys",
                    "value": f"{user}:{str(opts['ssh_public_key']).strip()}",
                }
            )
        if accel:
            instance["guest_accelerators"] = [
                {
                    "accelerator_type": f"zones/{zone}/acceleratorTypes/{accel}",
                    "accelerator_count": count,
                }
            ]
        if opts.get("service_account_email"):
            instance["service_accounts"] = [
                {
                    "email": opts["service_account_email"],
                    "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                }
            ]
        request: Dict[str, Any] = {
            "project": self.project,
            "zone": zone,
            "instance_resource": instance,
        }
        if spec.client_token:
            request["request_id"] = spec.client_token
        operation = self._call(self._client("InstancesClient").insert, request=request)
        # Quota / capacity failures surface on the zone operation; wait briefly for them.
        try:
            operation.result(timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            if _is_timeout(exc):
                logger.info(f"gcp insert still running name={name}; the lifecycle job polls it")
            else:
                raise map_gcp_error(_operation_error(operation) or exc) from exc
        else:
            err = _operation_error(operation)
            if err is not None:
                raise err
        return CloudVm(id=name, state=VM_PENDING, provider_state="PROVISIONING", zone=zone)

    def get_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> Optional[CloudVm]:
        if not zone:
            raise CloudInvalidRequestError("GCP instance lookups need the zone", provider="gcp")
        try:
            inst = self._client("InstancesClient").get(
                project=self.project, zone=zone, instance=vm_id
            )
        except Exception as exc:  # noqa: BLE001
            mapped = map_gcp_error(exc)
            if isinstance(mapped, CloudNotFoundError):
                return None
            raise mapped from exc
        private_ip = public_ip = None
        if inst.network_interfaces:
            nic = inst.network_interfaces[0]
            private_ip = nic.network_i_p or None
            if nic.access_configs:
                public_ip = nic.access_configs[0].nat_i_p or None
        return CloudVm(
            id=vm_id,
            state=STATUS_MAP.get(inst.status, VM_UNKNOWN),
            provider_state=inst.status,
            private_ip=private_ip,
            public_ip=public_ip,
            zone=zone,
            extra={"status_message": inst.status_message or None},
        )

    def stop_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        self._call(
            self._client("InstancesClient").stop,
            project=self.project,
            zone=zone,
            instance=vm_id,
        )

    def start_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        self._call(
            self._client("InstancesClient").start,
            project=self.project,
            zone=zone,
            instance=vm_id,
        )

    def terminate_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        try:
            self._client("InstancesClient").delete(project=self.project, zone=zone, instance=vm_id)
        except Exception as exc:  # noqa: BLE001
            mapped = map_gcp_error(exc)
            if isinstance(mapped, CloudNotFoundError):
                return
            raise mapped from exc


# ==========================================
# Helpers (pure)
# ==========================================


def split_instance_type(type_id: str) -> Tuple[str, Optional[str], int]:
    """``n1-standard-8+nvidia-tesla-t4x1`` -> (machine, accelerator, count); plain types pass."""
    m = _COMBO.match(type_id)
    if m:
        return m.group("machine"), m.group("accel"), int(m.group("count"))
    return type_id, None, 0


def gcp_name(name: str) -> str:
    """RFC1035: lowercase letters, digits, '-', starting with a letter, <= 63 chars."""
    cleaned = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"gd-{cleaned}"
    return cleaned[:63].rstrip("-")


def gcp_label_value(value: Any) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", str(value).lower())[:63]


def _is_timeout(exc: Exception) -> bool:
    import concurrent.futures

    return isinstance(exc, (TimeoutError, concurrent.futures.TimeoutError))


def _operation_error(operation: Any) -> Optional[CloudError]:
    """Map a finished zone operation's error (quota / capacity live there)."""
    raw = getattr(operation, "_extended_operation", None)
    errors = []
    try:
        if raw is not None and raw.error and raw.error.errors:
            errors = [(e.code or "", e.message or "") for e in raw.error.errors]
    except Exception:  # noqa: BLE001
        errors = []
    if not errors:
        code = getattr(operation, "error_code", None)
        message = getattr(operation, "error_message", None)
        if not code and not message:
            return None
        errors = [(str(code or ""), str(message or ""))]
    reason, text = errors[0]
    if any(m in reason or m in text for m in CAPACITY_MARKERS):
        return CloudCapacityError(text or reason, provider="gcp", provider_code=reason)
    if "QUOTA" in reason.upper() or "quota" in text.lower():
        return CloudQuotaError(text or reason, provider="gcp", provider_code=reason)
    return CloudError(text or reason, provider="gcp", provider_code=reason)
