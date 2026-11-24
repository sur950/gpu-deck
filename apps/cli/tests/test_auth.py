"""Login flow, session storage, Bearer header, cookie + CSRF fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import respx
from httpx import Response

from gpudeck.auth import Credentials, CredentialStore
from gpudeck.config import Config
from gpudeck.errors import EXIT_AUTH

from .conftest import BASE, ENDPOINT, TOKEN, envelope, error, run, run_json

LOGIN_BODY = {
    "session_id": "4b0a1e1e-0000-4000-8000-000000000001",
    "expires_at": "2026-11-17T12:00:00Z",
    "expires_in": 43200,
    "csrf_token": "csrf-xyz",
    "user": {"id": "4b0a1e1e-0000-4000-8000-000000000002", "username": "admin"},
}


def test_login_captures_session_cookie_and_stores_it(
    api, isolated_config: Path, capsys, monkeypatch
):
    route = api.post("/auth/login").mock(
        return_value=Response(
            200,
            json={"success": True, "data": LOGIN_BODY, "error": None, "meta": None},
            headers=[
                ("set-cookie", f"gpudeck_session={TOKEN}; Path=/; HttpOnly"),
                ("set-cookie", "gpudeck_csrf=csrf-xyz; Path=/"),
            ],
        )
    )
    monkeypatch.setattr("sys.stdin", _Stdin("s3cret\n"))
    code, out, err = run(
        ["login", "--endpoint", ENDPOINT, "--username", "admin", "--password-stdin"], capsys
    )
    assert code == 0, err
    assert route.called
    assert json.loads(route.calls[0].request.content) == {"username": "admin", "password": "s3cret"}
    assert "logged in" in out and TOKEN not in out  # never echo the token
    creds_file = isolated_config / "credentials.json"
    assert creds_file.exists()
    assert oct(creds_file.stat().st_mode & 0o777) == "0o600"
    stored = json.loads(creds_file.read_text())["default"]
    assert stored["token"] == TOKEN and stored["csrf_token"] == "csrf-xyz"
    cfg = Config.load()
    assert cfg.profiles["default"].endpoint == ENDPOINT


def test_login_accepts_any_session_cookie_name(api, capsys, monkeypatch):
    api.post("/auth/login").mock(
        return_value=Response(
            200,
            json={"success": True, "data": LOGIN_BODY, "error": None, "meta": None},
            headers={"set-cookie": "gpu_deck_session=tok-2; Path=/"},
        )
    )
    monkeypatch.setattr("sys.stdin", _Stdin("pw\n"))
    code, _, err = run(["login", "-e", ENDPOINT, "-u", "admin", "--password-stdin"], capsys)
    assert code == 0, err
    creds = CredentialStore(use_keyring=False).load("default")
    assert creds and creds.token == "tok-2" and creds.cookie_name == "gpu_deck_session"


def test_login_failure_maps_to_auth_exit_code(api, capsys, monkeypatch):
    api.post("/auth/login").mock(return_value=error(401, "INVALID_CREDENTIALS", "bad password"))
    monkeypatch.setattr("sys.stdin", _Stdin("pw\n"))
    code, _, err = run(["login", "-e", ENDPOINT, "-u", "admin", "--password-stdin"], capsys)
    assert code == EXIT_AUTH
    assert "INVALID_CREDENTIALS" in err


def test_bearer_header_is_sent(api, logged_in, capsys):
    route = api.get("/auth/me").mock(return_value=envelope({"id": "x", "username": "admin"}))
    data = run_json(["auth", "whoami"], capsys)
    assert data["username"] == "admin"
    req = route.calls[0].request
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert "x-csrf-token" not in req.headers
    assert "cookie" not in req.headers


def test_cookie_fallback_adds_csrf_on_mutations(api, logged_in, capsys):
    """401 on Bearer -> retry with the session cookie + X-CSRF-Token, remember cookie mode."""
    calls = []

    def handler(request):
        calls.append(request)
        if "authorization" in request.headers:
            return error(401, "AUTHENTICATION_ERROR", "bearer not accepted")
        assert request.headers["cookie"] == f"gpudeck_session={TOKEN}"
        assert request.headers["x-csrf-token"] == "csrf-1"
        return envelope({"id": "c1", "name": "lab", "type": "bare_metal", "status": "active"})

    api.post("/clusters").mock(side_effect=handler)
    data = run_json(["clusters", "create", "--name", "lab", "--type", "bare_metal"], capsys)
    assert data["id"] == "c1"
    assert len(calls) == 2
    assert Config.load().profiles["default"].auth_mode == "cookie"


def test_cookie_mode_fetches_csrf_when_missing(api, logged_in: Path, capsys):
    creds = json.loads((logged_in / "credentials.json").read_text())
    creds["default"]["csrf_token"] = None
    (logged_in / "credentials.json").write_text(json.dumps(creds))
    cfg = Config.load()
    cfg.profiles["default"].auth_mode = "cookie"
    cfg.save()
    api.get("/auth/csrf").mock(
        return_value=envelope(
            {"csrf_token": "fresh", "header_name": "X-CSRF-Token", "cookie_name": "gpudeck_csrf"}
        )
    )
    route = api.post("/api-keys/11111111-1111-4111-8111-111111111111/revoke").mock(
        return_value=envelope({"id": "11111111-1111-4111-8111-111111111111", "status": "revoked"})
    )
    data = run_json(["api-keys", "revoke", "11111111-1111-4111-8111-111111111111"], capsys)
    assert data["status"] == "revoked"
    assert route.calls[0].request.headers["x-csrf-token"] == "fresh"


def test_env_token_overrides_store(api, logged_in, capsys, monkeypatch):
    monkeypatch.setenv("GPUDECK_TOKEN", "env-token")
    route = api.get("/auth/me").mock(return_value=envelope({"id": "x", "username": "admin"}))
    run_json(["auth", "whoami"], capsys)
    assert route.calls[0].request.headers["authorization"] == "Bearer env-token"


def test_not_logged_in(api, capsys):
    code, _, err = run(["-e", ENDPOINT, "auth", "whoami"], capsys)
    assert code == EXIT_AUTH and "not logged in" in err


def test_logout_revokes_and_forgets(api, logged_in: Path, capsys):
    api.post("/auth/logout").mock(return_value=Response(204))
    code, out, _ = run(["logout"], capsys)
    assert code == 0 and "logged out" in out
    assert "default" not in json.loads((logged_in / "credentials.json").read_text())


def test_credentials_roundtrip_file_store(isolated_config: Path):
    store = CredentialStore(use_keyring=False)
    assert store.load("p") is None
    assert store.save("p", Credentials(token="t", username="u")) == "file"
    loaded = store.load("p")
    assert loaded and loaded.token == "t" and loaded.username == "u"
    store.delete("p")
    assert store.load("p") is None
    assert os.path.exists(isolated_config / "credentials.json")


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def readline(self) -> str:
        return self._text

    def read(self) -> str:
        return self._text


@pytest.mark.parametrize("url", [ENDPOINT, ENDPOINT + "/", BASE])
def test_base_url_normalisation(url):
    from gpudeck.config import Profile

    assert Profile(endpoint=url).base_url == BASE


def test_endpoint_override_is_not_persisted(api, logged_in, capsys, monkeypatch):
    """-e / GPUDECK_ENDPOINT apply to one run; a later config save must not store them."""
    other = "http://other.test:9000"
    api.get("/auth/me").mock(return_value=envelope({"id": "x", "username": "admin"}))
    with respx.mock(base_url=other + "/api/v1", assert_all_called=False) as other_api:
        route = other_api.get("/auth/me").mock(
            return_value=envelope({"id": "y", "username": "ops"})
        )
        monkeypatch.setenv("GPUDECK_ENDPOINT", other)
        assert run_json(["auth", "whoami"], capsys)["username"] == "ops"
        assert route.called
    # a save triggered by another command keeps the stored endpoint
    assert run(["config", "set", "default", "--timeout", "10"], capsys)[0] == 0
    assert Config.load().profiles["default"].endpoint == ENDPOINT


@pytest.mark.parametrize(
    ("endpoint", "cleartext"),
    [
        ("http://localhost:8180", False),
        ("http://127.0.0.1:8180", False),
        ("http://[::1]:8180", False),
        ("https://gpudeck.example.com", False),
        ("http://gpudeck.example.com", True),
        ("http://10.0.0.5:8180", True),
    ],
)
def test_cleartext_endpoint_detection(endpoint, cleartext):
    from gpudeck.commands.auth import cleartext_endpoint

    assert cleartext_endpoint(endpoint) is cleartext


def test_login_warns_about_argv_password_and_cleartext(isolated_config, capsys):
    remote = "http://gpudeck.example.com"
    with respx.mock(base_url=remote + "/api/v1") as router:
        route = router.post("/auth/login").mock(
            return_value=Response(
                200,
                json={"success": True, "data": LOGIN_BODY, "error": None, "meta": None},
                headers={"set-cookie": f"gpudeck_session={TOKEN}; Path=/"},
            )
        )
        code, out, err = run(["login", "-e", remote, "-u", "admin", "--password", "pw"], capsys)
    assert code == 0, err
    assert route.called
    assert "--password leaves the password in shell history" in err
    assert "plain HTTP" in err
    assert TOKEN not in out
