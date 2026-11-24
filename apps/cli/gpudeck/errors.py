"""Error types and process exit codes for the gpudeck CLI."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_CONNECTION = 5

_AUTH_CODES = {
    "AUTHENTICATION_ERROR",
    "INVALID_CREDENTIALS",
    "ACCOUNT_LOCKED",
    "CSRF_FAILED",
    "SESSION_EXPIRED",
    "IP_BLOCKED",
}


class GpudeckError(Exception):
    """Base error; ``exit_code`` drives the process status."""

    exit_code = EXIT_ERROR

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code


class ConfigError(GpudeckError):
    exit_code = EXIT_USAGE


class NotLoggedIn(GpudeckError):
    exit_code = EXIT_AUTH


class ConnectionFailed(GpudeckError):
    exit_code = EXIT_CONNECTION


class ApiError(GpudeckError):
    """An error envelope (``success: false``) or unexpected HTTP status from the admin API."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: object = None,
        field: str | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.details = details
        self.field = field
        if status in (401,) or code in _AUTH_CODES:
            exit_code = EXIT_AUTH
        elif status == 404 or code == "RESOURCE_NOT_FOUND":
            exit_code = EXIT_NOT_FOUND
        elif status == 422 or code == "VALIDATION_ERROR":
            exit_code = EXIT_USAGE
        else:
            exit_code = EXIT_ERROR
        super().__init__(f"{code}: {message}" if code else message, exit_code=exit_code)

    @classmethod
    def from_response(cls, status: int, payload: object, text: str = "") -> "ApiError":
        """Build from a parsed envelope (dict) or raw body."""
        if isinstance(payload, dict):
            err = payload.get("error") or {}
            if isinstance(err, dict):
                return cls(
                    status,
                    str(err.get("code") or f"HTTP_{status}"),
                    str(err.get("message") or text or "request failed"),
                    err.get("details"),
                    err.get("field"),
                )
        return cls(status, f"HTTP_{status}", text[:500] or "request failed")
