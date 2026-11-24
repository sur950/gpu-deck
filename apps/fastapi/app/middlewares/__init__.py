"""
Middleware registry — the extension point for HTTP middleware.

``install_middlewares(app, process)`` installs the core middlewares (request
id, context, security headers, logging, GZip) and then every module in this
package that exposes::

    ORDER = 50                    # lower runs closer to the app (added first); default 100
    PROCESSES = ("admin",)        # which FastAPI apps get it; default ("admin", "serving")

    def install(app: FastAPI, process: str) -> None:
        app.add_middleware(MyMiddleware)

Modules are added in ascending ORDER, then name. Remember Starlette's stack is
LIFO: the middleware added LAST is the OUTERMOST (sees the request first). So
a module with a *higher* ORDER wraps those with lower ones. Core middlewares
use 0-20; auth-ish ones (cookie -> bearer, CSRF, IP rules) should use 30-60.

Drop a file here rather than editing main.py.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import List, Tuple

from app.core.logger import get_logger
from fastapi import FastAPI

logger = get_logger(__name__)

_SKIP = {"__init__"}


def discover_middleware_modules(process: str) -> List[Tuple[int, str, object]]:
    """Import every module in app.middlewares exposing install(); sorted by (ORDER, name)."""
    package = importlib.import_module(__name__)
    found = []
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if name in _SKIP or name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{name}")
        install = getattr(module, "install", None)
        if not callable(install):
            continue
        processes = getattr(module, "PROCESSES", ("admin", "serving"))
        if process not in processes and "*" not in processes:
            continue
        order = int(getattr(module, "ORDER", 100))
        found.append((order, name, module))
    found.sort(key=lambda item: (item[0], item[1]))
    return found


def install_middlewares(app: FastAPI, process: str) -> List[str]:
    """Install all discovered middlewares on ``app``; returns the module names in install order."""
    installed = []
    for order, name, module in discover_middleware_modules(process):
        module.install(app, process)
        installed.append(name)
        logger.debug(f"[{process}] middleware installed: {name} (order={order})")
    logger.info(f"[{process}] middlewares: {', '.join(installed) or 'none'}")
    return installed


__all__ = ["install_middlewares", "discover_middleware_modules"]
