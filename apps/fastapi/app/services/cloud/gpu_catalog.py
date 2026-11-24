"""
GPU facts the provider APIs do not return.

AWS (``GpuInfo``) and DigitalOcean (``gpu_info``) report GPU model, count and
memory directly. Azure resource SKUs report only a GPU count, GCP only an
accelerator type name; the tables below fill in model and VRAM for the
families those clouds sell today. Unknown families keep ``None`` (the UI
shows "unknown") rather than guessing.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from app.core.logger import get_logger

logger = get_logger(__name__)

GIB = 1024**3

# GCP accelerator type -> (model, vendor, VRAM GiB per GPU)
GCP_ACCELERATORS: Dict[str, Tuple[str, str, int]] = {
    "nvidia-tesla-t4": ("NVIDIA T4", "nvidia", 16),
    "nvidia-tesla-v100": ("NVIDIA V100", "nvidia", 16),
    "nvidia-tesla-p100": ("NVIDIA P100", "nvidia", 16),
    "nvidia-tesla-p4": ("NVIDIA P4", "nvidia", 8),
    "nvidia-tesla-a100": ("NVIDIA A100 40GB", "nvidia", 40),
    "nvidia-a100-80gb": ("NVIDIA A100 80GB", "nvidia", 80),
    "nvidia-l4": ("NVIDIA L4", "nvidia", 24),
    "nvidia-h100-80gb": ("NVIDIA H100 80GB", "nvidia", 80),
    "nvidia-h100-mega-80gb": ("NVIDIA H100 Mega 80GB", "nvidia", 80),
    "nvidia-h200-141gb": ("NVIDIA H200 141GB", "nvidia", 141),
    "nvidia-b200": ("NVIDIA B200", "nvidia", 180),
    "nvidia-rtx-pro-6000": ("NVIDIA RTX PRO 6000", "nvidia", 96),
}

# GCP accelerator-optimised machine families -> accelerator type.
GCP_MACHINE_FAMILY_ACCELERATOR = {
    "a2-highgpu": "nvidia-tesla-a100",
    "a2-megagpu": "nvidia-tesla-a100",
    "a2-ultragpu": "nvidia-a100-80gb",
    "a3-highgpu": "nvidia-h100-80gb",
    "a3-edgegpu": "nvidia-h100-80gb",
    "a3-megagpu": "nvidia-h100-mega-80gb",
    "a3-ultragpu": "nvidia-h200-141gb",
    "a4-highgpu": "nvidia-b200",
    "g2-standard": "nvidia-l4",
    "g4-standard": "nvidia-rtx-pro-6000",
}

# Azure VM family / size patterns -> (model, vendor, VRAM GiB per GPU). Checked in order.
AZURE_SIZE_PATTERNS = [
    (r"^Standard_ND\d+is?r?_H200_v5$", ("NVIDIA H200", "nvidia", 141)),
    (r"^Standard_ND\d+is?r?_H100_v5$", ("NVIDIA H100", "nvidia", 80)),
    (r"^Standard_ND\d+is?r?_MI300X_v5$", ("AMD Instinct MI300X", "amd", 192)),
    (r"^Standard_ND\d+amsr_A100_v4$", ("NVIDIA A100 80GB", "nvidia", 80)),
    (r"^Standard_ND\d+asr_v4$", ("NVIDIA A100 40GB", "nvidia", 40)),
    (r"^Standard_NC\d+ads_H100_v5$", ("NVIDIA H100 NVL", "nvidia", 94)),
    (r"^Standard_NC\d+ads_A100_v4$", ("NVIDIA A100 80GB", "nvidia", 80)),
    (r"^Standard_NC\d+as_T4_v3$", ("NVIDIA T4", "nvidia", 16)),
    (r"^Standard_NC\d+s_v3$", ("NVIDIA V100", "nvidia", 16)),
    (r"^Standard_NC\d+ads_A10_v4$", ("NVIDIA A10", "nvidia", 24)),
    (r"^Standard_NV\d+ads_A10_v5$", ("NVIDIA A10 (partial)", "nvidia", 24)),
    (r"^Standard_NV\d+s_v3$", ("NVIDIA M60", "nvidia", 8)),
    (r"^Standard_NG\d+ads_V620_v1$", ("AMD Radeon PRO V620", "amd", 32)),
    (r"^Standard_NV\d+ads_V710_v5$", ("AMD Radeon PRO V710", "amd", 28)),
]

# Families whose SKU reports a fractional GPU (NVadsA10 v5 sells 1/6..1 of an A10).
AZURE_PARTIAL_GPU = re.compile(r"^Standard_NV(\d+)ads_A10_v5$")


def gcp_accelerator(
    accelerator_type: str,
) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """(model, vendor, vram_bytes per GPU) for a GCP accelerator type name."""
    name = accelerator_type.rsplit("/", 1)[-1]
    entry = GCP_ACCELERATORS.get(name)
    if entry is None:
        vendor = "nvidia" if name.startswith("nvidia") else None
        return name, vendor, None
    model, vendor, vram = entry
    return model, vendor, vram * GIB


def gcp_machine_accelerator(machine_type: str) -> Optional[str]:
    for prefix, accel in GCP_MACHINE_FAMILY_ACCELERATOR.items():
        if machine_type.startswith(prefix):
            return accel
    return None


def azure_gpu(size: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """(model, vendor, vram_bytes per GPU) for an Azure VM size name."""
    for pattern, (model, vendor, vram) in AZURE_SIZE_PATTERNS:
        if re.match(pattern, size, flags=re.IGNORECASE):
            return model, vendor, vram * GIB
    return None, None, None


def normalise_gpu_name(manufacturer: Optional[str], name: Optional[str]) -> Optional[str]:
    """``("NVIDIA", "A10G")`` -> ``"NVIDIA A10G"``; keeps names that already carry the vendor."""
    if not name:
        return None
    if manufacturer and not name.lower().startswith(manufacturer.lower()):
        return f"{manufacturer} {name}"
    return name


def vendor_of(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    low = name.lower()
    for vendor in ("nvidia", "amd", "intel", "habana", "aws"):
        if low.startswith(vendor):
            return "amd" if vendor == "amd" else vendor
    return None
