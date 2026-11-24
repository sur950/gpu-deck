"""
Logging configuration using Loguru.

Every module starts with::

    from app.core.logger import get_logger
    logger = get_logger(__name__)

Secrets are never logged; log ids and names only.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.settings import settings
from loguru import logger


def serialize_record(record: Dict[str, Any]) -> str:
    """Serialize log record to JSON format."""
    subset = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    if record.get("extra"):
        subset.update(record["extra"])
    if record.get("exception"):
        subset["exception"] = {
            "type": record["exception"].type.__name__,
            "value": str(record["exception"].value),
            "traceback": record["exception"].traceback,
        }
    return json.dumps(subset, default=str)


def format_record(record: Dict[str, Any]) -> str:
    """Format log record for text output."""
    format_string = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>\n"
    )
    if record.get("exception"):
        format_string += "{exception}\n"
    return format_string


def _add_file_sink(**sink_kwargs):
    """
    Best-effort file logging sink. A log directory/file permission problem
    here must not crash every process that imports app.core.settings
    (alembic, one-off scripts, etc.) — console logging alone is enough to
    keep those running.
    """
    try:
        log_file_path = Path(settings.LOG_FILE_PATH)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.LOG_FILE_PATH,
            level=settings.LOG_LEVEL,
            rotation=settings.LOG_ROTATION,
            retention=settings.LOG_RETENTION,
            compression="zip",
            backtrace=True,
            diagnose=settings.DEBUG,
            **sink_kwargs,
        )
    except OSError as exc:
        logger.warning(f"File logging disabled — could not open {settings.LOG_FILE_PATH}: {exc}")


def setup_logging():
    """Configure Loguru logger based on settings."""
    logger.remove()

    if settings.LOG_FORMAT == "json":
        logger.add(
            sys.stdout,
            level=settings.LOG_LEVEL,
            serialize=True,
            backtrace=True,
            diagnose=settings.DEBUG,
        )
        _add_file_sink(serialize=True)
    else:
        logger.add(
            sys.stdout,
            format=format_record,
            level=settings.LOG_LEVEL,
            colorize=True,
            backtrace=True,
            diagnose=settings.DEBUG,
        )
        _add_file_sink(format=format_record)

    logger.info(f"Logging configured - Level: {settings.LOG_LEVEL}, Format: {settings.LOG_FORMAT}")
    return logger


def get_logger(name: Optional[str] = None):
    """Get a logger instance with optional name."""
    if name:
        return logger.bind(logger_name=name)
    return logger


class log_context:
    """Context manager for adding context to logs."""

    def __init__(self, **kwargs):
        self.context = kwargs

    def __enter__(self):
        self.token = logger.contextualize(**self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


# Initialize logging
app_logger = setup_logging()
