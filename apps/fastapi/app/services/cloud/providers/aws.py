"""
AWS EC2 GPU VMs via boto3.

Credential (vault, provider ``aws``, type ``cloud_account``), JSON::

    {"access_key_id": "AKIA...", "secret_access_key": "...",
     "session_token": null, "role_arn": null, "external_id": null}

With ``role_arn`` the keys only call ``sts:AssumeRole`` and every other call
uses the temporary credentials. Required IAM actions: ``ec2:DescribeRegions``,
``ec2:DescribeInstanceTypes``, ``ec2:DescribeImages``, ``ec2:RunInstances``,
``ec2:CreateTags``, ``ec2:DescribeInstances``, ``ec2:StartInstances``,
``ec2:StopInstances``, ``ec2:TerminateInstances``, ``ssm:GetParameter``
(public DLAMI parameter), ``pricing:GetProducts``,
``servicequotas:ListServiceQuotas``, ``sts:GetCallerIdentity``.

Image: ``image`` (AMI id) or the latest AWS Deep Learning Base OSS NVIDIA
driver AMI (Ubuntu 22.04) resolved from its public SSM parameter. Launch
options (``clusters.config.cloud`` / request ``options``): ``subnet_id``,
``security_group_ids`` (must allow tcp/9443 from the API layer), ``key_name``,
``iam_instance_profile``, ``associate_public_ip``. IMDSv2 is required on
every VM; ``ClientToken`` = our row id makes RunInstances idempotent.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.services.cloud.base import (
    VM_PENDING,
    VM_RUNNING,
    VM_STOPPED,
    VM_STOPPING,
    VM_TERMINATED,
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
    mib,
)
from app.services.cloud.gpu_catalog import normalise_gpu_name, vendor_of

logger = get_logger(__name__)

DLAMI_PARAMETERS = {
    "x86_64": "/aws/service/deeplearning/ami/x86_64/"
    "base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
    "arm64": "/aws/service/deeplearning/ami/arm64/"
    "base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
}
# instanceFamily values the Pricing API uses for accelerator instances.
PRICING_FAMILIES = ("GPU instance", "Machine Learning ASIC Instances")
# Service Quotas names of the on-demand accelerator vCPU quotas.
GPU_QUOTA_MARKERS = (
    "G and VT instances",
    "P instances",
    "P4",
    "P5",
    "DL instances",
    "Inf instances",
    "Trn instances",
)

QUOTA_CODES = {
    "VcpuLimitExceeded",
    "InstanceLimitExceeded",
    "MaxSpotInstanceCountExceeded",
    "VolumeLimitExceeded",
    "AddressLimitExceeded",
}
CAPACITY_CODES = {"InsufficientInstanceCapacity", "InsufficientCapacity", "Unsupported"}
AUTH_CODES = {
    "AuthFailure",
    "UnauthorizedOperation",
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "AccessDenied",
    "AccessDeniedException",
    "OptInRequired",
}
STATE_MAP = {
    "pending": VM_PENDING,
    "running": VM_RUNNING,
    "stopping": VM_STOPPING,
    "stopped": VM_STOPPED,
    "shutting-down": VM_TERMINATING,
    "terminated": VM_TERMINATED,
}


def map_client_error(exc: Exception) -> CloudError:
    """botocore error -> CloudError subclass (message keeps the AWS text, never credentials)."""
    try:
        from botocore.exceptions import (
            ClientError,
            EndpointConnectionError,
            NoCredentialsError,
            PartialCredentialsError,
        )
    except ImportError:  # pragma: no cover - boto3 is a declared dependency
        return CloudError(str(exc), provider="aws")
    if isinstance(exc, CloudError):
        return exc
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return CloudCredentialError("AWS credential is incomplete", provider="aws")
    if isinstance(exc, EndpointConnectionError):
        return CloudError(f"AWS endpoint unreachable: {exc}", provider="aws")
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {}) or {}
        code = str(err.get("Code") or "")
        message = str(err.get("Message") or code or "AWS error")
        if code in QUOTA_CODES:
            return CloudQuotaError(message, provider="aws", provider_code=code)
        if code in CAPACITY_CODES:
            return CloudCapacityError(message, provider="aws", provider_code=code)
        if code in AUTH_CODES or code.startswith("AccessDenied"):
            return CloudAuthError(message, provider="aws", provider_code=code)
        if code.endswith("NotFound") or code == "ResourceNotFoundException":
            return CloudNotFoundError(message, provider="aws", provider_code=code)
        if code.startswith("Invalid") or code in (
            "MissingParameter",
            "ValidationException",
        ):
            return CloudInvalidRequestError(message, provider="aws", provider_code=code)
        return CloudError(message, provider="aws", provider_code=code)
    return CloudError(f"AWS call failed: {exc}", provider="aws")


class AwsProvider(CloudProvider):
    name = "aws"
    display_name = "Amazon Web Services"
    cluster_type = "cloud_aws"
    sdk = "boto3"

    def __init__(self, credentials: Dict[str, Any], *, timeout: float = 60.0, session: Any = None):
        super().__init__(credentials, timeout=timeout)
        self._session = session
        self._clients: Dict[tuple, Any] = {}

    # ----- clients -----

    @staticmethod
    def parse_credentials(raw: Dict[str, Any]) -> Dict[str, Any]:
        key = raw.get("access_key_id") or raw.get("aws_access_key_id")
        secret = raw.get("secret_access_key") or raw.get("aws_secret_access_key")
        if not key or not secret:
            raise CloudCredentialError(
                "AWS credential needs access_key_id and secret_access_key",
                provider="aws",
            )
        return {
            "access_key_id": key,
            "secret_access_key": secret,
            "session_token": raw.get("session_token") or raw.get("aws_session_token"),
            "role_arn": raw.get("role_arn"),
            "external_id": raw.get("external_id"),
        }

    def _boto_session(self):
        if self._session is not None:
            return self._session
        import boto3

        creds = self.credentials
        session = boto3.session.Session(
            aws_access_key_id=creds["access_key_id"],
            aws_secret_access_key=creds["secret_access_key"],
            aws_session_token=creds.get("session_token"),
        )
        if creds.get("role_arn"):
            try:
                params = {"RoleArn": creds["role_arn"], "RoleSessionName": "gpu-deck"}
                if creds.get("external_id"):
                    params["ExternalId"] = creds["external_id"]
                assumed = session.client("sts", region_name="us-east-1").assume_role(**params)
            except Exception as exc:  # noqa: BLE001
                raise map_client_error(exc) from exc
            tmp = assumed["Credentials"]
            session = boto3.session.Session(
                aws_access_key_id=tmp["AccessKeyId"],
                aws_secret_access_key=tmp["SecretAccessKey"],
                aws_session_token=tmp["SessionToken"],
            )
        self._session = session
        return session

    def _client(self, service: str, region: str):
        key = (service, region)
        if key not in self._clients:
            from botocore.config import Config

            config = Config(
                connect_timeout=min(self.timeout, 15),
                read_timeout=self.timeout,
                retries={"max_attempts": 3, "mode": "standard"},
                user_agent_extra="gpu-deck",
            )
            self._clients[key] = self._boto_session().client(
                service, region_name=region, config=config
            )
        return self._clients[key]

    def _call(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    # ----- catalog -----

    def check_credentials(self) -> CredentialCheck:
        try:
            ident = self._call(self._client("sts", "us-east-1").get_caller_identity)
        except CloudError as exc:
            return CredentialCheck(ok=False, detail=f"{exc.error_code}: {exc.detail}")
        return CredentialCheck(ok=True, account=str(ident.get("Account")), detail=ident.get("Arn"))

    def list_regions(self) -> List[CloudRegion]:
        out = self._call(self._client("ec2", "us-east-1").describe_regions, AllRegions=False)
        regions = [
            CloudRegion(id=r["RegionName"], name=r["RegionName"])
            for r in out.get("Regions", [])
            if r.get("OptInStatus") in (None, "opt-in-not-required", "opted-in")
        ]
        return sorted(regions, key=lambda r: r.id)

    def list_instance_types(self, region: str) -> List[CloudInstanceType]:
        ec2 = self._client("ec2", region)
        paginator = ec2.get_paginator("describe_instance_types")
        items: List[CloudInstanceType] = []
        try:
            for page in paginator.paginate(PaginationConfig={"PageSize": 100}):
                for it in page.get("InstanceTypes", []):
                    parsed = _parse_instance_type(it, region)
                    if parsed is not None:
                        items.append(parsed)
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        prices = self._prices(region)
        for item in items:
            price = prices.get(item.id)
            if price is not None:
                item.price_per_hour_usd = price
                item.price_source = "aws:pricing:on-demand:linux"
        return sorted(items, key=lambda i: (i.gpu_model or "", i.gpu_count, i.id))

    def _prices(self, region: str) -> Dict[str, float]:
        """On-demand Linux list price per hour per instance type (empty on any failure)."""
        try:
            pricing = self._client("pricing", "us-east-1")
            out: Dict[str, float] = {}
            for family in PRICING_FAMILIES:
                token: Optional[str] = None
                while True:
                    params: Dict[str, Any] = {
                        "ServiceCode": "AmazonEC2",
                        "FormatVersion": "aws_v1",
                        "MaxResults": 100,
                        "Filters": [
                            {
                                "Type": "TERM_MATCH",
                                "Field": "regionCode",
                                "Value": region,
                            },
                            {
                                "Type": "TERM_MATCH",
                                "Field": "instanceFamily",
                                "Value": family,
                            },
                            {
                                "Type": "TERM_MATCH",
                                "Field": "operatingSystem",
                                "Value": "Linux",
                            },
                            {
                                "Type": "TERM_MATCH",
                                "Field": "tenancy",
                                "Value": "Shared",
                            },
                            {
                                "Type": "TERM_MATCH",
                                "Field": "preInstalledSw",
                                "Value": "NA",
                            },
                            {
                                "Type": "TERM_MATCH",
                                "Field": "capacitystatus",
                                "Value": "Used",
                            },
                        ],
                    }
                    if token:
                        params["NextToken"] = token
                    page = pricing.get_products(**params)
                    for raw in page.get("PriceList", []):
                        parsed = parse_price_item(raw)
                        if parsed:
                            out[parsed[0]] = parsed[1]
                    token = page.get("NextToken")
                    if not token:
                        break
            return out
        except Exception as exc:  # noqa: BLE001 - prices are best effort
            logger.warning(f"aws pricing unavailable region={region}: {type(exc).__name__}")
            return {}

    def list_quotas(self, region: str) -> List[CloudQuota]:
        sq = self._client("service-quotas", region)
        quotas: List[CloudQuota] = []
        token: Optional[str] = None
        try:
            while True:
                params: Dict[str, Any] = {"ServiceCode": "ec2", "MaxResults": 100}
                if token:
                    params["NextToken"] = token
                page = sq.list_service_quotas(**params)
                for q in page.get("Quotas", []):
                    name = q.get("QuotaName", "")
                    if "On-Demand" in name and any(m in name for m in GPU_QUOTA_MARKERS):
                        quotas.append(
                            CloudQuota(
                                name=name,
                                used=None,
                                limit=q.get("Value"),
                                unit="vCPU",
                                region=region,
                            )
                        )
                token = page.get("NextToken")
                if not token:
                    break
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        return quotas

    # ----- lifecycle -----

    def resolve_image(self, region: str, instance_type: str) -> str:
        arch = "x86_64"
        try:
            out = self._client("ec2", region).describe_instance_types(InstanceTypes=[instance_type])
            archs = (
                (out.get("InstanceTypes") or [{}])[0]
                .get("ProcessorInfo", {})
                .get("SupportedArchitectures", [])
            )
            if archs and "x86_64" not in archs and "arm64" in archs:
                arch = "arm64"
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        param = DLAMI_PARAMETERS[arch]
        out = self._call(self._client("ssm", region).get_parameter, Name=param)
        return out["Parameter"]["Value"]

    def create_vm(self, spec: VmSpec) -> CloudVm:
        ec2 = self._client("ec2", spec.region)
        image = spec.image or self.resolve_image(spec.region, spec.instance_type)
        root_device = "/dev/sda1"
        try:
            images = ec2.describe_images(ImageIds=[image]).get("Images", [])
            if images and images[0].get("RootDeviceName"):
                root_device = images[0]["RootDeviceName"]
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        opts = spec.options or {}
        tags = [{"Key": k, "Value": str(v)[:255]} for k, v in spec.tags.items()]
        tags.append({"Key": "Name", "Value": spec.name})
        params: Dict[str, Any] = {
            "ImageId": image,
            "InstanceType": spec.instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": spec.user_data,
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": tags},
                {"ResourceType": "volume", "Tags": tags},
            ],
            "BlockDeviceMappings": [
                {
                    "DeviceName": root_device,
                    "Ebs": {
                        "VolumeSize": int(spec.disk_gb),
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                        "Encrypted": True,
                    },
                }
            ],
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        }
        if spec.client_token:
            params["ClientToken"] = spec.client_token[:64]
        if spec.zone:
            params["Placement"] = {"AvailabilityZone": spec.zone}
        if opts.get("key_name"):
            params["KeyName"] = opts["key_name"]
        if opts.get("iam_instance_profile"):
            profile = str(opts["iam_instance_profile"])
            params["IamInstanceProfile"] = (
                {"Arn": profile} if profile.startswith("arn:") else {"Name": profile}
            )
        groups = list(opts.get("security_group_ids") or [])
        if opts.get("subnet_id"):
            nic: Dict[str, Any] = {
                "DeviceIndex": 0,
                "SubnetId": opts["subnet_id"],
                "DeleteOnTermination": True,
            }
            if groups:
                nic["Groups"] = groups
            if "associate_public_ip" in opts:
                nic["AssociatePublicIpAddress"] = bool(opts["associate_public_ip"])
            params["NetworkInterfaces"] = [nic]
        elif groups:
            params["SecurityGroupIds"] = groups
        out = self._call(ec2.run_instances, **params)
        inst = out["Instances"][0]
        return _vm_from(inst)

    def get_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> Optional[CloudVm]:
        try:
            out = self._client("ec2", region).describe_instances(InstanceIds=[vm_id])
        except Exception as exc:  # noqa: BLE001
            mapped = map_client_error(exc)
            if isinstance(mapped, CloudNotFoundError):
                return None
            raise mapped from exc
        for res in out.get("Reservations", []):
            for inst in res.get("Instances", []):
                if inst.get("InstanceId") == vm_id:
                    return _vm_from(inst)
        return None

    def stop_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        self._call(self._client("ec2", region).stop_instances, InstanceIds=[vm_id])

    def start_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        self._call(self._client("ec2", region).start_instances, InstanceIds=[vm_id])

    def terminate_vm(self, vm_id: str, *, region: str, zone: Optional[str] = None) -> None:
        try:
            self._client("ec2", region).terminate_instances(InstanceIds=[vm_id])
        except Exception as exc:  # noqa: BLE001
            mapped = map_client_error(exc)
            if isinstance(mapped, CloudNotFoundError):
                return  # already gone
            raise mapped from exc


# ==========================================
# Parsing helpers (pure; unit-tested with recorded payloads)
# ==========================================


def _parse_instance_type(it: Dict[str, Any], region: str) -> Optional[CloudInstanceType]:
    gpu_info = it.get("GpuInfo") or {}
    accel = it.get("InferenceAcceleratorInfo") or it.get("NeuronInfo") or {}
    gpus = gpu_info.get("Gpus") or []
    if not gpus and not accel:
        return None
    vcpus = (it.get("VCpuInfo") or {}).get("DefaultVCpus")
    memory = (it.get("MemoryInfo") or {}).get("SizeInMiB")
    if gpus:
        first = gpus[0]
        model = normalise_gpu_name(first.get("Manufacturer"), first.get("Name"))
        count = sum(int(g.get("Count") or 0) for g in gpus)
        vram = (first.get("MemoryInfo") or {}).get("SizeInMiB")
        vendor = (first.get("Manufacturer") or "").lower() or vendor_of(model)
    else:
        devices = accel.get("Accelerators") or accel.get("NeuronDevices") or []
        first = devices[0] if devices else {}
        model = normalise_gpu_name(first.get("Manufacturer") or "AWS", first.get("Name"))
        count = sum(int(d.get("Count") or 0) for d in devices)
        vram = (first.get("MemoryInfo") or {}).get("SizeInMiB")
        vendor = "aws"
    archs = (it.get("ProcessorInfo") or {}).get("SupportedArchitectures") or []
    return CloudInstanceType(
        id=it["InstanceType"],
        gpu_model=model,
        gpu_vendor=vendor or None,
        gpu_count=count,
        gpu_vram_bytes=mib(vram) if vram else None,
        vcpus=int(vcpus) if vcpus else None,
        memory_bytes=mib(memory) if memory else None,
        regions=[region],
        family=it["InstanceType"].split(".", 1)[0],
        extra={"architectures": archs},
    )


def parse_price_item(raw: Any) -> Optional[tuple]:
    """One Pricing API ``PriceList`` entry -> (instance_type, usd_per_hour) or None."""
    item = json.loads(raw) if isinstance(raw, str) else raw
    attrs = (item.get("product") or {}).get("attributes") or {}
    itype = attrs.get("instanceType")
    if not itype:
        return None
    for term in ((item.get("terms") or {}).get("OnDemand") or {}).values():
        for dim in (term.get("priceDimensions") or {}).values():
            usd = (dim.get("pricePerUnit") or {}).get("USD")
            if usd is None or dim.get("unit") not in ("Hrs", "Hours", None):
                continue
            try:
                value = float(usd)
            except ValueError:
                continue
            if value > 0:
                return itype, value
    return None


def _vm_from(inst: Dict[str, Any]) -> CloudVm:
    raw_state = (inst.get("State") or {}).get("Name")
    return CloudVm(
        id=inst["InstanceId"],
        state=STATE_MAP.get(raw_state or "", VM_UNKNOWN),
        provider_state=raw_state,
        public_ip=inst.get("PublicIpAddress"),
        private_ip=inst.get("PrivateIpAddress"),
        zone=(inst.get("Placement") or {}).get("AvailabilityZone"),
        image=inst.get("ImageId"),
    )
