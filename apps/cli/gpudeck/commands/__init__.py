"""Hand-written commands layered over the commands built from the OpenAPI spec."""

from __future__ import annotations

from .auth import register as register_auth
from .config_cmd import register as register_config
from .live import register as register_live

REGISTRARS = (register_auth, register_config, register_live)

__all__ = ["REGISTRARS"]
