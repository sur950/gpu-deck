"""
Unit tests: alert channel matching, HMAC signing and delivery to local
loopback servers (HTTP for Slack/webhook, a minimal SMTP responder for email).
"""

import json
import socket
import socketserver
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from app.core.settings import settings
from app.services.alerts.senders import (
    AlertMessage,
    send,
    sign_payload,
    slack_body,
    verify_signature,
)
from app.services.alerts.service import channel_matches, next_attempt_due


class Recorder(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Recorder.received.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(Recorder.status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    Recorder.received = []
    Recorder.status = 200
    server = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class SmtpHandler(socketserver.StreamRequestHandler):
    messages = []

    def _send(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self._send("220 localhost test smtp")
        data_mode, buf, envelope = False, [], {"rcpt": []}
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            if data_mode:
                if line == ".":
                    data_mode = False
                    SmtpHandler.messages.append({**envelope, "data": "\n".join(buf)})
                    self._send("250 queued")
                else:
                    buf.append(line)
                continue
            cmd = line.upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                self._send("250 localhost")
            elif cmd.startswith("MAIL FROM"):
                envelope["from"] = line
                self._send("250 ok")
            elif cmd.startswith("RCPT TO"):
                envelope["rcpt"].append(line)
                self._send("250 ok")
            elif cmd == "DATA":
                data_mode = True
                self._send("354 end with .")
            elif cmd == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("250 ok")


@pytest.fixture
def smtp_server():
    SmtpHandler.messages = []
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SmtpHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def message(**extra):
    return AlertMessage(
        event_id="e1",
        event_type="swap.failed",
        severity="critical",
        title="Swap unload failed",
        message="drain timed out",
        resource_type="swap_operation",
        resource_id="r1",
        payload={"k": 1},
        delivery_id="d1",
        **extra,
    )


def channel(**extra):
    base = {
        "enabled": True,
        "deleted_at": None,
        "min_severity": "warning",
        "event_filter": [],
    }
    base.update(extra)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- matching


def test_channel_matching_severity_and_filters():
    assert channel_matches(channel(), "swap.failed", "critical")
    assert not channel_matches(channel(), "rule.fired", "info")  # below min_severity
    assert channel_matches(channel(min_severity="info"), "rule.fired", "info")
    assert not channel_matches(channel(enabled=False), "swap.failed", "critical")
    assert channel_matches(channel(event_filter=["swap.*"]), "swap.rolled_back", "warning")
    assert not channel_matches(channel(event_filter=["swap.*"]), "rule.error", "warning")


def test_dry_run_alerts_need_an_explicit_filter():
    assert not channel_matches(channel(min_severity="info"), "rule.dry_run", "info")
    assert channel_matches(
        channel(min_severity="info", event_filter=["rule.*"]), "rule.dry_run", "info"
    )


def test_retry_backoff_doubles(monkeypatch):
    monkeypatch.setattr(settings, "ALERT_RETRY_BACKOFF_SECONDS", 30)
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    fresh = SimpleNamespace(attempts=0, updated_at=now, created_at=now)
    assert next_attempt_due(fresh, now)
    one = SimpleNamespace(attempts=1, updated_at=now - timedelta(seconds=29), created_at=now)
    assert not next_attempt_due(one, now)
    two = SimpleNamespace(attempts=2, updated_at=now - timedelta(seconds=61), created_at=now)
    assert next_attempt_due(two, now)


def test_signature_roundtrip():
    sig = sign_payload("s3cret", "1700000000", b'{"a":1}')
    assert sig.startswith("sha256=")
    assert verify_signature("s3cret", "1700000000", b'{"a":1}', sig)
    assert not verify_signature("other", "1700000000", b'{"a":1}', sig)
    assert not verify_signature("s3cret", "1700000001", b'{"a":1}', sig)


# ---------------------------------------------------------------- delivery


async def test_webhook_delivery_is_signed(http_server):
    result = await send(
        "webhook",
        message(),
        {"url": f"{http_server}/hook", "headers": {"X-Team": "gpu"}},
        "s3cret",
    )
    assert result.ok and result.response_code == 200
    req = Recorder.received[0]
    body = req["body"]
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert headers["x-gpudeck-event"] == "swap.failed"
    assert headers["x-gpudeck-delivery"] == "d1"
    assert headers["x-team"] == "gpu"
    assert verify_signature(
        "s3cret", headers["x-gpudeck-timestamp"], body, headers["x-gpudeck-signature"]
    )
    payload = json.loads(body)
    assert payload["event_type"] == "swap.failed" and payload["resource"]["id"] == "r1"


async def test_webhook_unsigned_without_secret_and_failure_status(http_server):
    Recorder.status = 500
    result = await send("webhook", message(), {"url": f"{http_server}/hook"}, None)
    assert not result.ok and result.response_code == 500
    assert "x-gpudeck-signature" not in {k.lower() for k in Recorder.received[0]["headers"]}


async def test_webhook_connection_refused_is_a_failed_attempt():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = await send("webhook", message(), {"url": f"http://127.0.0.1:{port}/x"}, None)
    assert not result.ok and result.response_code is None and result.error


async def test_slack_delivery(http_server):
    result = await send("slack", message(), {"channel": "#gpu"}, f"{http_server}/services/T/B/X")
    assert result.ok
    body = json.loads(Recorder.received[0]["body"])
    assert body["channel"] == "#gpu"
    assert body["text"].startswith("[CRITICAL] Swap unload failed")
    assert any("drain timed out" in json.dumps(b) for b in body["blocks"])


def test_slack_body_marks_tests():
    assert slack_body(message(test=True), {})["text"].startswith("[TEST]")


async def test_email_delivery(smtp_server):
    config = {
        "host": "127.0.0.1",
        "port": smtp_server,
        "use_tls": False,
        "recipients": ["ops@example.com", "gpu@example.com"],
        "from": "deck@example.com",
    }
    result = await send("email", message(), config, None)
    assert result.ok, result.error
    sent = SmtpHandler.messages[0]
    assert len(sent["rcpt"]) == 2
    assert "Swap unload failed" in sent["data"] and "drain timed out" in sent["data"]


async def test_email_without_recipients_fails_fast():
    result = await send("email", message(), {"host": "127.0.0.1"}, None)
    assert not result.ok and "recipients" in result.error
