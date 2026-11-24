"""
Application settings — aggregated from one module per domain.

Extension point
---------------
Every module in ``app/core/settings/`` (except ``__init__`` and ``base``) that
defines one or more ``DomainSettings`` subclasses is picked up automatically.
The classes are merged into a single ``Settings`` class, so a new domain adds
its own file (``app/core/settings/<domain>.py``) and never edits this one::

    # app/core/settings/billing.py
    from pydantic import Field
    from app.core.settings.base import DomainSettings

    class BillingSettings(DomainSettings):
        BILLING_CURRENCY: str = Field(default="USD", description="Report currency")

Then anywhere in the app::

    from app.core.settings import settings
    settings.BILLING_CURRENCY

Rules:

- Field names are ``UPPER_SNAKE`` and identical to the env var names.
- Every field has ``Field(description=...)``; critical values have no default.
- Values come from the process environment first, then ``apps/fastapi/.env``
  (override the file with ``GPU_DECK_ENV_FILE``).
- Two domains must not declare the same field name; the aggregator raises at
  import time if they do, which is the one collision this layout can detect.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Dict, List, Type

from app.core.settings.base import ENV_FILE, DomainSettings
from pydantic_settings import SettingsConfigDict

_SKIP_MODULES = {"__init__", "base"}


def discover_settings_classes() -> List[Type[DomainSettings]]:
    """Import every settings module and return its DomainSettings subclasses, sorted."""
    package = importlib.import_module(__name__)
    classes: List[Type[DomainSettings]] = []
    seen_fields: Dict[str, str] = {}

    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if module_info.name in _SKIP_MODULES or module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for attr_name in sorted(dir(module)):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, DomainSettings)
                and obj is not DomainSettings
                and obj.__module__ == module.__name__
            ):
                for field_name in obj.model_fields:
                    owner = seen_fields.get(field_name)
                    if owner and owner != obj.__name__:
                        raise RuntimeError(
                            f"Settings field {field_name!r} is declared by both "
                            f"{owner} and {obj.__name__}; rename one of them."
                        )
                    seen_fields[field_name] = obj.__name__
                classes.append(obj)
    return classes


def build_settings_class() -> Type[DomainSettings]:
    """Create the merged Settings class from all discovered domain classes."""
    bases = tuple(discover_settings_classes()) or (DomainSettings,)
    namespace = {
        "__module__": __name__,
        "__doc__": "Merged application settings (see app/core/settings/__init__.py).",
        "model_config": SettingsConfigDict(
            env_file=str(ENV_FILE),
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        ),
    }
    return type("Settings", bases, namespace)


Settings = build_settings_class()

# Global settings instance — `from app.core.settings import settings`
settings = Settings()

__all__ = [
    "Settings",
    "settings",
    "DomainSettings",
    "discover_settings_classes",
    "ENV_FILE",
]
