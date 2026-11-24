"""
Unit tests: audit event_type matching, rules next_evaluation_at, pure-ASGI
core middleware, cross-loop Redis close, committed generated models.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = [pytest.mark.unit]

FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _sql(cond) -> str:
    return str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_audit_event_type_matching():
    from app.db.postgres.repositories.audit_log_repository import event_type_condition as c

    assert _sql(c("rule.created")) == "audit_logs.event_type = 'rule.created'"
    bare = _sql(c("rule"))
    assert "= 'rule'" in bare and "LIKE 'rule.%%'" in bare
    assert "LIKE 'rule.%%'" in _sql(c("rule.")) and "=" not in _sql(c("rule."))
    assert "LIKE 'rule.%%'" in _sql(c("rule.*"))
    assert "LIKE '%%.failed'" in _sql(c("*.failed"))
    # LIKE metacharacters in user input are escaped.
    assert "api\\_key.%%" in _sql(c("api_key"))
    both = _sql(c("auth,swap."))
    assert "'auth'" in both and "'swap.%%'" in both


def _rule(**kw):
    base = dict(
        id=uuid.uuid4(),
        enabled=True,
        type="idle_unload",
        params={},
        last_evaluated_at=None,
        evaluation_interval_seconds=60,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_rule_next_evaluation_at():
    from app.services.rules.service import next_evaluation_at

    now = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
    assert next_evaluation_at(_rule(enabled=False), now) is None
    assert next_evaluation_at(_rule(), now) == now
    last = now - timedelta(seconds=20)
    assert next_evaluation_at(_rule(last_evaluated_at=last), now) == last + timedelta(seconds=60)
    overdue = now - timedelta(seconds=600)
    assert next_evaluation_at(_rule(last_evaluated_at=overdue), now) == now
    sched = _rule(type="time_schedule", params={"cron": "30 18 * * *", "timezone": "UTC"})
    assert next_evaluation_at(sched, now) == datetime(2026, 11, 17, 18, 30, tzinfo=timezone.utc)
    broken = _rule(type="time_schedule", params={"cron": "not a cron"})
    assert next_evaluation_at(broken, now) is None


async def _run_asgi(app, path="/x", headers=()):
    sent = []
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": list(headers),
        "client": ("10.0.0.9", 1234),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return scope, sent


@pytest.mark.asyncio
async def test_core_middleware_is_pure_asgi_and_sets_headers():
    from app.core.request_context import get_request_id
    from app.middlewares.core_middlewares import CoreHTTPMiddleware

    seen = {}

    async def inner(scope, receive, send):
        seen["rid"] = get_request_id()
        seen["state"] = dict(scope["state"])
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-frame-options", b"SAMEORIGIN")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    mw = CoreHTTPMiddleware(inner, process="serving")
    rid = str(uuid.uuid4())
    scope, sent = await _run_asgi(mw, headers=[(b"x-request-id", rid.encode())])
    headers = dict(sent[0]["headers"])
    assert headers[b"x-request-id"] == rid.encode()
    assert seen["rid"] == rid and seen["state"]["request_id"] == rid
    assert seen["state"]["client_ip"] == "10.0.0.9"
    assert headers[b"x-frame-options"] == b"SAMEORIGIN"  # app value kept (setdefault)
    assert headers[b"x-content-type-options"] == b"nosniff"

    # A non-UUID inbound id is replaced (request_id columns are uuid).
    _, sent = await _run_asgi(mw, headers=[(b"x-request-id", b"not-a-uuid")])
    new_id = dict(sent[0]["headers"])[b"x-request-id"].decode()
    assert new_id != "not-a-uuid" and uuid.UUID(new_id)


def test_serving_stack_has_no_base_http_middleware():
    from app.api_layer_main import app
    from starlette.middleware.base import BaseHTTPMiddleware

    for m in app.user_middleware:
        assert not (isinstance(m.cls, type) and issubclass(m.cls, BaseHTTPMiddleware)), m.cls


def test_close_redis_drops_client_from_closed_loop():
    from app.db.redis import client as mod

    class Dummy:
        closed = False

        async def aclose(self):  # pragma: no cover - must not be awaited
            Dummy.closed = True

    old_loop = asyncio.new_event_loop()
    old_loop.close()
    saved = (mod._client, mod._client_loop)
    mod._client, mod._client_loop = Dummy(), old_loop
    try:
        asyncio.run(mod.close_redis())
        assert mod._client is None and not Dummy.closed
    finally:
        mod._client, mod._client_loop = saved


def test_generated_models_committed_without_partitions():
    path = FASTAPI_ROOT / "app/db/postgres/models/generated_models.py"
    text = path.read_text()
    assert "_p2026" not in text and "class CloudInstances(Base)" in text
    assert "class Budgets(Base)" not in text  # budgets removed (product decision 2026-11-18)
    ignore = (FASTAPI_ROOT / ".gitignore").read_text()
    assert "!app/db/postgres/models/generated_models.py" in ignore


def test_env_docs_settings_and_examples_agree():
    """scripts/check_env_docs.py: env.md <-> settings <-> both .env.example files."""
    import subprocess
    import sys

    script = FASTAPI_ROOT.parents[1] / "scripts" / "check_env_docs.py"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


def test_alembic_migrations_form_one_linear_chain():
    """One head, no branches: `alembic upgrade head` must work everywhere."""
    from tests.schema_helpers import MIGRATIONS, _revision_info, migration_chain

    revs = {}
    for folder in MIGRATIONS.iterdir():
        if (folder / "migration.py").is_file():
            rev, down = _revision_info(folder / "migration.py")
            revs[rev] = down
    downs = [d for d in revs.values() if d is not None]
    heads = sorted(set(revs) - set(downs))
    assert len(heads) == 1, f"multiple alembic heads: {heads}"
    branched = sorted({d for d in downs if downs.count(d) > 1})
    assert not branched, f"migrations branch off {branched}; revise the current head instead"
    assert len(migration_chain()) == len(revs)
