"""
Helper utilities and functions.
"""

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import pytz


def generate_uuid() -> str:
    """Generate a UUID v4 string."""
    return str(uuid.uuid4())


def generate_random_string(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe random string."""
    return secrets.token_urlsafe(length)


def generate_random_hex(length: int = 32) -> str:
    """Generate a cryptographically secure random hex string."""
    return secrets.token_hex(length)


def hash_string(value: str) -> str:
    """Hash a string using SHA-256 (used for API keys and session tokens)."""
    return hashlib.sha256(value.encode()).hexdigest()


def get_current_timestamp() -> datetime:
    """Get current UTC timestamp."""
    return datetime.now(pytz.UTC)


def get_timestamp_after(
    days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0
) -> datetime:
    """Get timestamp after specified duration from now."""
    return get_current_timestamp() + timedelta(
        days=days, hours=hours, minutes=minutes, seconds=seconds
    )


def convert_to_utc(dt: datetime) -> datetime:
    """Convert datetime to UTC."""
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(pytz.UTC)


def is_expired(expiry_time: datetime) -> bool:
    """Check if a datetime has expired."""
    return get_current_timestamp() > expiry_time


def build_url(base_url: str, path: str = "", params: Optional[Dict[str, Any]] = None) -> str:
    """Build a URL with path and query parameters."""
    url = base_url.rstrip("/")
    if path:
        url = f"{url}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return url


def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing potentially dangerous characters."""
    sanitized = re.sub(r"[^\w\-.]", "_", filename)
    return sanitized.replace("..", "_")


def chunks(lst: List[Any], size: int) -> List[List[Any]]:
    """Split a list into chunks of specified size."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def deep_merge(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries."""
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def truncate_string(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate string to specified length."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def bytes_to_human_readable(bytes_size: float) -> str:
    """Convert bytes to human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


def remove_none_values(d: Dict[str, Any]) -> Dict[str, Any]:
    """Remove None values from dictionary."""
    return {k: v for k, v in d.items() if v is not None}


def parse_user_agent(user_agent: str) -> str:
    """Parse user_agent string into a human-readable device name."""
    ua = (user_agent or "").lower()
    browser = "Unknown Browser"
    if "firefox" in ua:
        browser = "Firefox"
    elif "edg" in ua:
        browser = "Edge"
    elif "chrome" in ua:
        browser = "Chrome"
    elif "safari" in ua:
        browser = "Safari"

    platform = "Unknown Platform"
    if "iphone" in ua or "ipad" in ua:
        platform = "on iOS"
    elif "android" in ua:
        platform = "on Android"
    elif "macintosh" in ua or "mac os" in ua:
        platform = "on macOS"
    elif "windows" in ua:
        platform = "on Windows"
    elif "linux" in ua:
        platform = "on Linux"
    return f"{browser} {platform}"
