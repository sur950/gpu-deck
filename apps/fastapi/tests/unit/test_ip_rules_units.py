"""
Unit tests: IP rule semantics (block wins; any allow rule switches the scope to
allow-list mode), spoof-resistant client IP resolution, and the IP rules middleware
(checker mocked).
"""

from datetime import datetime, timedelta, timezone

import pytest
from app.core.settings import settings
from app.middlewares import ip_rules as ip_mw
from app.services.security import ip_rules
from app.services.security.client_ip import resolve_client_ip
from app.services.security.ip_rules import IpDecision, compile_rules, evaluate
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

pytestmark = [pytest.mark.unit, pytest.mark.middleware]


def rule(action, cidr, enabled=True, expires_at=None, rid="r"):
    return {
        "id": rid,
        "action": action,
        "cidr": cidr,
        "enabled": enabled,
        "expires_at": expires_at,
    }


def decide(ip, rules, env_allow=(), env_block=(), scope="admin"):
    return evaluate(ip, scope, compile_rules(list(rules), list(env_allow), list(env_block)))


# (ip, rules, env_allow, expected_allowed, expected_reason)
MATRIX = [
    ("10.0.0.5", [], [], True, "no_rules"),
    ("10.0.0.5", [rule("block", "10.0.0.0/8")], [], False, "blocked_by_rule"),
    ("11.0.0.5", [rule("block", "10.0.0.0/8")], [], True, "no_rules"),
    ("10.0.0.5", [rule("allow", "10.0.0.0/24")], [], True, "allowed_by_rule"),
    ("10.0.1.5", [rule("allow", "10.0.0.0/24")], [], False, "not_in_allowlist"),
    # block wins over an overlapping allow
    (
        "10.0.0.5",
        [rule("allow", "10.0.0.0/8"), rule("block", "10.0.0.5/32")],
        [],
        False,
        "blocked_by_rule",
    ),
    # env allowlist alone switches the scope into allow-list mode
    ("192.168.1.9", [], ["192.168.1.0/24"], True, "env_allowlist"),
    ("192.168.2.9", [], ["192.168.1.0/24"], False, "not_in_allowlist"),
    # env allow + db allow are merged
    (
        "172.16.0.1",
        [rule("allow", "172.16.0.0/16")],
        ["192.168.1.0/24"],
        True,
        "allowed_by_rule",
    ),
    # block still beats an env allow
    (
        "192.168.1.9",
        [rule("block", "192.168.1.9")],
        ["192.168.1.0/24"],
        False,
        "blocked_by_rule",
    ),
    # disabled rules are ignored
    ("10.0.0.5", [rule("block", "10.0.0.0/8", enabled=False)], [], True, "no_rules"),
    # IPv6
    ("2001:db8::1", [rule("block", "2001:db8::/32")], [], False, "blocked_by_rule"),
    ("2001:db9::1", [rule("allow", "2001:db8::/32")], [], False, "not_in_allowlist"),
    # IPv4 address never matches an IPv6 network and vice versa
    ("10.0.0.1", [rule("block", "::/0")], [], True, "no_rules"),
    # garbage address in allow-list mode is denied, in open mode allowed
    ("not-an-ip", [rule("allow", "10.0.0.0/8")], [], False, "not_in_allowlist"),
    ("", [], [], True, "no_rules"),
]


@pytest.mark.parametrize("ip,rules,env_allow,allowed,reason", MATRIX)
def test_ip_rule_matrix(ip, rules, env_allow, allowed, reason):
    decision = decide(ip, rules, env_allow)
    assert (decision.allowed, decision.reason) == (allowed, reason)


def test_expired_rules_are_ignored():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert decide("10.0.0.5", [rule("block", "10.0.0.0/8", expires_at=past)]).allowed
    assert not decide("10.0.0.5", [rule("block", "10.0.0.0/8", expires_at=future)]).allowed


def test_matched_rule_is_reported():
    decision = decide("10.0.0.5", [rule("block", "10.0.0.0/8", rid="abc")])
    assert decision.matched_rule["id"] == "abc"


def test_env_lists_per_scope(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_IP_ALLOWLIST", "1.2.3.4, 5.6.7.0/24")
    monkeypatch.setattr(settings, "IP_ALLOWLIST", "9.9.9.9")
    monkeypatch.setattr(settings, "IP_BLOCKLIST", "8.8.8.8")
    assert ip_rules.env_lists_for("api_layer") == (["1.2.3.4", "5.6.7.0/24"], [])
    assert ip_rules.env_lists_for("admin") == (["9.9.9.9"], ["8.8.8.8"])


@pytest.mark.asyncio
async def test_checker_uses_local_snapshot_and_bool_helper(monkeypatch):
    loads = []

    async def fake_read(scope):
        loads.append(scope)
        return [rule("block", "203.0.113.0/24")]

    checker = ip_rules.IpRuleChecker()
    monkeypatch.setattr(checker, "_read_snapshot", fake_read)
    monkeypatch.setattr(ip_rules, "checker", checker)
    monkeypatch.setattr(settings, "GATEWAY_IP_ALLOWLIST", "")
    assert await ip_rules.check_ip_allowed("203.0.113.7", "api_layer") is False
    assert await ip_rules.check_ip_allowed("198.51.100.1", "api_layer") is True
    assert loads == ["api_layer"]  # second call served from the process-local copy
    with pytest.raises(ValueError):
        await checker.check("1.1.1.1", "nope")


# ---------------------------------------------------------------------------
# Client IP resolution
# ---------------------------------------------------------------------------


def _scope(peer, **headers):
    return {
        "type": "http",
        "client": (peer, 1234) if peer else None,
        "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()],
    }


def test_client_ip_ignores_forwarded_headers_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1/32")
    assert resolve_client_ip(_scope("198.51.100.7", x_forwarded_for="10.0.0.1")) == "198.51.100.7"


def test_client_ip_walks_forwarded_chain_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1/32,10.1.0.0/16")
    scope = _scope("127.0.0.1", x_forwarded_for="6.6.6.6, 203.0.113.9, 10.1.2.3")
    # Right-most non-proxy hop wins: a client-supplied 6.6.6.6 cannot spoof.
    assert resolve_client_ip(scope) == "203.0.113.9"
    assert resolve_client_ip(_scope("127.0.0.1", x_real_ip="203.0.113.10")) == "203.0.113.10"
    assert resolve_client_ip(_scope("127.0.0.1")) == "127.0.0.1"
    assert resolve_client_ip(_scope(None)) == ""


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def _mw_app(process):
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/api/v1/things", ok),
            Route("/api/v1/health/live", ok),
            Route("/v1/chat/completions", ok, methods=["POST"]),
        ]
    )
    app.add_middleware(ip_mw.IpRulesMiddleware, process=process)
    return app


@pytest.fixture
def deny_all(monkeypatch):
    seen = []

    async def fake_check(ip, scope):
        seen.append((ip, scope))
        return IpDecision(ip, scope, False, "blocked_by_rule", None)

    monkeypatch.setattr(ip_mw, "check_ip", fake_check)
    return seen


@pytest.mark.asyncio
async def test_middleware_blocks_admin_with_envelope(deny_all):
    async with AsyncClient(transport=ASGITransport(app=_mw_app("admin")), base_url="http://t") as c:
        r = await c.get("/api/v1/things")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "IP_BLOCKED"
        live = await c.get("/api/v1/health/live")
        assert live.status_code == 200
    assert deny_all == [("127.0.0.1", "admin")]


@pytest.mark.asyncio
async def test_middleware_blocks_serving_in_openai_shape(deny_all):
    async with AsyncClient(
        transport=ASGITransport(app=_mw_app("serving")), base_url="http://t"
    ) as c:
        r = await c.post("/v1/chat/completions", json={})
    assert r.status_code == 403
    assert "error" in r.json() and "success" not in r.json()
    assert deny_all[0][1] == "api_layer"


@pytest.mark.asyncio
async def test_middleware_fails_open_on_checker_error(monkeypatch):
    async def boom(ip, scope):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ip_mw, "check_ip", boom)
    async with AsyncClient(transport=ASGITransport(app=_mw_app("admin")), base_url="http://t") as c:
        assert (await c.get("/api/v1/things")).status_code == 200
