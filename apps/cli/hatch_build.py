"""Hatch build hook: ship the admin API contract inside the package as the offline fallback spec.

Source order: ``libs/contracts/openapi/admin.yaml`` in the repository (normal builds), then
``gpudeck/_spec/admin.yaml`` already inside an unpacked sdist (wheel built from the sdist). The
file lands at ``gpudeck/_spec/admin.yaml`` in both the sdist and the wheel and is never written
into the source tree, so libs/contracts stays the single source of truth.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

logger = logging.getLogger(__name__)

TARGET = "gpudeck/_spec/admin.yaml"


class SpecBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        candidates = (
            root.parent.parent / "libs" / "contracts" / "openapi" / "admin.yaml",
            root / TARGET,
        )
        source = next((c for c in candidates if c.is_file()), None)
        if source is None:
            raise FileNotFoundError(
                "admin.yaml not found; build from the repository (libs/contracts/openapi) "
                "or from an sdist that contains " + TARGET
            )
        build_data["force_include"][str(source)] = TARGET
