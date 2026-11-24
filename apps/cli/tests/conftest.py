"""Shared fixtures: isolated config and cache dirs, file-backed credentials, respx admin API."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import respx
from httpx import Response

from gpudeck import main as cli_main

ENDPOINT = "http://admin.test:8180"
BASE = ENDPOINT + "/api/v1"
TOKEN = "sess-token-abc123"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("GPUDECK_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("GPUDECK_CACHE_DIR", str(tmp_path / "cache"))
    # Commands come from the repository contract unless a test opts into the live spec.
    monkeypatch.setenv("GPUDECK_SPEC", "bundled")
    monkeypatch.delenv("GPUDECK_SPEC_TTL", raising=False)
    monkeypatch.setenv("GPUDECK_NO_KEYRING", "1")
    monkeypatch.delenv("GPUDECK_TOKEN", raising=False)
    monkeypatch.delenv("GPUDECK_ENDPOINT", raising=False)
    monkeypatch.delenv("GPUDECK_PROFILE", raising=False)
    monkeypatch.setenv("GPUDECK_OUTPUT", "json")
    monkeypatch.setenv("COLUMNS", "200")
    return cfg


@pytest.fixture
def logged_in(isolated_config: Path) -> Path:
    """Write a profile + credentials as if `gpudeck login` had run (bearer mode)."""
    isolated_config.mkdir(parents=True, exist_ok=True)
    (isolated_config / "config.toml").write_text(
        'current_profile = "default"\n\n[profiles.default]\n'
        f'endpoint = "{ENDPOINT}"\nauth_mode = "bearer"\nverify_tls = true\nca_bundle = ""\n'
        "use_keyring = false\ntimeout_seconds = 30.0\n"
    )
    (isolated_config / "credentials.json").write_text(
        json.dumps(
            {"default": {"token": TOKEN, "cookie_name": "gpudeck_session", "csrf_token": "csrf-1"}}
        )
    )
    return isolated_config


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


def envelope(data, meta=None) -> Response:
    return Response(200, json={"success": True, "data": data, "error": None, "meta": meta})


def error(status: int, code: str, message: str = "boom") -> Response:
    return Response(
        status,
        json={
            "success": False,
            "data": None,
            "error": {"code": code, "message": message},
            "meta": None,
        },
    )


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run(argv: list[str], capsys) -> tuple[int, str, str]:
    code = cli_main.main(argv)
    out, err = capsys.readouterr()
    # Typer forces a colour terminal for help on CI (GITHUB_ACTIONS, FORCE_COLOR); compare text.
    return code, ANSI.sub("", out), ANSI.sub("", err)


def run_json(argv: list[str], capsys):
    code, out, err = run(argv, capsys)
    assert code == 0, f"exit {code}: {err}\n{out}"
    return json.loads(out)
