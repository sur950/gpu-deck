"""
Alerts: channels (Slack / email / signed webhook), events with dedupe,
deliveries with retries, test-send.

``emit`` is the entry point for every domain (rules, swaps, fleet, GPU
instances)::

    from app.services.alerts import alerts
    await alerts.emit(
        "node.offline",               # event_type (dot-separated; channels filter on globs)
        "warning",                    # info | warning | critical
        "Node gpu-07 offline",        # title
        "No heartbeat for 90 s",      # message (optional)
        resource_type="node", resource_id=node.id, resource_name=node.name,
        payload={"last_heartbeat_at": "..."},
        dedupe_key=f"node.offline:{node.id}",
    )                                 # -> alert_events.id, or None on failure (never raises)

``emit_sync(...)`` is the same for sync code (Celery failure hooks).

Flow: emit writes/bumps ``alert_events`` (same ``dedupe_key`` within
``ALERT_DEDUPE_WINDOW_SECONDS`` bumps ``occurrences`` and does not notify
again), creates one ``alert_deliveries`` row per matching channel (enabled,
``severity >= min_severity``, ``event_filter`` globs, ``rule.dry_run`` only when
listed explicitly), commits, then hands the event to the ``alerts`` Celery
queue. Failed attempts are retried by the ``alerts.retry_deliveries`` job with
exponential backoff (``ALERT_RETRY_BACKOFF_SECONDS`` x 2^n) up to
``ALERT_MAX_ATTEMPTS``. After the commit every emit (new event or occurrence
bump), ack / resolve and test-send also publishes an ``alert`` live event
(``live.py``; best effort, never raises). Secrets (Slack webhook URL, SMTP password, webhook
signing secret) live in the vault (``credential_id``), never in ``config``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.alert_repository import (
    AlertChannelRepository,
    AlertDeliveryRepository,
    AlertEventRepository,
)
from app.schemas.alerts_schema import (
    SEVERITY_RANK,
    AlertChannelCreate,
    AlertChannelOut,
    AlertChannelUpdate,
    AlertDeliveryOut,
    AlertEventOut,
    AlertSeverity,
    AlertTestResult,
)
from app.services.alerts.live import alert_live_payload, publish_live
from app.services.alerts.senders import AlertMessage, SendResult, send
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SEVERITIES = ("info", "warning", "critical")
EXPLICIT_ONLY_EVENTS = ("rule.dry_run",)
CREDENTIAL_PREFIX = "alert-channel:"
_CREDENTIAL_TYPE = {"slack": "webhook", "webhook": "webhook", "email": "smtp"}


def _rank(severity: Any) -> int:
    value = severity.value if hasattr(severity, "value") else str(severity)
    return SEVERITY_RANK.get(AlertSeverity(value), 0)


def channel_matches(channel: Any, event_type: str, severity: str) -> bool:
    """Enabled, severity at or above min_severity, and event_filter glob match (empty = all)."""
    if not channel.enabled or getattr(channel, "deleted_at", None) is not None:
        return False
    if _rank(severity) < _rank(channel.min_severity):
        return False
    filters = list(channel.event_filter or [])
    if not filters:
        return event_type not in EXPLICIT_ONLY_EVENTS
    return any(fnmatch.fnmatchcase(event_type, pattern) for pattern in filters)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def message_for(event: Any, delivery_id: Optional[uuid.UUID] = None) -> AlertMessage:
    return AlertMessage(
        event_id=str(event.id),
        event_type=event.event_type,
        severity=event.severity,
        title=event.title,
        message=event.message,
        resource_type=event.resource_type,
        resource_id=str(event.resource_id) if event.resource_id else None,
        resource_name=event.resource_name,
        payload=dict(event.payload or {}),
        occurrences=int(event.occurrences or 1),
        first_seen_at=_iso(event.first_seen_at),
        last_seen_at=_iso(event.last_seen_at),
        delivery_id=str(delivery_id) if delivery_id else None,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    return value


async def resolve_channel_secret(session: AsyncSession, channel: Any) -> Optional[str]:
    """Plaintext secret for a channel: its credential, else the env-named default credential."""
    from app.services.vault.service import VaultService

    vault = VaultService(session)
    if channel.credential_id:
        return await vault.get_secret(
            channel.credential_id, purpose="alert delivery", required=False
        )
    default_name = {
        "slack": settings.SLACK_DEFAULT_WEBHOOK_CREDENTIAL,
        "webhook": settings.WEBHOOK_SIGNING_SECRET_CREDENTIAL,
        "email": settings.SMTP_PASSWORD_CREDENTIAL,
    }.get(channel.type)
    if not default_name:
        return None
    return await vault.get_secret(default_name, purpose="alert delivery", required=False)


# Typed vault credential field -> sender config key (SMTP server, webhook URL). The secret
# itself (password / HMAC secret / Slack URL) is the credential's stored value.
_CREDENTIAL_CONFIG = {
    "email": {"host": "host", "port": "port", "username": "username", "from": "from"},
    "webhook": {"url": "url"},
}
_LEGACY_CONFIG_KEYS = {
    "smtp_host": "host",
    "smtp_port": "port",
    "smtp_username": "username",
}


def credential_config(channel_type: str, fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sender settings a typed credential carries for a channel type."""
    out = {
        dst: fields[src]
        for src, dst in _CREDENTIAL_CONFIG.get(channel_type, {}).items()
        if fields and fields.get(src) not in (None, "")
    }
    if channel_type == "email" and fields and fields.get("starttls") is not None:
        out["use_tls"] = bool(fields["starttls"])
    return out


def _legacy_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(config)
    for old, new in _LEGACY_CONFIG_KEYS.items():
        if old in out:
            value = out.pop(old)
            if value not in (None, "") and out.get(new) in (None, ""):
                out[new] = value
    return out


async def channel_send_config(session: AsyncSession, channel: Any) -> Dict[str, Any]:
    """Channel config over the public fields of its typed credential (SMTP host / port /
    username / from / STARTTLS, webhook URL). Reads no secret material."""
    config = _legacy_keys(dict(channel.config or {}))
    credential_id = getattr(channel, "credential_id", None)
    if not credential_id:
        return config
    from app.services.vault.service import VaultService

    fields = await VaultService(session).get_public_fields(credential_id)
    defaults = credential_config(channel.type, fields)
    return {**defaults, **{k: v for k, v in config.items() if v not in (None, "")}}


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


async def _emit_impl(
    session: AsyncSession,
    event_type: str,
    severity: str,
    title: str,
    message: Optional[str],
    resource_type: Optional[str],
    resource_id: Any,
    resource_name: Optional[str],
    payload: Optional[Dict[str, Any]],
    dedupe_key: Optional[str],
) -> Tuple[uuid.UUID, bool, Dict[str, Any]]:
    events = AlertEventRepository(session)
    now = datetime.now(timezone.utc)
    payload = _json_safe(payload or {})
    if dedupe_key:
        existing = await events.find_open_duplicate(
            dedupe_key, settings.ALERT_DEDUPE_WINDOW_SECONDS, now
        )
        if existing is not None:
            await events.bump_occurrence(existing, now, payload)
            return existing.id, False, alert_live_payload(existing)
    event = await events.create(
        severity=severity,
        event_type=str(event_type)[:100],
        title=str(title)[:512],
        message=message,
        resource_type=(resource_type or None) and str(resource_type)[:100],
        resource_id=uuid.UUID(str(resource_id)) if resource_id else None,
        resource_name=(resource_name or None) and str(resource_name)[:255],
        payload=payload,
        dedupe_key=(dedupe_key or None) and str(dedupe_key)[:255],
        status="open",
        occurrences=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    deliveries = AlertDeliveryRepository(session)
    created = False
    for channel in await AlertChannelRepository(session).list_enabled():
        if channel_matches(channel, event.event_type, severity):
            await deliveries.get_or_create(event.id, channel.id)
            created = True
    return event.id, created, alert_live_payload(event, "open")


async def emit(
    event_type: str,
    severity: str,
    title: str,
    message: Optional[str] = None,
    *,
    resource_type: Optional[str] = None,
    resource_id: Any = None,
    resource_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
    session_factory=None,
    enqueue: bool = True,
) -> Optional[uuid.UUID]:
    """Record an alert event and queue its deliveries. Never raises; returns the event id."""
    if severity not in SEVERITIES:
        severity = "warning"
    try:
        if session_factory is None:
            from app.db.postgres.connection import get_session_maker

            session_factory = get_session_maker()
        async with session_factory() as session:
            event_id, has_deliveries, live = await _emit_impl(
                session,
                event_type,
                severity,
                title,
                message,
                resource_type,
                resource_id,
                resource_name,
                payload,
                dedupe_key,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - alerting must never break the caller
        logger.error(f"alerts.emit({event_type!r}) failed: {exc}")
        return None
    logger.info(f"alert {event_type} [{severity}] {title} -> event {event_id}")
    if has_deliveries and enqueue:
        enqueue_event_delivery(event_id)
    await publish_live("alert", live)
    return event_id


def emit_sync(
    event_type: str,
    severity: str,
    title: str,
    message: Optional[str] = None,
    **kwargs: Any,
) -> Optional[uuid.UUID]:
    """``emit`` for sync code; runs on a private event loop in a helper thread."""
    out: Dict[str, Any] = {}

    def _run() -> None:
        out["id"] = asyncio.run(emit(event_type, severity, title, message, **kwargs))

    thread = threading.Thread(target=_run, name="alerts-emit", daemon=True)
    thread.start()
    thread.join(timeout=30)
    return out.get("id")


def enqueue_event_delivery(event_id: uuid.UUID) -> None:
    """Hand an event to the alerts queue; the retry job covers a broker outage. Never raises."""
    try:
        from app.workers.tasks.alerts_tasks import deliver_event

        deliver_event.apply_async(kwargs={"event_id": str(event_id)})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue alert delivery for {event_id} failed ({exc}); retry job will send")


class _AlertsFacade:
    """``alerts.emit(...)`` / ``alerts.emit_sync(...)``."""

    emit = staticmethod(emit)
    emit_sync = staticmethod(emit_sync)


alerts = _AlertsFacade()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def next_attempt_due(delivery: Any, now: datetime) -> bool:
    """Exponential backoff between attempts: base x 2^(attempts-1) after the last attempt."""
    attempts = int(delivery.attempts or 0)
    if attempts == 0:
        return True
    wait = settings.ALERT_RETRY_BACKOFF_SECONDS * (2 ** (attempts - 1))
    last = delivery.updated_at or delivery.created_at
    return last is None or now >= last + timedelta(seconds=wait)


async def deliver_one(
    session: AsyncSession, delivery_id: uuid.UUID, force: bool = False
) -> Optional[str]:
    """
    One attempt for one delivery row, holding ``FOR UPDATE SKIP LOCKED`` so a
    Celery task and the retry job never send the same row twice. Returns the
    resulting status, or None when the row is locked / not due / not pending.
    """
    from app.db.postgres.models.generated_models import AlertDeliveries
    from sqlalchemy import select

    row = (
        await session.execute(
            select(AlertDeliveries)
            .where(AlertDeliveries.id == delivery_id)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None or row.status not in ("pending",):
        return None
    now = datetime.now(timezone.utc)
    if not force and not next_attempt_due(row, now):
        return None
    deliveries = AlertDeliveryRepository(session)
    channel = await AlertChannelRepository(session).get_by_id(row.channel_id)
    if channel is None or channel.deleted_at is not None or not channel.enabled:
        await deliveries.suppress(row, "channel disabled or deleted")
        return "suppressed"
    event = await AlertEventRepository(session).get_by_id(row.alert_event_id)
    if event is None:
        await deliveries.suppress(row, "event deleted")
        return "suppressed"
    secret = await resolve_channel_secret(session, channel)
    config = await channel_send_config(session, channel)
    result = await send(channel.type, message_for(event, row.id), config, secret)
    await deliveries.record_attempt(
        row,
        result.ok,
        result.response_code,
        result.error,
        now,
        settings.ALERT_MAX_ATTEMPTS,
    )
    if result.ok:
        logger.info(f"alert {event.event_type} delivered to {channel.type}:{channel.name}")
    else:
        logger.warning(
            f"alert {event.event_type} to {channel.type}:{channel.name} attempt "
            f"{row.attempts}/{settings.ALERT_MAX_ATTEMPTS} failed: {result.error}"
        )
    return row.status


async def deliver_event(event_id: uuid.UUID, session_factory=None) -> Dict[str, int]:
    """First attempt for every pending delivery of an event (Celery task body)."""
    from app.db.postgres.connection import get_session_maker

    factory = session_factory or get_session_maker()
    counts: Dict[str, int] = {}
    async with factory() as session:
        ids = [
            d.id
            for d in await AlertDeliveryRepository(session).for_event(event_id)
            if d.status == "pending"
        ]
    for delivery_id in ids:
        async with factory() as session:
            status = await deliver_one(session, delivery_id)
            await session.commit()
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


async def retry_pending(session_factory=None, limit: int = 200) -> Dict[str, int]:
    """Retry job body: every pending delivery whose backoff has elapsed."""
    from app.db.postgres.connection import get_session_maker

    factory = session_factory or get_session_maker()
    async with factory() as session:
        rows = await AlertDeliveryRepository(session).list_pending(
            settings.ALERT_MAX_ATTEMPTS, limit
        )
        now = datetime.now(timezone.utc)
        due = [r.id for r in rows if r.status == "pending" and next_attempt_due(r, now)]
    counts: Dict[str, int] = {}
    for delivery_id in due:
        async with factory() as session:
            status = await deliver_one(session, delivery_id)
            await session.commit()
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Admin API service
# ---------------------------------------------------------------------------


class AlertService:
    """Channels CRUD + test, events list / ack / resolve / resend."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.channels = AlertChannelRepository(session)
        self.events = AlertEventRepository(session)
        self.deliveries = AlertDeliveryRepository(session)

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "AlertService":
        return AlertService(db)

    # ------------------------------------------------------------------ channels

    @staticmethod
    def _validate_config(
        channel_type: str,
        config: Dict[str, Any],
        has_secret: bool,
        provided: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """``provided``: settings the channel's typed credential carries (SMTP host, webhook
        URL); the channel config may leave those out."""
        config = _legacy_keys(dict(config or {}))
        provided = provided or {}
        for key in ("password", "secret", "token", "signing_secret", "webhook_url"):
            if key in config and channel_type != "slack":
                raise ValidationError(f"config.{key}: secrets go in 'secret' (stored in the vault)")
        if channel_type == "webhook":
            url = str(config.get("url") or provided.get("url") or "")
            if not url.startswith(("http://", "https://")):
                raise ValidationError("config.url: http(s) URL required for webhook channels")
            from urllib.parse import urlsplit

            from app.services.alerts.senders import blocked_address

            # Literal IPs are checked here; host names are resolved and checked at send time.
            host = urlsplit(url).hostname or ""
            reason = blocked_address(host)
            if reason and not reason.startswith("unparseable"):
                raise ValidationError(f"config.url: destination not allowed ({reason})")
            if config.get("headers") is not None and not isinstance(config["headers"], dict):
                raise ValidationError("config.headers must be an object")
        elif channel_type == "email":
            recipients = config.get("recipients")
            if isinstance(recipients, str):
                recipients = [r.strip() for r in recipients.split(",") if r.strip()]
                config["recipients"] = recipients
            if not recipients or not all("@" in str(r) for r in recipients):
                raise ValidationError("config.recipients: at least one email address required")
            if not (config.get("host") or provided.get("host") or settings.SMTP_HOST):
                raise ValidationError(
                    "config.host required: pick an SMTP credential (no SMTP_HOST default)"
                )
        elif channel_type == "slack":
            if "webhook_url" in config:
                raise ValidationError("config.webhook_url: put the Slack webhook URL in 'secret'")
            if (
                not has_secret
                and not config.get("url")
                and not settings.SLACK_DEFAULT_WEBHOOK_CREDENTIAL
            ):
                raise ValidationError("Slack channels need 'secret' (webhook URL) or credential_id")
        return config

    async def _credential_settings(
        self, channel_type: str, credential_id: Optional[uuid.UUID]
    ) -> Dict[str, Any]:
        """The credential must fit the channel type (Slack webhook / SMTP server / signed
        webhook; untyped legacy secrets of the same category pass). Returns the settings it
        carries (SMTP host ..., webhook URL)."""
        if credential_id is None:
            return {}
        from app.services.vault import types as credential_kinds
        from app.services.vault.service import VaultService, require_compatible

        accepted = credential_kinds.ALERT_CHANNEL_KINDS.get(channel_type, [])
        await require_compatible(
            self.session, credential_id, accepted, f"A {channel_type} alert channel"
        )
        fields = await VaultService(self.session).get_public_fields(credential_id)
        return credential_config(channel_type, fields)

    @staticmethod
    def _inline_fields(channel_type: str, secret: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Typed fields for a secret typed into the channel form (stored as a credential)."""
        if channel_type == "slack":
            return {"webhook_url": secret}
        if channel_type == "webhook":
            return {"hmac_secret": secret, "url": config.get("url")}
        return {
            "password": secret,
            "host": config.get("host") or settings.SMTP_HOST,
            "port": config.get("port") or settings.SMTP_PORT,
            "username": config.get("username") or settings.SMTP_USERNAME,
            "from": config.get("from") or settings.SMTP_FROM,
            "starttls": config.get("use_tls", settings.SMTP_USE_TLS),
        }

    async def _store_secret(
        self,
        name: str,
        channel_type: str,
        secret: str,
        existing_id: Optional[uuid.UUID],
        created_by,
        config: Optional[Dict[str, Any]] = None,
    ) -> uuid.UUID:
        from app.services.vault import types as credential_kinds
        from app.services.vault.service import VaultService

        vault = VaultService(self.session)
        if existing_id is not None:
            cred = await vault.get_credential(existing_id)
            if str(cred.get("name", "")).startswith(CREDENTIAL_PREFIX):
                await vault.update_credential(existing_id, {"value": secret})
                return existing_id
        base = {
            "name": f"{CREDENTIAL_PREFIX}{name}:{uuid.uuid4().hex[:8]}",
            "description": f"Secret of alert channel {name}",
            "metadata": {"alert_channel": name},
        }
        kind = (credential_kinds.ALERT_CHANNEL_KINDS.get(channel_type) or [None])[0]
        try:
            cred = await vault.create_credential(
                {
                    **base,
                    "provider": kind,
                    "fields": self._inline_fields(channel_type, secret, dict(config or {})),
                },
                created_by=created_by,
            )
        except credential_kinds.CredentialValidationError as exc:
            # Not a complete typed credential (e.g. no SMTP host yet): keep it untyped.
            logger.info(f"alert channel {name}: secret stored untyped ({exc.detail})")
            cred = await vault.create_credential(
                {
                    **base,
                    "type": _CREDENTIAL_TYPE.get(channel_type, "other"),
                    "provider": "alerts",
                    "value": secret,
                },
                created_by=created_by,
            )
        return uuid.UUID(str(cred["id"]))

    async def _get_channel(self, channel_id: uuid.UUID):
        channel = await self.channels.get_active(channel_id)
        if channel is None:
            raise ResourceNotFoundError(resource_type="Alert channel")
        return channel

    async def list_channels(self, **filters: Any) -> Tuple[List[AlertChannelOut], int]:
        rows, total = await self.channels.list(**filters)
        return [AlertChannelOut.model_validate(r) for r in rows], total

    async def get_channel(self, channel_id: uuid.UUID) -> AlertChannelOut:
        return AlertChannelOut.model_validate(await self._get_channel(channel_id))

    async def create_channel(self, payload: AlertChannelCreate, created_by) -> AlertChannelOut:
        from app.core.exceptions import DuplicateResourceError
        from app.services.audit import audit

        if await self.channels.name_exists(payload.name):
            raise DuplicateResourceError(resource_type="Alert channel", field="name")
        channel_type = payload.type.value
        has_secret = bool(payload.secret or payload.credential_id)
        provided = await self._credential_settings(channel_type, payload.credential_id)
        config = self._validate_config(channel_type, payload.config, has_secret, provided)
        credential_id = payload.credential_id
        if payload.secret:
            credential_id = await self._store_secret(
                payload.name, channel_type, payload.secret, None, created_by, config
            )
        row = await self.channels.create(
            name=payload.name,
            type=channel_type,
            config=config,
            credential_id=credential_id,
            enabled=payload.enabled,
            min_severity=(
                payload.min_severity.value
                if payload.min_severity
                else settings.ALERT_DEFAULT_MIN_SEVERITY
            ),
            event_filter=list(payload.event_filter or []),
            created_by=created_by,
        )
        out = AlertChannelOut.model_validate(row)
        audit.record(
            "alert_channel.created",
            resource_type="alert_channel",
            resource_id=row.id,
            resource_name=row.name,
            after_state=out.model_dump(mode="json"),
        )
        return out

    async def update_channel(
        self, channel_id: uuid.UUID, payload: AlertChannelUpdate
    ) -> AlertChannelOut:
        from app.core.exceptions import DuplicateResourceError
        from app.services.audit import audit

        row = await self._get_channel(channel_id)
        before = AlertChannelOut.model_validate(row).model_dump(mode="json")
        data = payload.model_dump(exclude_unset=True)
        if (
            "name" in data
            and data["name"]
            and await self.channels.name_exists(data["name"], channel_id)
        ):
            raise DuplicateResourceError(resource_type="Alert channel", field="name")
        changes: Dict[str, Any] = {}
        for key in ("name", "enabled", "event_filter"):
            if key in data and data[key] is not None:
                changes[key] = data[key]
        if data.get("min_severity") is not None:
            changes["min_severity"] = payload.min_severity.value
        if "credential_id" in data:
            changes["credential_id"] = data["credential_id"]
        secret = data.get("secret")
        has_secret = bool(secret or changes.get("credential_id", row.credential_id))
        provided: Dict[str, Any] = {}
        if changes.get("credential_id") is not None:
            provided = await self._credential_settings(row.type, changes["credential_id"])
        elif row.credential_id is not None:
            from app.services.vault.service import VaultService

            provided = credential_config(
                row.type,
                await VaultService(self.session).get_public_fields(row.credential_id),
            )
        if "config" in data and data["config"] is not None:
            changes["config"] = self._validate_config(
                row.type, data["config"], has_secret, provided
            )
        if secret:
            changes["credential_id"] = await self._store_secret(
                changes.get("name", row.name),
                row.type,
                secret,
                changes.get("credential_id", row.credential_id),
                row.created_by,
                changes.get("config", row.config),
            )
        updated = await self.channels.update(channel_id, **changes)
        out = AlertChannelOut.model_validate(updated)
        audit.record(
            "alert_channel.updated",
            resource_type="alert_channel",
            resource_id=row.id,
            resource_name=out.name,
            before_state=before,
            after_state=out.model_dump(mode="json"),
        )
        return out

    async def delete_channel(self, channel_id: uuid.UUID) -> None:
        from app.services.audit import audit

        row = await self._get_channel(channel_id)
        await self.channels.soft_delete(channel_id)
        audit.record(
            "alert_channel.deleted",
            resource_type="alert_channel",
            resource_id=row.id,
            resource_name=row.name,
        )

    async def test_channel(self, channel_id: uuid.UUID) -> AlertTestResult:
        from app.services.audit import audit

        channel = await self._get_channel(channel_id)
        now = datetime.now(timezone.utc)
        msg = AlertMessage(
            event_id=str(uuid.uuid4()),
            event_type="alert.test",
            severity="info",
            title=f"Test alert from {settings.APP_NAME}",
            message=f"Channel '{channel.name}' is configured correctly.",
            resource_type="alert_channel",
            resource_id=str(channel.id),
            resource_name=channel.name,
            first_seen_at=now.isoformat(),
            last_seen_at=now.isoformat(),
            test=True,
        )
        try:
            secret = await resolve_channel_secret(self.session, channel)
        except Exception as exc:  # noqa: BLE001
            secret = None
            logger.warning(f"alert test: secret for channel {channel.id} unavailable: {exc}")
        config = await channel_send_config(self.session, channel)
        await self.channels.release_connection()
        result: SendResult = await send(channel.type, msg, config, secret)
        await self.channels.record_test(channel.id, result.ok, result.error, now)
        audit.record(
            "alert_channel.tested",
            resource_type="alert_channel",
            resource_id=channel.id,
            resource_name=channel.name,
            outcome="success" if result.ok else "failure",
            message=result.error,
        )
        await publish_live(
            "alert",
            {
                "kind": "test",
                "test": True,
                "id": f"test:{channel.id}:{now.isoformat()}",
                "event_type": "alert.test",
                "title": "Test alert",
                "severity": "info",
                "channel_id": channel.id,
                "channel_name": channel.name,
                "channel_type": channel.type,
                "ok": result.ok,
                "message": result.error,
                "resource_type": "alert_channel",
                "resource_id": channel.id,
                "resource_name": channel.name,
            },
        )
        return AlertTestResult(
            ok=result.ok,
            sent_at=now,
            response_code=result.response_code,
            error=result.error,
            latency_ms=result.latency_ms,
        )

    # ------------------------------------------------------------------ events

    async def _events_out(self, rows: List[Any]) -> List[AlertEventOut]:
        deliveries = await self.deliveries.for_events([r.id for r in rows])
        channel_ids = list({d.channel_id for ds in deliveries.values() for d in ds})
        names = await self.channels.names(channel_ids)
        out = []
        for row in rows:
            data = AlertEventOut.model_validate(row)
            data.deliveries = [
                AlertDeliveryOut.model_validate(d).model_copy(
                    update={"channel_name": names.get(d.channel_id)}
                )
                for d in deliveries.get(row.id, [])
            ]
            out.append(data)
        return out

    async def list_events(self, **filters: Any) -> Tuple[List[AlertEventOut], int]:
        rows, total = await self.events.list(**filters)
        return await self._events_out(rows), total

    async def _get_event(self, event_id: uuid.UUID):
        row = await self.events.get_by_id(event_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Alert event")
        return row

    async def get_event(self, event_id: uuid.UUID) -> AlertEventOut:
        return (await self._events_out([await self._get_event(event_id)]))[0]

    async def set_status(self, event_id: uuid.UUID, status: str) -> AlertEventOut:
        from app.services.audit import audit

        row = await self._get_event(event_id)
        now = datetime.now(timezone.utc)
        changes: Dict[str, Any] = {"status": status}
        if status == "acknowledged" and row.acknowledged_at is None:
            changes["acknowledged_at"] = now
        if status == "resolved":
            changes["resolved_at"] = now
            if row.acknowledged_at is None:
                changes["acknowledged_at"] = now
        await self.events.update(event_id, **changes)
        audit.record(
            f"alert_event.{status}",
            resource_type="alert_event",
            resource_id=row.id,
            resource_name=row.title,
        )
        out = await self.get_event(event_id)
        # Commit before announcing so a client refetching on the frame sees the new status
        # (get_db's own commit at the end of the request is then a no-op).
        await self.session.commit()
        await publish_live("alert", alert_live_payload(out, status))
        return out

    async def resend(self, event_id: uuid.UUID, channel_id: Optional[uuid.UUID]) -> AlertEventOut:
        from app.services.audit import audit

        row = await self._get_event(event_id)
        if channel_id is not None:
            channels = [await self._get_channel(channel_id)]
        else:
            channels = [
                c
                for c in await self.channels.list_enabled()
                if channel_matches(c, row.event_type, row.severity)
            ]
        targets = []
        for channel in channels:
            delivery = await self.deliveries.get_or_create(row.id, channel.id)
            await self.deliveries.reset_for_resend(delivery)
            targets.append(delivery.id)
        await self.events.release_connection()
        for delivery_id in targets:
            await deliver_one(self.session, delivery_id, force=True)
            await self.session.commit()
        audit.record(
            "alert_event.resent",
            resource_type="alert_event",
            resource_id=row.id,
            resource_name=row.title,
            metadata={"channels": [str(c.id) for c in channels]},
        )
        return await self.get_event(event_id)
