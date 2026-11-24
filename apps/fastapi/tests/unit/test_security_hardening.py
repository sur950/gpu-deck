"""
Regression tests for security and robustness findings from the backend hardening review.

Each test pins one finding: it failed before the fix and passes after it.
"""

import asyncio
import threading
import uuid

import pytest
from app.core.settings import settings
from app.middlewares.cookie_auth import CookieAuthMiddleware
from app.middlewares.csrf import CSRFMiddleware
from app.services.auth.csrf import generate_csrf_token
from app.services.auth.token_crypto import generate_session_token
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# ---------------------------------------------------------------------------
# A non-Bearer Authorization header (browser-cached Basic credentials of an
# auth proxy) must not switch CSRF off while the session cookie authenticates.
# ---------------------------------------------------------------------------


async def _echo(request: Request):
    return JSONResponse({"authorization": request.headers.get("authorization")})


def _csrf_app():
    app = Starlette(routes=[Route(f"{settings.API_V1_PREFIX}/things", _echo, methods=["POST"])])
    app.add_middleware(CookieAuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    return app


async def _post(headers):
    async with AsyncClient(transport=ASGITransport(app=_csrf_app()), base_url="http://t") as c:
        return await c.post(f"{settings.API_V1_PREFIX}/things", headers=headers)


@pytest.mark.asyncio
async def test_csrf_not_skipped_for_basic_authorization_with_session_cookie():
    raw, token_hash = generate_session_token(str(uuid.uuid4()), str(uuid.uuid4()))
    csrf = generate_csrf_token(token_hash)
    cookies = f"{settings.SESSION_COOKIE_NAME}={raw}; {settings.CSRF_COOKIE_NAME}={csrf}"
    forged = await _post({"cookie": cookies, "authorization": "Basic YWRtaW46cHc="})
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "CSRF_ERROR"
    empty_bearer = await _post({"cookie": cookies, "authorization": "Bearer "})
    assert empty_bearer.status_code == 403
    # Real bearer clients (CLI / Terraform) stay exempt; double submit still works.
    assert (await _post({"cookie": cookies, "authorization": "Bearer cli"})).status_code == 200
    ok = await _post({"cookie": cookies, settings.CSRF_HEADER_NAME: csrf})
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# After losing the jobs-leader fence the old driver must not release the
# swap's device locks (same owner string as the new leader that resumed the swap).
# ---------------------------------------------------------------------------


def _swap_harness(fence=None):
    from app.services.swap.executor import SwapExecutor
    from app.services.swap.fakes import (
        FakeAgent,
        FakeHealth,
        FakeLoadArgs,
        FakeRouting,
        MemoryLocks,
        MemorySwapStore,
    )
    from app.services.swap.planner import build_plan
    from app.services.swap.ports import InstanceRecord, Swap

    store, locks = MemorySwapStore(), MemoryLocks()
    dev = uuid.uuid4()
    old = store.add_instance(
        InstanceRecord(
            id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            node_id=uuid.uuid4(),
            device_ids=[dev],
            state="ready",
            health="healthy",
            port=10240,
            served_model_name="m",
            traffic_weight=100,
        )
    )
    built = build_plan({"kind": "unload", "instance_id": old.id}, [dev])
    swap = store.add_swap(
        Swap(
            id=uuid.uuid4(),
            kind=built["kind"],
            state="pending",
            trigger="rule",
            deployment_id=old.deployment_id,
            from_instance_id=old.id,
            node_id=old.node_id,
            plan=built["plan"],
            steps=built["steps"],
            drain_timeout_seconds=60,
        )
    )
    executor = SwapExecutor(
        store=store,
        commands=FakeAgent(),
        routing=FakeRouting(),
        load_args=FakeLoadArgs(),
        health=FakeHealth(),
        locks=locks,
        fence=fence,
    )
    return executor, store, locks, swap, dev


@pytest.mark.asyncio
async def test_swap_keeps_device_lock_when_fenced_store_write_fails():
    executor, store, locks, swap, dev = _swap_harness()
    store.fence_ok = False  # the new leader took the lease; its driver owns the lock now
    await executor.drive(swap.id)
    assert locks.held.get(f"swap:lock:device:{dev}") == f"swap:{swap.id}"


@pytest.mark.asyncio
async def test_swap_keeps_device_lock_when_fence_check_fails():
    async def fence():
        return False

    executor, store, locks, swap, dev = _swap_harness(fence=fence)
    done = await executor.drive(swap.id)
    assert done.state == "pending"
    assert locks.held.get(f"swap:lock:device:{dev}") == f"swap:{swap.id}"


@pytest.mark.asyncio
async def test_swap_releases_device_lock_when_it_finishes():
    executor, store, locks, swap, dev = _swap_harness()
    done = await executor.drive(swap.id)
    assert done.state == "completed"
    assert f"swap:lock:device:{dev}" not in locks.held


# ---------------------------------------------------------------------------
# SSH GPU instances are keys-only; request env may not re-enable passwords /
# sudo, swap the keys or widen GPU visibility / slice limits.
# ---------------------------------------------------------------------------

_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl t@x"


@pytest.mark.parametrize(
    "key",
    [
        "PASSWORD_ACCESS",
        "USER_PASSWORD",
        "SUDO_ACCESS",
        "PUBLIC_KEY",
        "USER_NAME",
        "NVIDIA_VISIBLE_DEVICES",
        "CUDA_MPS_ACTIVE_THREAD_PERCENTAGE",
        "GPUDECK_SSH_USER",
        "password_access",
    ],
)
def test_gpu_instance_env_rejects_managed_variables(key):
    from app.schemas.gpu_instances_schema import GpuInstanceCreate
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="managed by GPU Deck"):
        GpuInstanceCreate(name="dev", authorized_keys=[_KEY], env={key: "true"})


def test_gpu_instance_env_allows_ordinary_variables():
    from app.schemas.gpu_instances_schema import GpuInstanceCreate

    body = GpuInstanceCreate(name="dev", authorized_keys=[_KEY], env={"TZ": "UTC", "HF_HOME": "/x"})
    assert body.env == {"TZ": "UTC", "HF_HOME": "/x"}


# ---------------------------------------------------------------------------
# Cluster egress allow-list entries end up in pf / nftables rules on the
# nodes; only IPs, CIDRs and host names (optional :port) are accepted.
# ---------------------------------------------------------------------------


def test_cluster_egress_allowlist_accepts_hosts_ips_and_cidrs():
    from app.schemas.fleet_schema import ClusterCreate

    entries = ["huggingface.co:443", "10.0.0.0/8", "10.1.2.3:443", "[2001:db8::1]:443"]
    assert ClusterCreate(name="c", type="vm", egress_allowlist=entries).egress_allowlist == entries


@pytest.mark.parametrize(
    "entry",
    [
        "1.2.3.4/32\npass out quick all",
        "10.0.0.0/8 }",
        "10.0.0.0/99",
        "a b",
        "h:70000",
        "-x",
    ],
)
def test_cluster_egress_allowlist_rejects_rule_injection(entry):
    from app.schemas.fleet_schema import ClusterUpdate
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ClusterUpdate(egress_allowlist=[entry])


# ---------------------------------------------------------------------------
# Alert deliveries never reach cloud metadata / link-local addresses (SSRF from
# the jobs process; the channel test endpoint returns the response body).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://0.0.0.0:8180/api/v1/nodes",
        "http://[::ffff:169.254.169.254]/",
    ],
)
async def test_alert_webhook_refuses_metadata_and_link_local(url):
    from app.services.alerts.senders import AlertMessage, send

    msg = AlertMessage(
        event_id=str(uuid.uuid4()),
        event_type="node.offline",
        severity="critical",
        title="t",
        message="m",
    )
    result = await asyncio.wait_for(send("webhook", msg, {"url": url}, None), timeout=5)
    assert result.ok is False
    assert "blocked" in (result.error or "")


def test_alert_channel_config_rejects_metadata_url():
    from app.core.exceptions import ValidationError
    from app.services.alerts.service import AlertService

    with pytest.raises(ValidationError, match="not allowed"):
        AlertService._validate_config("webhook", {"url": "http://169.254.169.254/x"}, False)
    ok = AlertService._validate_config("webhook", {"url": "http://10.0.0.5/hook"}, False)
    assert ok["url"] == "http://10.0.0.5/hook"


# ---------------------------------------------------------------------------
# The hub result waiter must keep one subscription per event loop. The Celery
# threads worker runs each task on its own thread + loop; a second task used to stop
# the first task's subscription from a foreign loop and drop its pending futures.
# ---------------------------------------------------------------------------


def test_result_waiter_is_per_event_loop(monkeypatch):
    from app.services.agent_hub import bus
    from app.services.agent_hub import service as hub_service

    subs = []

    class FakeSub:
        def __init__(self, name, handler):
            self.handler, self.stopped, self.loop = handler, False, None
            subs.append(self)

        async def start(self):
            self.loop = asyncio.get_running_loop()

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(bus, "Subscription", FakeSub)
    waiter = hub_service._ResultWaiter()
    command_id = uuid.uuid4()
    a_registered, b_done = threading.Event(), threading.Event()
    outcome = {}

    async def task_a():
        await waiter.ensure()
        fut = waiter.register(command_id)
        a_registered.set()
        await asyncio.get_running_loop().run_in_executor(None, b_done.wait, 5)
        await subs[0].handler({"command_id": str(command_id), "state": "succeeded"})
        outcome["a"] = (await asyncio.wait_for(fut, 1))["state"]
        waiter.forget(command_id, fut)

    async def task_b():
        await waiter.ensure()
        fut = waiter.register(uuid.uuid4())
        outcome["b_loop_ok"] = subs[-1].loop is asyncio.get_running_loop()
        fut.cancel()

    def run_b():
        a_registered.wait(5)
        asyncio.run(task_b())
        b_done.set()

    tb = threading.Thread(target=run_b)
    tb.start()
    asyncio.run(task_a())
    tb.join(5)
    assert outcome == {"a": "succeeded", "b_loop_ok": True}
    assert len(subs) == 2 and not subs[0].stopped


# ---------------------------------------------------------------------------
# The agent hub listener caps request bodies like the admin API (the join POST
# is parsed before the one-time token is checked).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_hub_rejects_oversized_join_body(monkeypatch):
    from app.agent_hub_main import app as hub_app

    monkeypatch.setattr(settings, "ADMIN_MAX_REQUEST_BODY_BYTES", 1024)
    async with AsyncClient(transport=ASGITransport(app=hub_app), base_url="http://hub") as c:
        r = await c.post(
            "/agent/v1/join",
            content=b"{" + b" " * 4096 + b"}",
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 413


# ---------------------------------------------------------------------------
# /live/ws authenticates with the session cookie, so a browser handshake from a
# foreign origin must be refused (cross-site WebSocket hijacking).
# ---------------------------------------------------------------------------


def test_live_ws_origin_check(monkeypatch):
    from app.api.v1.routes.metrics import ws_origin_allowed

    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://deck.example.com")
    monkeypatch.setattr(settings, "PUBLIC_API_URL", "https://deck-admin.example.com")
    assert ws_origin_allowed(None, "deck-admin.example.com")  # CLI / scripts (bearer)
    assert ws_origin_allowed("https://deck.example.com", "deck-admin.example.com")
    assert ws_origin_allowed("https://deck-admin.example.com/", "deck-admin.example.com")
    assert ws_origin_allowed("http://localhost:8180", "localhost:8180")  # same origin
    assert not ws_origin_allowed("https://evil.example.com", "deck-admin.example.com")
    assert not ws_origin_allowed("null", "deck-admin.example.com")


def test_live_ws_refuses_foreign_origin_before_session_lookup():
    from app.main import app as admin_app
    from starlette.testclient import TestClient

    async def allow(ip, scope):
        return type("Decision", (), {"allowed": True, "reason": "test"})()

    called = []

    async def no_session_lookup(self, token):  # must not be reached for a foreign origin
        called.append(token)
        return None

    client = TestClient(admin_app)  # no lifespan: no DB / Redis
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.middlewares.ip_rules.check_ip", allow)
        mp.setattr(
            "app.services.auth.session_manager.SessionManager.validate_session",
            no_session_lookup,
        )
        with client.websocket_connect(
            f"{settings.API_V1_PREFIX}/live/ws",
            headers={
                "origin": "https://evil.example.com",
                "cookie": "gpudeck_session=x",
            },
        ) as ws:
            closed = ws.receive()
    assert closed["type"] == "websocket.close" and closed["code"] == 4401
    assert called == []


# ---------------------------------------------------------------------------
# In-flight counters: shutdown clears this process's counts in Redis, and a long
# stream keeps its instance key alive (TTL refreshed while the count is non-zero).
# ---------------------------------------------------------------------------


class _FakePipe:
    def __init__(self, log):
        self.log = log

    def hset(self, *a):
        self.log.append(("hset",) + a)

    def hdel(self, *a):
        self.log.append(("hdel",) + a)

    def expire(self, *a):
        self.log.append(("expire",) + a)

    async def execute(self):
        return []


@pytest.mark.asyncio
async def test_inflight_tracker_flush_and_stop(monkeypatch):
    from app.services.serving import inflight

    log = []

    class FakeRedis:
        def pipeline(self, transaction=False):
            return _FakePipe(log)

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr("app.db.redis.client.get_redis", fake_get_redis)
    tracker = inflight.InflightTracker()
    tracker.acquire("long-stream")
    await tracker.flush()
    log.clear()
    await tracker.flush()  # nothing dirty, the stream is still open
    assert any(op[0] == "expire" and op[1].endswith("inflight:long-stream") for op in log)
    log.clear()
    await tracker.stop()
    assert any(op[0] == "hdel" and op[1].endswith("inflight:long-stream") for op in log)
    assert any(op[0] == "hdel" and op[1].endswith("inflight:procs") for op in log)
    assert tracker.counts == {} and tracker.total == 0


# ---------------------------------------------------------------------------
# Importing a task module before celery_app (any producer process) used to
# register that module with queue "default" (QUEUE not defined yet) and drop its queue.
# ---------------------------------------------------------------------------


def test_task_module_imported_first_keeps_its_queue():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import app.workers.tasks.benchmark_tasks\n"
        "from app.workers.celery_app import celery_app, queue_names\n"
        "route = celery_app.conf.task_routes['app.workers.tasks.benchmark_tasks.*']\n"
        "sys.__stdout__.write('RESULT %s %s\\n' % (route['queue'], 'benchmark' in queue_names()))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")][-1]
    assert line == "RESULT benchmark True"


# ---------------------------------------------------------------------------
# PROMETHEUS_SCRAPE_TOKEN is documented as required on /metrics; the process
# /metrics (API layer: per-API-key usage series) ignored it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_metrics_require_scrape_token_when_set(monkeypatch):
    from app.api_layer_main import app as serving_app

    monkeypatch.setattr(settings, "PROMETHEUS_SCRAPE_TOKEN", "s3cret-scrape")
    async with AsyncClient(transport=ASGITransport(app=serving_app), base_url="http://api") as c:
        assert (await c.get("/metrics")).status_code == 401
        bad = await c.get("/metrics", headers={"authorization": "Bearer nope"})
        assert bad.status_code == 401
        ok = await c.get("/metrics", headers={"authorization": "Bearer s3cret-scrape"})
        assert ok.status_code == 200 and "http_requests_total" in ok.text
    monkeypatch.setattr(settings, "PROMETHEUS_SCRAPE_TOKEN", "")
    async with AsyncClient(transport=ASGITransport(app=serving_app), base_url="http://api") as c:
        assert (await c.get("/metrics")).status_code == 200


# ---------------------------------------------------------------------------
# Admin IP rules must also cover WebSocket handshakes (/api/v1/live/ws).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ip_rules_block_websocket_handshake(monkeypatch):
    from app.middlewares.ip_rules import IpRulesMiddleware

    async def deny(ip, scope):
        return type("Decision", (), {"allowed": False, "reason": "not_in_allowlist"})()

    monkeypatch.setattr("app.middlewares.ip_rules.check_ip", deny)
    reached, sent = [], []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    mw = IpRulesMiddleware(inner, "admin")
    scope = {
        "type": "websocket",
        "path": "/api/v1/live/ws",
        "headers": [],
        "client": ("6.6.6.6", 1),
    }
    await mw(scope, receive, send)
    assert reached == [] and sent and sent[0]["type"] == "websocket.close"
    # Exemptions are exact: a path merely ending in /metrics is still checked.
    http = {
        "type": "http",
        "path": "/api/v1/things/metrics",
        "headers": [],
        "method": "GET",
    }
    await mw({**http, "client": ("6.6.6.6", 1)}, receive, send)
    assert reached == []
    await mw({**http, "path": "/metrics", "client": ("6.6.6.6", 1)}, receive, send)
    assert reached == ["/metrics"]
