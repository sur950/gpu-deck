"""
Pure units: bootstrap script, credential parsing, catalog filters,
GPU facts, price-plan suggestion and route wiring (no DB / network).
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from app.services.cloud import catalog
from app.services.cloud.base import CloudCredentialError, CloudInvalidRequestError
from app.services.cloud.credentials import parse_secret
from app.services.cloud.gpu_catalog import (
    azure_gpu,
    gcp_accelerator,
    gcp_machine_accelerator,
)
from app.services.cloud.lifecycle import price_plan_suggestion
from app.services.cloud.user_data import BootstrapParams, render_bootstrap

pytestmark = pytest.mark.unit

CA = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----"


def _script(**kw):
    params = dict(
        instance_id=str(uuid.uuid4()),
        provider="aws",
        join_token="gdj_" + "a" * 40,
        ca_fingerprint="ab" * 32,
        ca_pem=CA,
        node_name="lab g5 $(reboot)",
        labels={"gpu-deck/cloud-provider": "aws", "team": "a b'c"},
        hub_url="https://hub.example.com:8443",
        agent_version="1.0.0",
    )
    params.update(kw)
    return render_bootstrap(BootstrapParams(**params))


def test_bootstrap_script_is_valid_bash_and_quotes_everything():
    script = _script()
    assert script.startswith("#!/bin/bash\n")
    assert "NODE_NAME='lab g5 $(reboot)'" in script  # no command substitution possible
    assert "'team=a b'\"'\"'c'" in script
    assert "JOIN_TOKEN=gdj_" + "a" * 40 in script
    assert "https://hub.example.com:8443/agent/v1/downloads" not in script  # built at runtime
    assert 'URL="$DOWNLOAD_BASE/agent/v1/downloads/$AGENT_VERSION/$TARGET"' in script
    assert CA in script and "ExecStart=/usr/local/bin/gpudeck-agent" in script
    assert "--ca-fingerprint" in script and ".gpudeck-joined" in script
    assert len(script.encode()) < 16 * 1024  # AWS user-data limit
    bash = shutil.which("bash")
    if bash:
        proc = subprocess.run([bash, "-n"], input=script, text=True, capture_output=True)
        assert proc.returncode == 0, proc.stderr


def test_bootstrap_uses_download_base_setting(monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "CLOUD_AGENT_DOWNLOAD_BASE_URL", "https://dl.example.com/")
    assert "DOWNLOAD_BASE=https://dl.example.com\n" in _script()
    monkeypatch.setattr(settings, "CLOUD_AGENT_DOWNLOAD_BASE_URL", "")
    monkeypatch.setattr(settings, "AGENT_HUB_PUBLIC_URL", "https://hub.internal:8443")
    assert "DOWNLOAD_BASE=https://hub.internal:8443\n" in _script()


def test_parse_secret_per_provider():
    aws = parse_secret("aws", '{"access_key_id": "AK", "secret_access_key": "S"}')
    assert aws["access_key_id"] == "AK" and aws["role_arn"] is None
    assert parse_secret("digitalocean", "dop_v1_abc") == {"token": "dop_v1_abc"}
    assert parse_secret("digitalocean", '{"token": "dop_v1_x"}') == {"token": "dop_v1_x"}
    with pytest.raises(CloudCredentialError):
        parse_secret("aws", "not json")
    with pytest.raises(CloudCredentialError):
        parse_secret("azure", '{"tenant_id": "t"}')
    with pytest.raises(CloudCredentialError):
        parse_secret("gcp", '{"type": "authorized_user"}')
    gcp = parse_secret("gcp", '{"type": "service_account", "project_id": "p", "private_key": "k"}')
    assert gcp["project_id"] == "p"


def test_provider_infos_and_enabled(monkeypatch):
    from app.core.settings import settings

    infos = {p["id"]: p for p in catalog.provider_infos()}
    assert set(infos) == {"aws", "azure", "gcp", "digitalocean"}
    assert infos["digitalocean"]["cluster_type"] == "cloud_do"
    assert all(p["verified_live"] is False for p in infos.values())
    monkeypatch.setattr(settings, "CLOUD_PROVIDERS_ENABLED", "aws")
    catalog.ensure_enabled("aws")
    with pytest.raises(CloudInvalidRequestError):
        catalog.ensure_enabled("gcp")
    with pytest.raises(CloudInvalidRequestError):
        catalog.ensure_enabled("oracle")


def test_filter_instance_types():
    items = [
        {
            "id": "g5.xlarge",
            "gpu_vendor": "nvidia",
            "gpu_count": 1,
            "total_vram_bytes": 24 * 1024**3,
            "gpu_model": "NVIDIA A10G",
            "price_per_hour_usd": 1.0,
        },
        {
            "id": "p4d.24xlarge",
            "gpu_vendor": "nvidia",
            "gpu_count": 8,
            "total_vram_bytes": 320 * 1024**3,
            "gpu_model": "NVIDIA A100",
            "price_per_hour_usd": 32.7,
        },
        {
            "id": "inf2.xlarge",
            "gpu_vendor": "aws",
            "gpu_count": 1,
            "total_vram_bytes": None,
            "gpu_model": "AWS Inferentia2",
            "price_per_hour_usd": None,
        },
    ]
    ids = lambda **kw: [i["id"] for i in catalog.filter_instance_types(items, **kw)]  # noqa: E731
    assert ids(gpu_vendor="NVIDIA") == ["g5.xlarge", "p4d.24xlarge"]
    assert ids(min_vram_gb=100) == ["p4d.24xlarge"]
    assert ids(gpu_count=1) == ["g5.xlarge", "inf2.xlarge"]
    assert ids(search="a100") == ["p4d.24xlarge"]
    assert ids(max_price=5) == ["g5.xlarge"]


def test_gpu_facts():
    assert azure_gpu("Standard_ND96isr_H100_v5")[0] == "NVIDIA H100"
    assert azure_gpu("Standard_NC24ads_A100_v4")[2] == 80 * 1024**3
    assert azure_gpu("Standard_D4s_v5") == (None, None, None)
    assert gcp_accelerator("zones/us-central1-a/acceleratorTypes/nvidia-l4")[0] == "NVIDIA L4"
    assert gcp_accelerator("nvidia-future-x")[2] is None
    assert gcp_machine_accelerator("a3-highgpu-8g") == "nvidia-h100-80gb"


def test_price_plan_suggestion():
    row = SimpleNamespace(
        price_per_hour_usd=32.7726,
        gpu_count=8,
        provider="aws",
        region="us-east-1",
        instance_type="p4d.24xlarge",
        price_source="aws:pricing:on-demand:linux",
    )
    s = price_plan_suggestion(row)
    assert s["usd_per_gpu_minute"] == round(32.7726 / 60 / 8, 6)
    assert s["name"] == "cloud aws us-east-1 p4d.24xlarge"
    row.price_per_hour_usd = None
    assert price_plan_suggestion(row) is None


def test_cloud_routes_are_mounted_and_require_admin(test_client):
    from app.main import app

    mounted = {(m.upper(), p) for p, item in app.openapi()["paths"].items() for m in item}
    expected = {
        ("GET", "/api/v1/cloud/providers"),
        ("POST", "/api/v1/cloud/providers/{provider}/check-credential"),
        ("GET", "/api/v1/cloud/providers/{provider}/regions"),
        ("GET", "/api/v1/cloud/providers/{provider}/instance-types"),
        ("GET", "/api/v1/cloud/providers/{provider}/quotas"),
        ("GET", "/api/v1/cloud/nodes"),
        ("POST", "/api/v1/cloud/nodes"),
        ("GET", "/api/v1/cloud/nodes/{cloud_node_id}"),
        ("PATCH", "/api/v1/cloud/nodes/{cloud_node_id}"),
        ("DELETE", "/api/v1/cloud/nodes/{cloud_node_id}"),
        ("POST", "/api/v1/cloud/nodes/{cloud_node_id}/stop"),
        ("POST", "/api/v1/cloud/nodes/{cloud_node_id}/start"),
        ("POST", "/api/v1/cloud/nodes/{cloud_node_id}/terminate"),
        ("POST", "/api/v1/cloud/nodes/{cloud_node_id}/refresh"),
    }
    assert expected <= mounted
    # Price plans are v2 (app/api/v1/routes_v2): the list-price plan route is not mounted.
    assert ("POST", "/api/v1/cloud/nodes/{cloud_node_id}/apply-price-plan") not in mounted
    for method, path in [
        ("GET", "/api/v1/cloud/providers"),
        ("GET", "/api/v1/cloud/nodes"),
    ]:
        assert test_client.request(method, path).status_code in (401, 403)


def test_contract_paths_match_mounted_routes():
    """Every Cloud-tagged admin.yaml operation is mounted (and vice versa)."""
    from pathlib import Path

    import yaml
    from app.main import app

    spec_path = Path(__file__).resolve().parents[4] / "libs/contracts/openapi/admin.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    contract = {
        (m.upper(), "/api/v1" + p)
        for p, item in spec["paths"].items()
        for m, op in item.items()
        if isinstance(op, dict) and "Cloud" in op.get("tags", [])
    }
    mounted = {
        (m.upper(), p)
        for p, item in app.openapi()["paths"].items()
        for m, op in item.items()
        if "Cloud" in op.get("tags", [])
    }
    assert contract == mounted
