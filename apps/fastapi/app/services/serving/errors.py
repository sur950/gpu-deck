"""
Serving errors — one exception, three wire shapes (serving.md §4).

``ServingError`` carries the HTTP status, the GPU Deck code and a message;
``render_error`` turns it into the OpenAI, Anthropic or Ollama error body for
the format of the request. Streaming errors (headers already sent) use
``stream_error_frame``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)

HEADER_ERROR_CODE = "x-gpudeck-error-code"

# OpenAI `type` per status
_OPENAI_TYPES = {
    400: "invalid_request_error",
    404: "invalid_request_error",
    413: "invalid_request_error",
    415: "invalid_request_error",
    422: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    429: "rate_limit_error",
    500: "server_error",
    502: "server_error",
    503: "server_error",
    504: "server_error",
}

_ANTHROPIC_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    415: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
    529: "overloaded_error",
}


class ServingError(Exception):
    """An error the serving API reports in the client's wire format."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        param: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.param = param
        self.headers = dict(headers or {})
        if retry_after is not None:
            self.headers["Retry-After"] = str(retry_after)

    # Convenience constructors --------------------------------------------------

    @classmethod
    def invalid_request(cls, message: str, param: Optional[str] = None) -> "ServingError":
        return cls(400, "invalid_request_error", message, param=param)

    @classmethod
    def model_not_found(cls, model: str) -> "ServingError":
        return cls(404, "model_not_found", f"model '{model}' not found", param="model")

    @classmethod
    def model_not_ready(cls, model: str, retry_after: int = 5) -> "ServingError":
        return cls(
            503,
            "model_not_ready",
            f"model '{model}' has no ready instance",
            param="model",
            retry_after=retry_after,
        )

    @classmethod
    def model_type_mismatch(cls, model: str, model_type: str, endpoint: str) -> "ServingError":
        return cls(
            400,
            "model_type_mismatch",
            f"model '{model}' is of type '{model_type}' and cannot serve {endpoint}",
            param="model",
        )

    @classmethod
    def request_too_large(cls, limit: int) -> "ServingError":
        return cls(413, "request_too_large", f"request body exceeds {limit} bytes")

    @classmethod
    def upstream_error(cls, message: str, status_code: int = 502) -> "ServingError":
        return cls(status_code, "upstream_error", message)

    @classmethod
    def upstream_timeout(cls, message: str = "upstream request timed out") -> "ServingError":
        return cls(504, "upstream_timeout", message)

    @classmethod
    def instance_busy(cls, retry_after: int = 2) -> "ServingError":
        return cls(429, "instance_busy", "all instances are busy", retry_after=retry_after)

    @classmethod
    def no_instance_available(cls, model: str) -> "ServingError":
        return cls(
            503,
            "no_instance_available",
            f"no healthy instance available for model '{model}'",
            retry_after=5,
        )

    @classmethod
    def unsupported_endpoint(cls, message: str) -> "ServingError":
        return cls(404, "unsupported_endpoint", message)

    @classmethod
    def internal(cls, message: str = "internal error") -> "ServingError":
        return cls(500, "internal_error", message)


def error_body(fmt: str, status_code: int, code: str, message: str, param=None) -> Dict[str, Any]:
    """The JSON body for an error in the given format."""
    if fmt == "anthropic":
        return {
            "type": "error",
            "error": {
                "type": _ANTHROPIC_TYPES.get(status_code, "api_error"),
                "message": f"{message} [code={code}]",
            },
        }
    if fmt == "ollama":
        return {"error": message}
    return {
        "error": {
            "message": message,
            "type": _OPENAI_TYPES.get(status_code, "server_error"),
            "param": param,
            "code": code,
        }
    }


def error_headers(err: ServingError) -> Dict[str, str]:
    headers = {HEADER_ERROR_CODE: err.code, "Cache-Control": "no-store"}
    if err.status_code == 401:
        headers.setdefault("WWW-Authenticate", "ApiKey")
    headers.update(err.headers)
    return headers


def stream_error_frame(fmt: str, err: ServingError) -> bytes:
    """Error emitted after headers were sent (serving.md §4)."""
    body = error_body(fmt, err.status_code, err.code, err.message, err.param)
    if fmt == "anthropic":
        return f"event: error\ndata: {json.dumps(body)}\n\n".encode()
    if fmt == "ollama":
        return (json.dumps({"error": err.message, "done": True}) + "\n").encode()
    return f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n".encode()


def upstream_status_to_error(status_code: int, body: bytes) -> ServingError:
    """Map a non-2xx upstream response to a ServingError (5xx -> upstream_error)."""
    text = body.decode("utf-8", errors="replace")[:512]
    message = text or f"upstream returned {status_code}"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            inner = parsed.get("error")
            if isinstance(inner, dict):
                message = inner.get("message") or inner.get("code") or message
            elif isinstance(inner, str):
                message = inner
    except (json.JSONDecodeError, ValueError):
        pass
    if status_code >= 500:
        return ServingError(status_code, "upstream_error", message)
    return ServingError(status_code, "invalid_request_error", message)
