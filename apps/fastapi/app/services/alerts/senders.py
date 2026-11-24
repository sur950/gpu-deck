"""
Alert transports: Slack incoming webhook, SMTP email, signed HTTP webhook.

Each sender takes a rendered ``AlertMessage`` plus the channel config and the
resolved secret, performs one attempt and returns a ``SendResult`` (never
raises). Retries and bookkeeping live in service.py.

Webhook signature (documented for receivers)::

    X-GPUDeck-Timestamp: <unix seconds>
    X-GPUDeck-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>." + raw_body)>

Receivers recompute the HMAC over the exact bytes received and reject
timestamps older than a few minutes (replay protection).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

SEVERITY_EMOJI = {
    "info": ":information_source:",
    "warning": ":warning:",
    "critical": ":rotating_light:",
}


@dataclass
class AlertMessage:
    event_id: str
    event_type: str
    severity: str
    title: str
    message: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    occurrences: int = 1
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    delivery_id: Optional[str] = None
    test: bool = False

    def as_json(self) -> Dict[str, Any]:
        return {
            "id": self.event_id,
            "event_type": self.event_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "resource": {
                "type": self.resource_type,
                "id": self.resource_id,
                "name": self.resource_name,
            },
            "payload": self.payload,
            "occurrences": self.occurrences,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "app": settings.APP_NAME,
            "test": self.test,
        }


@dataclass
class SendResult:
    ok: bool
    response_code: Optional[int] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign_payload(secret, timestamp, body), signature or "")


def _timeout() -> float:
    return float(settings.ALERT_SEND_TIMEOUT_SECONDS)


# Cloud instance-metadata endpoints that are not link-local (AWS IPv6, Alibaba).
_METADATA_ADDRESSES = {
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
}


def blocked_address(ip: str) -> Optional[str]:
    """Why an alert delivery must not go to ``ip`` (None = allowed).

    Webhook / Slack URLs are admin input and deliveries run inside the control plane's
    network: link-local (cloud metadata credentials at 169.254.169.254), metadata,
    multicast, unspecified and reserved addresses are refused. Private ranges stay allowed
    (on-prem chat servers and webhook receivers are common).
    """
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return f"unparseable address {ip!r}"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr in _METADATA_ADDRESSES:
        return f"{addr} is a cloud metadata address"
    if addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        return f"{addr} is a link-local / multicast / reserved address"
    return None


async def check_destination(url: str) -> Optional[str]:
    """Reason the URL may not be called, or None. Resolves the host like the client will."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "invalid URL"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "only http(s) URLs with a host are allowed"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, port)
    except OSError:
        return None  # unresolvable: the request fails on its own
    for info in infos:
        reason = blocked_address(str(info[4][0]))
        if reason:
            return f"destination blocked: {reason}"
    return None


async def _post(url: str, body: bytes, headers: Dict[str, str]) -> SendResult:
    started = time.monotonic()
    blocked = await check_destination(url)
    if blocked:
        return SendResult(False, None, blocked, 0)
    try:
        async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False) as client:
            resp = await client.post(url, content=body, headers=headers)
        latency = int((time.monotonic() - started) * 1000)
        if 200 <= resp.status_code < 300:
            return SendResult(True, resp.status_code, None, latency)
        return SendResult(
            False,
            resp.status_code,
            f"HTTP {resp.status_code}: {resp.text[:300]}",
            latency,
        )
    except Exception as exc:  # noqa: BLE001 - one attempt, the caller retries
        return SendResult(
            False,
            None,
            f"{type(exc).__name__}: {exc}"[:500],
            int((time.monotonic() - started) * 1000),
        )


def slack_body(msg: AlertMessage, config: Dict[str, Any]) -> Dict[str, Any]:
    prefix = "[TEST] " if msg.test else ""
    headline = f"{SEVERITY_EMOJI.get(msg.severity, '')} {prefix}*{msg.title}*".strip()
    fields: List[Dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*Severity*\n{msg.severity}"},
        {"type": "mrkdwn", "text": f"*Event*\n`{msg.event_type}`"},
    ]
    if msg.resource_type:
        fields.append(
            {
                "type": "mrkdwn",
                "text": f"*{msg.resource_type}*\n{msg.resource_name or msg.resource_id or ''}",
            }
        )
    if msg.occurrences > 1:
        fields.append({"type": "mrkdwn", "text": f"*Occurrences*\n{msg.occurrences}"})
    blocks: List[Dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}}
    ]
    if msg.message:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": msg.message[:2900]}})
    blocks.append({"type": "section", "fields": fields})
    body: Dict[str, Any] = {
        "text": f"{prefix}[{msg.severity.upper()}] {msg.title}",
        "blocks": blocks,
    }
    for key in ("channel", "username", "icon_emoji"):
        if config.get(key):
            body[key] = config[key]
    return body


async def send_slack(
    msg: AlertMessage, config: Dict[str, Any], webhook_url: Optional[str]
) -> SendResult:
    url = webhook_url or config.get("url") or config.get("webhook_url")
    if not url:
        return SendResult(False, None, "slack channel has no webhook URL (secret)")
    body = json.dumps(slack_body(msg, config)).encode()
    return await _post(url, body, {"Content-Type": "application/json"})


async def send_webhook(
    msg: AlertMessage, config: Dict[str, Any], secret: Optional[str]
) -> SendResult:
    url = config.get("url")
    if not url:
        return SendResult(False, None, "webhook channel has no url")
    body = json.dumps(msg.as_json(), separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"{settings.APP_NAME}-alerts",
        "X-GPUDeck-Event": msg.event_type,
        "X-GPUDeck-Timestamp": timestamp,
    }
    if msg.delivery_id:
        headers["X-GPUDeck-Delivery"] = msg.delivery_id
    for key, value in (config.get("headers") or {}).items():
        if str(key).lower() not in ("content-type", "x-gpudeck-signature"):
            headers[str(key)] = str(value)
    if secret:
        headers["X-GPUDeck-Signature"] = sign_payload(secret, timestamp, body)
    return await _post(url, body, headers)


def email_message(msg: AlertMessage, config: Dict[str, Any]) -> EmailMessage:
    prefix = "[TEST] " if msg.test else ""
    mail = EmailMessage()
    mail["Subject"] = f"{prefix}[{settings.APP_NAME}] [{msg.severity.upper()}] {msg.title}"
    mail["From"] = config.get("from") or settings.SMTP_FROM
    mail["To"] = ", ".join(config.get("recipients") or [])
    lines = [
        msg.title,
        "",
        msg.message or "",
        "",
        f"Severity: {msg.severity}",
        f"Event: {msg.event_type}",
    ]
    if msg.resource_type:
        lines.append(f"{msg.resource_type}: {msg.resource_name or msg.resource_id}")
    if msg.occurrences > 1:
        lines.append(f"Occurrences: {msg.occurrences}")
    if msg.payload:
        lines += ["", json.dumps(msg.payload, indent=2, default=str)]
    mail.set_content("\n".join(lines))
    return mail


def _smtp_send(config: Dict[str, Any], password: Optional[str], mail: EmailMessage) -> None:
    host = config.get("host") or settings.SMTP_HOST
    port = int(config.get("port") or settings.SMTP_PORT)
    username = config.get("username") or settings.SMTP_USERNAME
    use_tls = config.get("use_tls", settings.SMTP_USE_TLS)
    timeout = _timeout()
    if not host:
        raise RuntimeError("no SMTP host configured")
    if port == 465 and use_tls:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    try:
        server.ehlo()
        if use_tls and port != 465:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(mail)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


async def send_email(
    msg: AlertMessage, config: Dict[str, Any], password: Optional[str]
) -> SendResult:
    if not config.get("recipients"):
        return SendResult(False, None, "email channel has no recipients")
    started = time.monotonic()
    try:
        await asyncio.to_thread(_smtp_send, config, password, email_message(msg, config))
        return SendResult(True, 250, None, int((time.monotonic() - started) * 1000))
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "smtp_code", None)
        return SendResult(
            False,
            int(code) if code else None,
            f"{type(exc).__name__}: {exc}"[:500],
            int((time.monotonic() - started) * 1000),
        )


async def send(
    channel_type: str, msg: AlertMessage, config: Dict[str, Any], secret: Optional[str]
) -> SendResult:
    if channel_type == "slack":
        return await send_slack(msg, config, secret)
    if channel_type == "webhook":
        return await send_webhook(msg, config, secret)
    if channel_type == "email":
        return await send_email(msg, config, secret)
    return SendResult(False, None, f"unknown channel type {channel_type}")
