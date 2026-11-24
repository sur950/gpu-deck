"""
Engine builds the node can run (glibc) and llama.cpp cross-node pipeline parallel.

- ``artifacts.select`` with the node's glibc / container runtime: older-release fallbacks,
  the official image, the next accelerator, and the agent payload shape (contract schema);
- ``artifacts.incompatibility`` / ``engine_unavailable_reason`` explain a node no build fits;
- ``refresh_compat`` copies the seed's compatibility keys onto existing rows of the same pin;
- migration 003 turns llama.cpp ``pipeline_parallel`` on.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from app.core.logger import get_logger
from app.services.engines import artifacts as arts
from app.services.engines.seed import LLAMACPP_ARTIFACTS

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[4]


def _artifact_schema():
    common = json.loads((ROOT / "libs/contracts/agent-protocol/common.json").read_text())
    return {**common["$defs"]["Artifact"], "$defs": common["$defs"]}


@pytest.mark.parametrize(
    "arch,vendor,driver,runtime,glibc,container,want_url,want_image",
    [
        # Ubuntu 24.04: the pinned GPU build, image kept as the agent's last resort.
        (
            "x86_64",
            "nvidia",
            "560.35",
            "12.6",
            "2.39",
            False,
            "cuda-12.8-x64",
            "server-cuda-",
        ),
        # Ubuntu 22.04 with Docker: the CUDA build is sent with its image (the agent runs it).
        (
            "x86_64",
            "nvidia",
            "560.35",
            "12.6",
            "2.35",
            True,
            "cuda-12.8-x64",
            "server-cuda-",
        ),
        # Ubuntu 22.04 without a container runtime: Vulkan (built on 22.04) instead of CUDA.
        (
            "x86_64",
            "nvidia",
            "560.35",
            "12.6",
            "2.35",
            False,
            "vulkan-x64",
            "server-vulkan-",
        ),
        # ROCm on Ubuntu 22.04: the b10375 release (last Ubuntu 22.04 ROCm build).
        (
            "x86_64",
            "amd",
            "6.10",
            "7.14.0",
            "2.35",
            False,
            "b10375-bin-ubuntu-rocm-7.14",
            None,
        ),
        # Debian 12 x86_64 CPU: the 22.04 build runs (glibc 2.34 needed).
        ("x86_64", None, None, None, "2.36", False, "ubuntu-x64", "server-b12464"),
        # arm64 on Debian 12 with Podman: the image.
        ("aarch64", None, None, None, "2.36", True, "ubuntu-arm64", "server-b12464"),
        # Unknown glibc (older agent): no filtering, the agent decides.
        (
            "aarch64",
            "nvidia",
            "580.65",
            None,
            None,
            False,
            "cuda-13.5-arm64",
            "server-cuda13",
        ),
    ],
)
def test_select_by_host_glibc(
    arch, vendor, driver, runtime, glibc, container, want_url, want_image
):
    picked = arts.select(
        LLAMACPP_ARTIFACTS, "linux", arch, vendor, driver, runtime, glibc, container
    )
    assert picked is not None and want_url in picked["url"], picked
    if want_image:
        assert want_image in picked["container"]["image"], picked
        assert "@sha256:" in picked["container"]["image"]
        assert picked["container"]["entrypoint"] == "/app/llama-server"
    for key in ("min_driver_version", "min_runtime_version"):
        assert key not in picked
        assert all(key not in f for f in picked.get("fallbacks", []))
    jsonschema.validate(picked, _artifact_schema())


def test_rocm_sends_its_older_release_as_fallback_for_the_agent():
    picked = arts.select(LLAMACPP_ARTIFACTS, "linux", "x86_64", "amd", "6.10", "10.0.1")
    assert "rocm-10.0" in picked["url"] and picked["min_glibc"] == "2.38"
    (fallback,) = picked["fallbacks"]
    assert "b10375" in fallback["url"] and fallback["min_glibc"] == "2.35"
    assert fallback["entrypoint"] == "llama-b10375/llama-server"
    assert "server-rocm-b12464" in picked["container"]["image"]


def test_no_fit_and_the_explanation():
    # arm64 Debian 12 without a container runtime: nothing runs.
    assert arts.select(LLAMACPP_ARTIFACTS, "linux", "aarch64", None, None, None, "2.36") is None
    why = arts.incompatibility(LLAMACPP_ARTIFACTS, "linux", "aarch64", "2.36", False)
    assert "glibc >= 2.38" in why and "glibc 2.36" in why
    assert "Docker or Podman" in why and "engine_paths" in why
    # With a container runtime, or on x86_64 (22.04 builds), or on macOS: no complaint.
    assert arts.incompatibility(LLAMACPP_ARTIFACTS, "linux", "aarch64", "2.36", True) is None
    assert arts.incompatibility(LLAMACPP_ARTIFACTS, "linux", "x86_64", "2.35", False) is None
    assert arts.incompatibility(LLAMACPP_ARTIFACTS, "macos", "aarch64", None, False) is None
    assert arts.node_host({"glibc_version": "2.35", "runtimes": ["native", "container"]}) == (
        "2.35",
        True,
    )
    assert arts.node_host(None) == (None, False)


def test_engine_unavailable_reason_names_glibc():
    from app.services.deployments.service import engine_unavailable_reason

    engine = SimpleNamespace(name="llama_cpp", runtime="native")
    version = SimpleNamespace(version="b12500", artifacts=LLAMACPP_ARTIFACTS)
    old = SimpleNamespace(
        os="linux",
        arch="aarch64",
        capabilities={"engines": [], "runtimes": ["native"], "glibc_version": "2.35"},
    )
    why = engine_unavailable_reason(old, engine, version)
    assert why.startswith("llama_cpp b12500: the pinned linux/aarch64 builds need glibc >= 2.38")
    new = SimpleNamespace(
        os="linux",
        arch="aarch64",
        capabilities={"engines": [], "runtimes": ["native"], "glibc_version": "2.39"},
    )
    assert engine_unavailable_reason(new, engine, version) is None
    docker = SimpleNamespace(
        os="linux",
        arch="aarch64",
        capabilities={
            "engines": [],
            "runtimes": ["native", "container"],
            "glibc_version": "2.35",
        },
    )
    assert engine_unavailable_reason(docker, engine, version) is None


def test_refresh_compat_only_touches_the_same_pin():
    from app.services.engines.service import refresh_compat

    old = {
        key: {k: v for k, v in entry.items() if k not in ("min_glibc", "fallbacks", "container")}
        for key, entry in LLAMACPP_ARTIFACTS.items()
    }
    old["linux/x86_64/vulkan"] = {**old["linux/x86_64/vulkan"], "sha256": "ab" * 32}
    merged = refresh_compat(old, LLAMACPP_ARTIFACTS)
    assert merged["linux/aarch64"]["min_glibc"] == "2.38"
    assert (
        merged["linux/x86_64/rocm"]["fallbacks"]
        == LLAMACPP_ARTIFACTS["linux/x86_64/rocm"]["fallbacks"]
    )
    assert "min_glibc" not in merged["linux/x86_64/vulkan"], "re-pinned entry left alone"
    assert "min_glibc" not in old["linux/aarch64"], "input not mutated"
    assert refresh_compat(merged, LLAMACPP_ARTIFACTS) is None, "idempotent"
    assert refresh_compat(None, LLAMACPP_ARTIFACTS) is None


def test_migration_003_enables_llama_cpp_pipeline_parallel():
    from tests.schema_helpers import migration_chain

    chain = [p.name for p in migration_chain()]
    assert "003_llamacpp_pipeline_parallel" in chain  # later migrations may follow it
    up = (
        ROOT
        / "apps/fastapi/app/db/postgres/alembic/migrations/003_llamacpp_pipeline_parallel"
        / "up.sql"
    ).read_text()
    assert '"pipeline_parallel": true' in up and "name = 'llama_cpp'" in up
