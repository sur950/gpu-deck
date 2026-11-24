"""
Cloud providers against mocked / recorded provider APIs (no real cloud calls).

- AWS: real boto3 against ``moto``'s EC2/STS/SSM backend; Pricing and Service
  Quotas (not implemented by moto) through botocore's ``Stubber`` with
  recorded responses.
- Azure: the real azure-mgmt-compute / azure-mgmt-network clients over their
  requests transport, intercepted by ``responses`` with recorded ARM payloads;
  Retail Prices via an httpx MockTransport.
- GCP: the real google-cloud-compute REST clients with anonymous credentials,
  intercepted by ``responses``.
- DigitalOcean: httpx MockTransport with recorded API v2 payloads.
"""

from __future__ import annotations

import json
import re

import boto3
import httpx
import pytest
import responses
from app.services.cloud.base import (
    VM_PENDING,
    VM_RUNNING,
    VM_STOPPED,
    CloudAuthError,
    CloudCapacityError,
    CloudCredentialError,
    CloudInvalidRequestError,
    CloudNotFoundError,
    CloudQuotaError,
    VmSpec,
)
from botocore.stub import Stubber
from moto import mock_aws

pytestmark = pytest.mark.unit

GIB = 1024**3


def spec(**kw) -> VmSpec:
    base = dict(
        name="lab-g5-1",
        region="us-east-1",
        instance_type="g5.xlarge",
        user_data="#!/bin/bash\necho hi\n",
        tags={"gpu-deck:managed": "true", "gpu-deck:cloud-instance-id": "abc"},
        client_token="0b6f8f9e-0000-4000-8000-000000000001",
    )
    base.update(kw)
    return VmSpec(**base)


# ==========================================
# AWS
# ==========================================


@pytest.fixture
def aws():
    from app.services.cloud.providers.aws import AwsProvider

    with mock_aws():
        yield AwsProvider({"access_key_id": "AKIATEST", "secret_access_key": "secret"})


def test_aws_parse_credentials():
    from app.services.cloud.providers.aws import AwsProvider

    assert (
        AwsProvider.parse_credentials({"access_key_id": "a", "secret_access_key": "b"})[
            "access_key_id"
        ]
        == "a"
    )
    with pytest.raises(CloudCredentialError):
        AwsProvider.parse_credentials({"access_key_id": "a"})


def test_aws_catalog_and_lifecycle(aws):
    assert aws.check_credentials().ok is True
    regions = [r.id for r in aws.list_regions()]
    assert "us-east-1" in regions
    types = {t.id: t for t in aws.list_instance_types("us-east-1")}
    g5 = types["g5.xlarge"]
    assert g5.gpu_count == 1 and g5.gpu_model == "NVIDIA A10G"
    assert g5.gpu_vram_bytes == 22888 * 1024**2  # AWS reports the A10G as 22888 MiB
    assert "m5.large" not in types  # CPU-only types are filtered out
    ami = boto3.client("ec2", "us-east-1").describe_images(Owners=["amazon"])["Images"][0]
    vm = aws.create_vm(spec(image=ami["ImageId"], options={"key_name": None}))
    assert vm.id.startswith("i-") and vm.state in (VM_PENDING, VM_RUNNING)
    tags = boto3.client("ec2", "us-east-1").describe_tags(
        Filters=[{"Name": "resource-id", "Values": [vm.id]}]
    )["Tags"]
    assert {
        "Key": "gpu-deck:managed",
        "Value": "true",
        "ResourceType": "instance",
        "ResourceId": vm.id,
    } in tags
    assert aws.get_vm(vm.id, region="us-east-1").state == VM_RUNNING
    aws.stop_vm(vm.id, region="us-east-1")
    assert aws.get_vm(vm.id, region="us-east-1").state == VM_STOPPED
    aws.start_vm(vm.id, region="us-east-1")
    aws.terminate_vm(vm.id, region="us-east-1")
    assert aws.get_vm(vm.id, region="us-east-1").state == "terminated"
    assert aws.get_vm("i-0123456789abcdef0", region="us-east-1") is None
    aws.terminate_vm("i-0123456789abcdef0", region="us-east-1")  # already gone: no error


def test_aws_image_resolution_uses_dlami_parameter(aws):
    from app.services.cloud.providers.aws import DLAMI_PARAMETERS

    ssm = aws._client("ssm", "us-east-1")
    with Stubber(ssm) as stub:  # /aws/service/* is reserved in moto: recorded responses
        stub.add_response(
            "get_parameter",
            {
                "Parameter": {
                    "Name": DLAMI_PARAMETERS["x86_64"],
                    "Value": "ami-0dlami000000000",
                }
            },
            {"Name": DLAMI_PARAMETERS["x86_64"]},
        )
        stub.add_client_error(
            "get_parameter",
            service_error_code="ParameterNotFound",
            http_status_code=400,
        )
        assert aws.resolve_image("us-east-1", "g5.xlarge") == "ami-0dlami000000000"
        with pytest.raises(CloudNotFoundError):
            aws.resolve_image("us-east-1", "g5g.xlarge")  # arm64 -> arm64 parameter


PRICE_ITEM = {
    "product": {"attributes": {"instanceType": "g5.xlarge", "regionCode": "us-east-1"}},
    "terms": {
        "OnDemand": {
            "X.JRTCKXETXF": {
                "priceDimensions": {
                    "X.JRTCKXETXF.6YS6EN2CT7": {
                        "unit": "Hrs",
                        "pricePerUnit": {"USD": "1.0060000000"},
                    }
                }
            }
        }
    },
}


def test_aws_prices_and_quotas_from_recorded_responses():
    from app.services.cloud.providers.aws import AwsProvider, parse_price_item

    assert parse_price_item(json.dumps(PRICE_ITEM)) == ("g5.xlarge", 1.006)
    provider = AwsProvider({"access_key_id": "AKIATEST", "secret_access_key": "secret"})
    pricing = provider._client("pricing", "us-east-1")
    with Stubber(pricing) as stub:
        stub.add_response("get_products", {"PriceList": [json.dumps(PRICE_ITEM)]})
        stub.add_response("get_products", {"PriceList": []})
        assert provider._prices("us-east-1") == {"g5.xlarge": 1.006}
    sq = provider._client("service-quotas", "us-east-1")
    with Stubber(sq) as stub:
        stub.add_response(
            "list_service_quotas",
            {
                "Quotas": [
                    {
                        "QuotaName": "Running On-Demand G and VT instances",
                        "Value": 64.0,
                        "QuotaCode": "L-DB2E81BA",
                    },
                    {
                        "QuotaName": "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) "
                        "instances",
                        "Value": 1152.0,
                        "QuotaCode": "L-1216C47A",
                    },
                    {
                        "QuotaName": "Running On-Demand P instances",
                        "Value": 0.0,
                        "QuotaCode": "L-417A185B",
                    },
                ]
            },
        )
        quotas = provider.list_quotas("us-east-1")
    assert [(q.name, q.limit) for q in quotas] == [
        ("Running On-Demand G and VT instances", 64.0),
        ("Running On-Demand P instances", 0.0),
    ]


def test_aws_error_mapping():
    from app.services.cloud.providers.aws import map_client_error
    from botocore.exceptions import ClientError

    def err(code):
        return map_client_error(ClientError({"Error": {"Code": code, "Message": code}}, "Op"))

    assert isinstance(err("VcpuLimitExceeded"), CloudQuotaError)
    assert err("VcpuLimitExceeded").status_code == 409
    assert isinstance(err("InsufficientInstanceCapacity"), CloudCapacityError)
    assert isinstance(err("UnauthorizedOperation"), CloudAuthError)
    assert isinstance(err("InvalidInstanceID.NotFound"), CloudNotFoundError)
    assert isinstance(err("InvalidAMIID.Malformed"), CloudInvalidRequestError)
    assert err("VcpuLimitExceeded").error_code == "CLOUD_QUOTA_EXCEEDED"


# ==========================================
# Azure
# ==========================================

SUB = "00000000-0000-0000-0000-000000000001"
ARM = re.compile(r"https://management\.azure\.com/.*")
RG = f"/subscriptions/{SUB}/resourceGroups/gpu-rg/providers"
NIC_ID = f"{RG}/Microsoft.Network/networkInterfaces/lab-a100-nic"
VM_ID = f"{RG}/Microsoft.Compute/virtualMachines/lab-a100"
AZ_SKUS = {
    "value": [
        {
            "resourceType": "virtualMachines",
            "name": "Standard_NC24ads_A100_v4",
            "family": "standardNCADSA100v4Family",
            "locations": ["eastus"],
            "locationInfo": [{"location": "eastus", "zones": ["2", "1"]}],
            "capabilities": [
                {"name": "vCPUs", "value": "24"},
                {"name": "MemoryGB", "value": "220"},
                {"name": "GPUs", "value": "1"},
            ],
            "restrictions": [],
        },
        {
            "resourceType": "virtualMachines",
            "name": "Standard_NC6s_v3",
            "family": "standardNCSv3Family",
            "locations": ["eastus"],
            "locationInfo": [{"location": "eastus", "zones": []}],
            "capabilities": [
                {"name": "vCPUs", "value": "6"},
                {"name": "GPUs", "value": "1"},
            ],
            "restrictions": [
                {
                    "type": "Location",
                    "values": ["eastus"],
                    "reasonCode": "NotAvailableForSubscription",
                }
            ],
        },
        {
            "resourceType": "virtualMachines",
            "name": "Standard_D4s_v5",
            "locations": ["eastus"],
            "locationInfo": [{"location": "eastus"}],
            "capabilities": [{"name": "vCPUs", "value": "4"}],
        },
        {"resourceType": "disks", "name": "Premium_LRS", "locations": ["eastus"]},
    ]
}
AZ_PRICES = {
    "Items": [
        {
            "armSkuName": "Standard_NC24ads_A100_v4",
            "retailPrice": 3.673,
            "unitOfMeasure": "1 Hour",
            "type": "Consumption",
            "productName": "Virtual Machines NCadsA100v4 Series",
            "skuName": "NC24ads A100 v4",
            "currencyCode": "USD",
        },
        {
            "armSkuName": "Standard_NC24ads_A100_v4",
            "retailPrice": 0.9,
            "unitOfMeasure": "1 Hour",
            "type": "Consumption",
            "productName": "Virtual Machines NCadsA100v4 Series",
            "skuName": "NC24ads A100 v4 Spot",
            "currencyCode": "USD",
        },
        {
            "armSkuName": "Standard_NC24ads_A100_v4",
            "retailPrice": 4.9,
            "unitOfMeasure": "1 Hour",
            "type": "Consumption",
            "productName": "Virtual Machines NCadsA100v4 Series Windows",
            "skuName": "NC24ads A100 v4",
            "currencyCode": "USD",
        },
    ],
    "NextPageLink": None,
}


class FakeToken:
    def get_token(self, *scopes, **kwargs):
        from azure.core.credentials import AccessToken

        return AccessToken("fake-token", 4102444800)

    def get_token_info(self, *scopes, options=None):
        from azure.core.credentials import AccessTokenInfo

        return AccessTokenInfo("fake-token", 4102444800)


@pytest.fixture
def azure():
    from app.services.cloud.providers.azure import AzureProvider

    prices = httpx.MockTransport(lambda request: httpx.Response(200, json=AZ_PRICES))
    creds = {
        "tenant_id": "t",
        "client_id": "c",
        "client_secret": "s",
        "subscription_id": SUB,
    }
    return AzureProvider(creds, token_credential=FakeToken(), http_transport=prices, timeout=10)


@responses.activate
def test_azure_catalog(azure):
    responses.add(
        responses.GET,
        re.compile(r".*/providers/Microsoft\.Compute/skus.*"),
        json=AZ_SKUS,
    )
    responses.add(
        responses.GET,
        re.compile(r".*/locations/eastus/usages.*"),
        json={
            "value": [
                {
                    "currentValue": 0,
                    "limit": 24,
                    "unit": "Count",
                    "name": {
                        "value": "standardNCADSA100v4Family",
                        "localizedValue": "Standard NCADS_A100_v4 Family vCPUs",
                    },
                },
                {
                    "currentValue": 8,
                    "limit": 100,
                    "unit": "Count",
                    "name": {
                        "value": "cores",
                        "localizedValue": "Total Regional vCPUs",
                    },
                },
                {
                    "currentValue": 0,
                    "limit": 10,
                    "unit": "Count",
                    "name": {"value": "standardDSv5Family", "localizedValue": "DSv5"},
                },
            ]
        },
    )
    regions = azure.list_regions()
    assert [(r.id, r.zones) for r in regions] == [("eastus", ["1", "2"])]
    types = azure.list_instance_types("eastus")
    assert [t.id for t in types] == ["Standard_NC24ads_A100_v4"]  # restricted + CPU skus dropped
    a100 = types[0]
    assert a100.gpu_model == "NVIDIA A100 80GB" and a100.gpu_vram_bytes == 80 * GIB
    assert a100.vcpus == 24 and a100.memory_bytes == 220 * GIB
    assert a100.price_per_hour_usd == 3.673  # Linux pay-as-you-go, not spot/windows
    quotas = azure.list_quotas("eastus")
    assert {q.name: (q.used, q.limit) for q in quotas} == {
        "Standard NCADS_A100_v4 Family vCPUs": (0, 24),
        "Total Regional vCPUs": (8, 100),
    }
    assert azure.check_credentials().ok is True


@responses.activate
def test_azure_create_poll_stop_terminate(azure):
    put_nic = responses.add(
        responses.PUT,
        re.compile(r".*/networkInterfaces/lab-a100-nic\?.*"),
        json={
            "id": NIC_ID,
            "location": "eastus",
            "properties": {"provisioningState": "Succeeded"},
        },
    )
    put_vm = responses.add(
        responses.PUT,
        re.compile(r".*/virtualMachines/lab-a100\?.*"),
        status=201,
        json={
            "id": VM_ID,
            "location": "eastus",
            "properties": {"provisioningState": "Creating"},
        },
    )
    vm = azure.create_vm(
        spec(
            name="lab_a100",
            region="eastus",
            zone="1",
            instance_type="Standard_NC24ads_A100_v4",
            options={
                "resource_group": "gpu-rg",
                "subnet_id": "/subscriptions/x/subnets/s",
            },
        )
    )
    assert vm.id == VM_ID and vm.state == VM_PENDING
    body = json.loads(put_vm.calls[0].request.body)
    props = body["properties"]
    assert body["zones"] == ["1"] and body["tags"]["gpu-deck:managed"] == "true"
    assert props["storageProfile"]["imageReference"]["offer"] == "ubuntu-hpc"
    assert props["storageProfile"]["osDisk"]["deleteOption"] == "Delete"
    assert props["networkProfile"]["networkInterfaces"][0]["properties"]["deleteOption"] == "Delete"
    assert props["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"] is True
    import base64

    assert base64.b64decode(props["osProfile"]["customData"]).startswith(b"#!/bin/bash")
    assert (
        json.loads(put_nic.calls[0].request.body)["properties"]["ipConfigurations"][0][
            "properties"
        ]["subnet"]["id"]
        == "/subscriptions/x/subnets/s"
    )

    responses.add(
        responses.GET,
        re.compile(r".*/virtualMachines/lab-a100\?.*"),
        json={
            "id": VM_ID,
            "zones": ["1"],
            "properties": {
                "provisioningState": "Succeeded",
                "instanceView": {
                    "statuses": [
                        {"code": "ProvisioningState/succeeded"},
                        {"code": "PowerState/running"},
                    ]
                },
                "networkProfile": {"networkInterfaces": [{"id": NIC_ID}]},
            },
        },
    )
    responses.add(
        responses.GET,
        re.compile(r".*/networkInterfaces/lab-a100-nic\?.*"),
        json={
            "id": NIC_ID,
            "properties": {
                "ipConfigurations": [
                    {
                        "name": "ipconfig1",
                        "properties": {"privateIPAddress": "10.0.0.4"},
                    }
                ]
            },
        },
    )
    got = azure.get_vm(VM_ID, region="eastus")
    assert got.state == VM_RUNNING and got.private_ip == "10.0.0.4" and got.zone == "1"
    dealloc = responses.add(
        responses.POST,
        re.compile(r".*/virtualMachines/lab-a100/deallocate\?.*"),
        status=202,
    )
    azure.stop_vm(VM_ID, region="eastus")
    assert dealloc.call_count == 1
    delete = responses.add(
        responses.DELETE, re.compile(r".*/virtualMachines/lab-a100\?.*"), status=202
    )
    azure.terminate_vm(VM_ID, region="eastus")
    assert delete.call_count == 1


@responses.activate
def test_azure_quota_error_cleans_up_nic(azure):
    responses.add(
        responses.PUT,
        re.compile(r".*/networkInterfaces/lab-a100-nic\?.*"),
        json={"id": NIC_ID, "properties": {"provisioningState": "Succeeded"}},
    )
    responses.add(
        responses.PUT,
        re.compile(r".*/virtualMachines/lab-a100\?.*"),
        status=409,
        json={
            "error": {
                "code": "OperationNotAllowed",
                "message": "Operation could not be "
                "completed as it results in exceeding approved standardNCADSA100v4Family "
                "Cores quota. Current Limit: 0",
            }
        },
    )
    nic_delete = responses.add(
        responses.DELETE,
        re.compile(r".*/networkInterfaces/lab-a100-nic\?.*"),
        status=200,
    )
    with pytest.raises(CloudQuotaError) as exc:
        azure.create_vm(
            spec(
                name="lab-a100",
                region="eastus",
                instance_type="Standard_NC24ads_A100_v4",
                options={"resource_group": "gpu-rg", "subnet_id": "/s"},
            )
        )
    assert exc.value.provider_code == "OperationNotAllowed"
    assert nic_delete.call_count == 1


def test_azure_requires_network_options(azure):
    with pytest.raises(CloudInvalidRequestError):
        azure.create_vm(spec(region="eastus", options={}))


@responses.activate
def test_azure_not_found_and_auth(azure):
    responses.add(
        responses.GET,
        re.compile(r".*/virtualMachines/lab-a100\?.*"),
        status=404,
        json={"error": {"code": "ResourceNotFound", "message": "not found"}},
    )
    assert azure.get_vm(VM_ID, region="eastus") is None
    responses.add(
        responses.GET,
        re.compile(r".*/usages.*"),
        status=403,
        json={"error": {"code": "AuthorizationFailed", "message": "no access"}},
    )
    check = azure.check_credentials()
    assert check.ok is False and "CLOUD_AUTH_FAILED" in check.detail


def test_azure_helpers():
    from app.services.cloud.providers.azure import image_reference, split_vm_id

    assert split_vm_id(VM_ID) == ("gpu-rg", "lab-a100")
    assert image_reference("Canonical:ubuntu-24_04-lts:server:latest")["publisher"] == "Canonical"
    assert image_reference("/subscriptions/x/images/i") == {"id": "/subscriptions/x/images/i"}
    with pytest.raises(CloudInvalidRequestError):
        image_reference("bad")


# ==========================================
# GCP
# ==========================================

GCE = "https://compute.googleapis.com/compute/v1/projects/proj"


@pytest.fixture
def gcp():
    from app.services.cloud.providers.gcp import GcpProvider
    from google.auth.credentials import AnonymousCredentials

    key = {
        "type": "service_account",
        "project_id": "proj",
        "private_key": "x",
        "client_email": "sa@proj.iam.gserviceaccount.com",
    }
    return GcpProvider(key, google_credentials=AnonymousCredentials(), timeout=5)


def _gcp_catalog_responses():
    responses.add(
        responses.GET,
        f"{GCE}/regions/us-central1",
        json={
            "name": "us-central1",
            "status": "UP",
            "zones": [f"{GCE}/zones/us-central1-a", f"{GCE}/zones/us-central1-b"],
            "quotas": [
                {"metric": "NVIDIA_L4_GPUS", "limit": 8, "usage": 1},
                {"metric": "CPUS", "limit": 72, "usage": 4},
                {"metric": "SSD_TOTAL_GB", "limit": 500, "usage": 0},
            ],
        },
    )
    for zone in ("us-central1-a", "us-central1-b"):
        responses.add(
            responses.GET,
            re.compile(rf"{GCE}/zones/{zone}/machineTypes.*"),
            json={
                "items": [
                    {
                        "name": "g2-standard-8",
                        "guestCpus": 8,
                        "memoryMb": 32768,
                        "accelerators": [
                            {
                                "guestAcceleratorType": "nvidia-l4",
                                "guestAcceleratorCount": 1,
                            }
                        ],
                    },
                    {"name": "e2-standard-4", "guestCpus": 4, "memoryMb": 16384},
                ]
            },
        )
        responses.add(
            responses.GET,
            re.compile(rf"{GCE}/zones/{zone}/acceleratorTypes.*"),
            json=(
                {
                    "items": [
                        {"name": "nvidia-tesla-t4", "maximumCardsPerInstance": 2},
                        {"name": "nvidia-l4", "maximumCardsPerInstance": 8},
                    ]
                }
                if zone.endswith("a")
                else {"items": []}
            ),
        )


@responses.activate
def test_gcp_catalog(gcp):
    responses.add(
        responses.GET,
        re.compile(rf"{GCE}/regions(\?.*)?$"),
        json={
            "items": [
                {
                    "name": "us-central1",
                    "description": "us-central1",
                    "status": "UP",
                    "zones": [f"{GCE}/zones/us-central1-a"],
                }
            ]
        },
    )
    _gcp_catalog_responses()
    assert [(r.id, r.zones) for r in gcp.list_regions()] == [("us-central1", ["us-central1-a"])]
    types = {t.id: t for t in gcp.list_instance_types("us-central1")}
    assert set(types) == {
        "g2-standard-8",
        "n1-standard-8+nvidia-tesla-t4x1",
        "n1-standard-16+nvidia-tesla-t4x2",
    }
    g2 = types["g2-standard-8"]
    assert g2.gpu_model == "NVIDIA L4" and g2.gpu_vram_bytes == 24 * GIB
    assert sorted(g2.extra["zones"]) == ["us-central1-a", "us-central1-b"]
    assert g2.price_per_hour_usd is None  # the Compute API has no prices
    assert types["n1-standard-16+nvidia-tesla-t4x2"].gpu_count == 2
    quotas = {q.name: (q.used, q.limit) for q in gcp.list_quotas("us-central1")}
    assert quotas == {"NVIDIA_L4_GPUS": (1.0, 8.0), "CPUS": (4.0, 72.0)}


@responses.activate
def test_gcp_create_and_poll(gcp):
    insert = responses.add(
        responses.POST,
        re.compile(rf"{GCE}/zones/us-central1-a/instances(\?.*)?$"),
        json={
            "name": "operation-1",
            "status": "DONE",
            "zone": f"{GCE}/zones/us-central1-a",
        },
    )
    vm = gcp.create_vm(
        spec(
            name="Lab_T4.Node",
            region="us-central1",
            zone="us-central1-a",
            instance_type="n1-standard-8+nvidia-tesla-t4x1",
            options={
                "subnetwork": "regions/us-central1/subnetworks/gpu",
                "public_ip": True,
            },
        )
    )
    assert vm.id == "lab-t4-node" and vm.zone == "us-central1-a"
    req = insert.calls[0].request
    body = json.loads(req.body)
    assert "requestId=0b6f8f9e" in req.url
    assert body["machineType"].endswith("machineTypes/n1-standard-8")
    assert body["guestAccelerators"][0]["acceleratorCount"] == 1
    assert body["scheduling"]["onHostMaintenance"] == "TERMINATE"
    assert body["labels"]["gpu-deck-managed"] == "true"
    meta = {i["key"]: i["value"] for i in body["metadata"]["items"]}
    assert meta["startup-script"].startswith("#!/bin/bash")
    assert body["networkInterfaces"][0]["accessConfigs"][0]["type"] == "ONE_TO_ONE_NAT"
    responses.add(
        responses.GET,
        f"{GCE}/zones/us-central1-a/instances/lab-t4-node",
        json={
            "name": "lab-t4-node",
            "status": "TERMINATED",
            "networkInterfaces": [
                {"networkIP": "10.128.0.5", "accessConfigs": [{"natIP": "34.1.2.3"}]}
            ],
        },
    )
    got = gcp.get_vm("lab-t4-node", region="us-central1", zone="us-central1-a")
    assert got.state == VM_STOPPED  # GCP TERMINATED == stopped
    assert (got.private_ip, got.public_ip) == ("10.128.0.5", "34.1.2.3")


@responses.activate
def test_gcp_capacity_error_from_operation(gcp):
    responses.add(
        responses.POST,
        re.compile(rf"{GCE}/zones/us-central1-a/instances(\?.*)?$"),
        json={
            "name": "operation-2",
            "status": "DONE",
            "httpErrorStatusCode": 503,
            "error": {
                "errors": [
                    {
                        "code": "ZONE_RESOURCE_POOL_EXHAUSTED",
                        "message": "The zone does not have enough resources",
                    }
                ]
            },
        },
    )
    with pytest.raises(CloudCapacityError):
        gcp.create_vm(
            spec(
                region="us-central1",
                zone="us-central1-a",
                instance_type="g2-standard-8",
            )
        )


@responses.activate
def test_gcp_quota_and_not_found(gcp):
    responses.add(
        responses.POST,
        re.compile(rf"{GCE}/zones/us-central1-a/instances(\?.*)?$"),
        status=403,
        json={
            "error": {
                "code": 403,
                "message": "Quota 'NVIDIA_L4_GPUS' exceeded. Limit: 0.0",
                "errors": [
                    {
                        "reason": "quotaExceeded",
                        "message": "Quota 'NVIDIA_L4_GPUS' exceeded.",
                    }
                ],
            }
        },
    )
    with pytest.raises(CloudQuotaError):
        gcp.create_vm(
            spec(
                region="us-central1",
                zone="us-central1-a",
                instance_type="g2-standard-8",
            )
        )
    responses.add(
        responses.GET,
        f"{GCE}/zones/us-central1-a/instances/gone",
        status=404,
        json={"error": {"code": 404, "message": "not found"}},
    )
    assert gcp.get_vm("gone", region="us-central1", zone="us-central1-a") is None


def test_gcp_helpers():
    from app.services.cloud.providers.gcp import gcp_name, split_instance_type

    assert split_instance_type("n1-standard-8+nvidia-tesla-t4x2") == (
        "n1-standard-8",
        "nvidia-tesla-t4",
        2,
    )
    assert split_instance_type("a2-highgpu-1g") == ("a2-highgpu-1g", None, 0)
    assert gcp_name("9 Lab Node") == "gd-9-lab-node"


# ==========================================
# DigitalOcean
# ==========================================

DO_SIZES = {
    "sizes": [
        {
            "slug": "gpu-h100x1-80gb",
            "memory": 245760,
            "vcpus": 20,
            "disk": 720,
            "price_hourly": 3.39,
            "available": True,
            "regions": ["nyc2", "tor1"],
            "description": "H100 GPU - 1X",
            "gpu_info": {
                "count": 1,
                "vram": {"amount": 80, "unit": "gib"},
                "model": "nvidia_h100",
            },
        },
        {
            "slug": "gpu-h100x8-640gb",
            "memory": 1966080,
            "vcpus": 160,
            "price_hourly": 23.92,
            "available": True,
            "regions": ["nyc2"],
            "gpu_info": {
                "count": 8,
                "vram": {"amount": 640, "unit": "gib"},
                "model": "nvidia_h100",
            },
        },
        {
            "slug": "s-2vcpu-4gb",
            "memory": 4096,
            "vcpus": 2,
            "price_hourly": 0.03571,
            "available": True,
            "regions": ["nyc2"],
        },
    ],
    "links": {},
    "meta": {"total": 3},
}


class DoApi:
    def __init__(self):
        self.calls = []
        self.droplets = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        assert request.headers["Authorization"] == "Bearer dop_v1_test"
        if path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "account": {
                        "email": "ops@example.com",
                        "droplet_limit": 25,
                        "status": "active",
                    }
                },
            )
        if path == "/v2/regions":
            return httpx.Response(
                200,
                json={
                    "regions": [
                        {
                            "slug": "nyc2",
                            "name": "New York 2",
                            "available": True,
                            "sizes": ["gpu-h100x1-80gb", "s-2vcpu-4gb"],
                        },
                        {
                            "slug": "sfo3",
                            "name": "San Francisco 3",
                            "available": True,
                            "sizes": ["s-2vcpu-4gb"],
                        },
                    ],
                    "links": {},
                },
            )
        if path == "/v2/sizes":
            return httpx.Response(200, json=DO_SIZES)
        if path == "/v2/droplets" and request.method == "GET":
            tag = request.url.params.get("tag_name")
            found = [d for d in self.droplets.values() if tag in d["tags"]] if tag else []
            return httpx.Response(
                200, json={"droplets": found, "meta": {"total": len(self.droplets)}}
            )
        if path == "/v2/droplets" and request.method == "POST":
            body = json.loads(request.content)
            if body["size"] == "gpu-h100x8-640gb":
                return httpx.Response(
                    422,
                    json={
                        "id": "unprocessable_entity",
                        "message": "creating this/these droplet(s) will "
                        "exceed your droplet limit",
                    },
                )
            droplet = {
                "id": 3164444,
                "status": "new",
                "tags": body["tags"],
                "networks": {"v4": []},
                "image": {"slug": body["image"]},
            }
            self.droplets[droplet["id"]] = droplet
            return httpx.Response(202, json={"droplet": droplet})
        m = re.match(r"^/v2/droplets/(\d+)(/actions)?$", path)
        if m:
            droplet = self.droplets.get(int(m.group(1)))
            if droplet is None:
                return httpx.Response(404, json={"id": "not_found", "message": "not found"})
            if m.group(2):
                action = json.loads(request.content)["type"]
                droplet["status"] = "off" if action == "shutdown" else "active"
                return httpx.Response(201, json={"action": {"type": action}})
            if request.method == "DELETE":
                del self.droplets[droplet["id"]]
                return httpx.Response(204)
            droplet["status"] = "active" if droplet["status"] == "new" else droplet["status"]
            droplet["networks"] = {
                "v4": [
                    {"type": "private", "ip_address": "10.10.0.5"},
                    {"type": "public", "ip_address": "164.90.1.2"},
                ]
            }
            return httpx.Response(200, json={"droplet": droplet})
        return httpx.Response(500, json={"message": f"unexpected {path}"})


@pytest.fixture
def do_api():
    return DoApi()


@pytest.fixture
def do(do_api):
    from app.services.cloud.providers.digitalocean import DigitalOceanProvider

    return DigitalOceanProvider({"token": "dop_v1_test"}, transport=httpx.MockTransport(do_api))


def test_digitalocean_catalog(do):
    assert do.check_credentials().account == "ops@example.com"
    assert [r.id for r in do.list_regions()] == ["nyc2"]
    types = do.list_instance_types("tor1")
    assert [t.id for t in types] == ["gpu-h100x1-80gb"]
    h100 = types[0]
    assert (h100.gpu_model, h100.gpu_count, h100.gpu_vram_bytes) == (
        "NVIDIA H100",
        1,
        80 * GIB,
    )
    assert h100.price_per_hour_usd == 3.39
    eight = [t for t in do.list_instance_types("nyc2") if t.gpu_count == 8][0]
    assert eight.gpu_vram_bytes == 80 * GIB and eight.total_vram_bytes == 640 * GIB
    quota = do.list_quotas("nyc2")[0]
    assert (quota.used, quota.limit) == (0.0, 25)


def test_digitalocean_lifecycle_and_idempotent_create(do, do_api):
    vm = do.create_vm(spec(region="nyc2", instance_type="gpu-h100x1-80gb"))
    assert vm.id == "3164444" and vm.state == VM_PENDING
    post = [c for c in do_api.calls if c.method == "POST"][0]
    body = json.loads(post.content)
    assert body["image"] == "gpu-h100x1-base"
    assert "gpu-deck:managed:true" in body["tags"]
    again = do.create_vm(spec(region="nyc2", instance_type="gpu-h100x1-80gb"))
    assert again.id == vm.id and len([c for c in do_api.calls if c.method == "POST"]) == 1
    got = do.get_vm(vm.id, region="nyc2")
    assert got.state == VM_RUNNING and got.public_ip == "164.90.1.2"
    do.stop_vm(vm.id, region="nyc2")
    assert do.get_vm(vm.id, region="nyc2").state == VM_STOPPED
    do.terminate_vm(vm.id, region="nyc2")
    assert do.get_vm(vm.id, region="nyc2") is None
    do.terminate_vm(vm.id, region="nyc2")  # 404 is fine


def test_digitalocean_quota_error(do):
    with pytest.raises(CloudQuotaError):
        do.create_vm(spec(region="nyc2", instance_type="gpu-h100x8-640gb", client_token=None))


def test_digitalocean_auth_error():
    from app.services.cloud.providers.digitalocean import DigitalOceanProvider

    api = httpx.MockTransport(
        lambda r: httpx.Response(
            401, json={"id": "unauthorized", "message": "Unable to authenticate you"}
        )
    )
    provider = DigitalOceanProvider({"token": "bad"}, transport=api)
    assert provider.check_credentials().ok is False
    with pytest.raises(CloudAuthError):
        provider.list_regions()
