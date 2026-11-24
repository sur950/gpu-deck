"""
Regression tests for runtime issues found while running the full stack (host checks,
worker detection, UTC sessions, metric aliases, installers, node proxy errors).
"""

import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_host_check_exempts_probes_and_scrapes():
    from app.middlewares.core_middlewares import HostCheckMiddleware

    app = FastAPI()

    @app.get("/api/v1/health/live")
    def live():
        return {"ok": True}

    @app.get("/metrics")
    def metrics():
        return {"ok": True}

    @app.get("/api/v1/things")
    def things():
        return {"ok": True}

    app.add_middleware(HostCheckMiddleware, allowed_hosts=["admin.example.com"])
    client = TestClient(app, base_url="http://10.1.2.3:8180")
    assert client.get("/api/v1/health/live").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/v1/things").status_code == 400
    assert client.get("/api/v1/things", headers={"host": "admin.example.com"}).status_code == 200


def test_trusted_hosts_setting_extends_allow_list(monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "TRUSTED_HOSTS", "localhost, 127.0.0.1,probe.internal")
    hosts = settings.get_trusted_hosts()
    assert {"localhost", "127.0.0.1", "probe.internal"} <= set(hosts)


def test_scheduler_started_with_dash_m_counts_as_worker_process(monkeypatch):
    from app.db.postgres import connection

    monkeypatch.setattr(sys, "argv", ["/srv/gpu-deck/apps/fastapi/app/workers/scheduler.py"])
    assert connection._is_worker_process()
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    assert not connection._is_worker_process()


def test_db_sessions_run_in_utc():
    from app.db.postgres.connection import _server_settings

    assert _server_settings()["timezone"] == "UTC"


def test_usage_metric_aliases_keep_requested_name():
    from app.services.metrics.catalog import resolve

    out = resolve("usage", ["requests,tokens_input", "tokens_cached"], [])
    assert not out.unknown
    assert [s.name for s in out.specs] == ["requests", "tokens_input", "tokens_cached"]
    assert resolve("usage", ["bogus"], []).unknown == ["bogus"]


def test_config_digest_ignores_version_only():
    from app.services.agent_hub.connection import _config_digest

    a = {
        "config_version": 1,
        "full": True,
        "settings": {"proxy": {"request_timeout_seconds": 598}},
    }
    b = {**a, "config_version": 7}
    c = {**a, "settings": {"proxy": {"request_timeout_seconds": 43}}}
    assert _config_digest(a) == _config_digest(b) != _config_digest(c)


def test_macos_installer_supports_user_launch_agent():
    from app.services.nodes import agent_downloads as dl

    script = dl.install_script(
        "gdj_" + "ab" * 20,
        hub_url="https://hub:8443",
        ca_fingerprint="cd" * 32,
        name="mac",
    )
    assert 'GD_HOME="${GPUDECK_HOME:-$HOME/.gpudeck}"' in script
    assert '"$BIN" --config "$CONF" join $JOIN --state-dir "$STATE"' in script
    assert "<key>EnvironmentVariables</key>" in script
    checked = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr


def test_llama_auto_tune_variants_have_no_duplicates():
    from app.services.benchmark.stats import default_variants

    for levels in ([1, 2, 4], [1], [8, 16]):
        v = default_variants("llama_cpp", levels)
        assert len(v) == len({repr(sorted(x.items())) for x in v})


@pytest.mark.parametrize("code", ["engine_timeout", "engine_unavailable", "instance_not_found"])
def test_node_proxy_errors_are_mapped(code):
    from app.services.serving.pipeline import _PROXY_ERRORS

    status, our_code, _ = _PROXY_ERRORS[code]
    assert status >= 500 and our_code.startswith("upstream_")
