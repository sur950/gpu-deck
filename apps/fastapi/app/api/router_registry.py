"""
Router auto-discovery — the extension point for HTTP routes.

Admin API: every module in ``app/api/v1/routes/`` that exposes a module-level
``router`` (an ``APIRouter``) is included automatically under
``settings.API_V1_PREFIX``. Tags come from the module's ``TAGS`` list, else
from the module name (``api_keys`` -> ``["API Keys"]``).

Serving API: every module under ``app/api/serving/**`` exposing ``router`` is
included with the prefix declared by the nearest package ``PREFIX`` (``openai/``
-> ``/v1``, ``anthropic/`` -> ``/anthropic/v1``, ``ollama/`` -> ``/api``); a
module may override with its own ``PREFIX``.

Optional module attributes:

    router = APIRouter()           # required; no prefix inside, full paths in decorators
    TAGS = ["Nodes"]               # Swagger tags
    PREFIX = "/v1"                 # override the mount prefix (serving only)
    INCLUDE_IN_SCHEMA = True       # hide a router from OpenAPI with False
    DEPENDENCIES = [Depends(...)]  # applied to every route in the router

Modules whose name starts with ``_`` are skipped. Import errors are NOT
swallowed: a broken route module must fail startup loudly.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from types import ModuleType
from typing import List, Optional

from app.core.logger import get_logger
from fastapi import APIRouter, FastAPI

logger = get_logger(__name__)

_ACRONYMS = {
    "api": "API",
    "gpu": "GPU",
    "ha": "HA",
    "pki": "PKI",
    "ip": "IP",
    "otel": "OTel",
}


@dataclass
class DiscoveredRouter:
    module_name: str
    router: APIRouter
    prefix: str
    tags: List[str]
    include_in_schema: bool
    dependencies: list


def tags_from_module_name(name: str) -> List[str]:
    """``api_keys`` -> ``["API Keys"]``; ``agent_hub`` -> ``["Agent Hub"]``."""
    leaf = name.rsplit(".", 1)[-1]
    words = [_ACRONYMS.get(w, w.capitalize()) for w in leaf.split("_") if w]
    return [" ".join(words)]


def _walk_modules(package: ModuleType):
    """Yield (module_name, is_pkg) for every module below ``package``, sorted, recursively."""
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if module_info.name.startswith("_"):
            continue
        full_name = f"{package.__name__}.{module_info.name}"
        if module_info.ispkg:
            yield full_name, True
            yield from _walk_modules(importlib.import_module(full_name))
        else:
            yield full_name, False


def _package_prefix(module_name: str, root_package: str, default: Optional[str]) -> Optional[str]:
    """Nearest enclosing package (below root) that defines PREFIX."""
    parts = module_name.split(".")
    root_parts = root_package.split(".")
    for depth in range(len(parts) - 1, len(root_parts) - 1, -1):
        pkg_name = ".".join(parts[:depth])
        pkg = importlib.import_module(pkg_name)
        prefix = getattr(pkg, "PREFIX", None)
        if prefix is not None:
            return prefix
    return default


def discover_routers(
    root_package: str, default_prefix: Optional[str] = None, recursive: bool = True
) -> List[DiscoveredRouter]:
    """Import every module under ``root_package`` and collect the ones exposing ``router``."""
    package = importlib.import_module(root_package)
    found: List[DiscoveredRouter] = []
    for module_name, is_pkg in _walk_modules(package):
        if is_pkg:
            continue
        if not recursive and module_name.count(".") != root_package.count(".") + 1:
            continue
        module = importlib.import_module(module_name)
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            continue
        prefix = getattr(module, "PREFIX", None)
        if prefix is None:
            prefix = _package_prefix(module_name, root_package, default_prefix) or ""
        found.append(
            DiscoveredRouter(
                module_name=module_name,
                router=router,
                prefix=prefix,
                tags=list(getattr(module, "TAGS", None) or tags_from_module_name(module_name)),
                include_in_schema=bool(getattr(module, "INCLUDE_IN_SCHEMA", True)),
                dependencies=list(getattr(module, "DEPENDENCIES", None) or []),
            )
        )
    return found


def include_discovered_routers(
    app: FastAPI,
    root_package: str,
    default_prefix: Optional[str] = None,
    label: str = "",
) -> List[DiscoveredRouter]:
    """Discover and ``app.include_router`` each router; returns what was mounted."""
    discovered = discover_routers(root_package, default_prefix=default_prefix)
    for item in discovered:
        app.include_router(
            item.router,
            prefix=item.prefix,
            tags=item.tags,
            include_in_schema=item.include_in_schema,
            dependencies=item.dependencies or None,
        )
        logger.info(
            f"[{label or root_package}] mounted {item.module_name} "
            f"at '{item.prefix or '/'}' tags={item.tags}"
        )
    if not discovered:
        logger.warning(f"[{label or root_package}] no routers found")
    return discovered


__all__ = [
    "DiscoveredRouter",
    "discover_routers",
    "include_discovered_routers",
    "tags_from_module_name",
]
