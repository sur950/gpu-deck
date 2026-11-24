"""HTTP session for the gpudeck CLI (plain httpx; requests are built from the OpenAPI spec).

* ``Session.request(method, path, ...)`` sends one admin API call with the profile's auth and
  unwraps the ``{success, data, error, meta}`` envelope into ``(data, meta)``; non-JSON bodies
  (CSV, PEM, Prometheus text) come back as ``str`` and binary downloads as ``bytes``.
* Auth: ``Authorization: Bearer <session token>`` or, in cookie mode, the session cookie plus the
  CSRF double-submit header on mutating requests. On a 401 in bearer mode with a cookie-born token
  the call is retried once in cookie mode and the profile remembers the mode that worked.
"""

from __future__ import annotations

import getpass
import logging
from typing import TYPE_CHECKING, Any, Iterator

from .auth import Credentials, CredentialStore
from .config import Config, Profile
from .errors import ApiError, ConnectionFailed, NotLoggedIn

if TYPE_CHECKING:  # pragma: no cover
    import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "gpudeck-cli"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
NO_BODY: Any = object()


def verify_value(profile: Profile) -> bool | str:
    if not profile.verify_tls:
        return False
    return profile.ca_bundle or True


def login(profile: Profile, username: str, password: str) -> Credentials:
    """POST /auth/login and capture the session cookie from Set-Cookie.

    The token is the value of the ``gpudeck_session`` cookie (fallback: any cookie whose name ends
    in ``_session``).
    """
    import httpx

    try:
        with httpx.Client(
            base_url=profile.base_url,
            verify=verify_value(profile),
            timeout=profile.timeout_seconds,
            headers={"User-Agent": USER_AGENT},
        ) as http:
            resp = http.post("/auth/login", json={"username": username, "password": password})
    except httpx.HTTPError as exc:
        raise ConnectionFailed(f"cannot reach {profile.base_url}: {exc}") from exc
    payload = _json_or_none(resp)
    if resp.status_code != 200 or not isinstance(payload, dict) or not payload.get("success"):
        raise ApiError.from_response(resp.status_code, payload, resp.text)
    data = payload.get("data") or {}
    cookie_name, token = _session_cookie(resp)
    if not token:
        raise ApiError(
            resp.status_code,
            "NO_SESSION_COOKIE",
            "login succeeded but no *_session cookie was set; cannot store a session token",
        )
    csrf_cookie = next((n for n in resp.cookies.keys() if n.endswith("_csrf")), "gpudeck_csrf")
    return Credentials(
        token=token,
        cookie_name=cookie_name,
        csrf_token=data.get("csrf_token") or resp.cookies.get(csrf_cookie),
        csrf_cookie_name=csrf_cookie,
        username=(data.get("user") or {}).get("username") or username,
        expires_at=data.get("expires_at"),
        session_id=data.get("session_id"),
    )


def _session_cookie(resp: "httpx.Response") -> tuple[str, str | None]:
    cookies = resp.cookies
    if "gpudeck_session" in cookies:
        return "gpudeck_session", cookies["gpudeck_session"]
    for name in cookies.keys():
        if name.endswith("_session"):
            return name, cookies[name]
    return "gpudeck_session", None


def _json_or_none(resp: "httpx.Response") -> Any:
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


class Session:
    """Authenticated access to the admin API for one profile."""

    def __init__(
        self,
        profile: Profile,
        creds: Credentials | None,
        config: Config | None = None,
        store: CredentialStore | None = None,
    ) -> None:
        self.profile = profile
        self.creds = creds
        self.config = config
        self.store = store
        self.auth_mode = (
            profile.auth_mode if profile.auth_mode in ("bearer", "cookie") else "bearer"
        )
        self._csrf: str | None = creds.csrf_token if creds else None
        self._http: httpx.Client | None = None

    # ----- construction -------------------------------------------------------------------
    @classmethod
    def from_config(cls, config: Config, profile_name: str | None = None) -> "Session":
        profile = config.get(profile_name)
        store = CredentialStore(use_keyring=profile.use_keyring)
        creds = store.load(profile.name)
        return cls(profile, creds, config, store)

    def require_auth(self) -> Credentials:
        if not self.creds or not self.creds.token:
            raise NotLoggedIn(
                f"not logged in to profile '{self.profile.name}'; run `gpudeck login` "
                "or set GPUDECK_TOKEN"
            )
        return self.creds

    def http(self) -> "httpx.Client":
        import httpx

        if self._http is None or str(self._http.base_url).rstrip("/") != self.profile.base_url:
            self._http = httpx.Client(
                base_url=self.profile.base_url,
                verify=verify_value(self.profile),
                timeout=httpx.Timeout(self.profile.timeout_seconds),
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    def auth_headers(self, method: str = "GET") -> dict[str, str]:
        creds = self.require_auth()
        if self.auth_mode == "cookie":
            headers = {"Cookie": f"{creds.cookie_name}={creds.token}"}
            if method.upper() in UNSAFE_METHODS:
                headers[creds.csrf_header_name] = self.csrf_token()
            return headers
        return {"Authorization": f"Bearer {creds.token}"}

    # ----- CSRF ---------------------------------------------------------------------------
    def csrf_token(self, refresh: bool = False) -> str:
        """Return the CSRF token for cookie mode, fetching it from GET /auth/csrf if needed."""
        import httpx

        if self._csrf and not refresh:
            return self._csrf
        creds = self.require_auth()
        try:
            resp = self.http().get(
                "/auth/csrf", headers={"Cookie": f"{creds.cookie_name}={creds.token}"}
            )
        except httpx.HTTPError as exc:
            raise ConnectionFailed(f"cannot reach {self.profile.base_url}: {exc}") from exc
        payload = _json_or_none(resp)
        if resp.status_code != 200 or not isinstance(payload, dict):
            raise ApiError.from_response(resp.status_code, payload, resp.text)
        data = payload.get("data") or {}
        self._csrf = str(data.get("csrf_token") or "")
        if data.get("header_name"):
            creds.csrf_header_name = str(data["header_name"])
        return self._csrf

    # ----- calls --------------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = NO_BODY,
        headers: dict[str, str] | None = None,
        auth: str = "required",
    ) -> tuple[Any, Any]:
        """Send one call and return ``(data, meta)``; raises ApiError on failure.

        ``auth``: ``required`` (fail fast when not logged in), ``public`` or ``optional`` (send the
        session when there is one; a 401 without one becomes "not logged in").
        """
        has_creds = bool(self.creds and self.creds.token)
        if auth == "required" and not has_creds:
            self.require_auth()
        try:
            return self._send(method, path, params, body, headers, has_creds)
        except ApiError as exc:
            if exc.status == 401 and not has_creds:
                raise NotLoggedIn(
                    f"not logged in to profile '{self.profile.name}'; run `gpudeck login` "
                    "or set GPUDECK_TOKEN"
                ) from exc
            if (
                exc.status == 401
                and has_creds
                and self.auth_mode == "bearer"
                and self.creds
                and self.creds.cookie_name
            ):
                logger.debug("bearer auth rejected; retrying in cookie mode")
                self.auth_mode = "cookie"
                result = self._send(method, path, params, body, headers, has_creds)
                self._remember_auth_mode()
                return result
            raise

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        body: Any,
        headers: dict[str, str] | None,
        with_auth: bool,
    ) -> tuple[Any, Any]:
        import httpx

        all_headers = dict(headers or {})
        if with_auth:
            all_headers.update(self.auth_headers(method))
        kwargs: dict[str, Any] = {"params": params or None, "headers": all_headers}
        if body is not NO_BODY:
            kwargs["json"] = body
        try:
            response = self.http().request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectionFailed(f"cannot reach {self.profile.base_url}: {exc}") from exc
        return unwrap(response.status_code, response.content, response.headers)

    def _remember_auth_mode(self) -> None:
        if self.config is None:
            return
        prof = self.config.profiles.get(self.profile.name)
        if prof is not None and prof.auth_mode != self.auth_mode:
            prof.auth_mode = self.auth_mode
            try:
                self.config.save()
            except OSError as exc:  # pragma: no cover
                logger.debug("could not persist auth mode: %s", exc)

    def stream_sse(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, str]]:
        """Yield parsed server-sent events ``{event, data, id}`` from a streaming GET."""
        import httpx

        headers = {"Accept": "text/event-stream", **self.auth_headers("GET")}
        try:
            with self.http().stream(
                "GET", path, params=params, headers=headers, timeout=None
            ) as resp:
                if resp.status_code != 200:
                    body = resp.read()
                    payload = None
                    try:
                        import json

                        payload = json.loads(body)
                    except ValueError:
                        pass
                    raise ApiError.from_response(
                        resp.status_code, payload, body.decode("utf-8", "replace")
                    )
                event: dict[str, str] = {}
                data_lines: list[str] = []
                for line in resp.iter_lines():
                    if line == "":
                        if data_lines or event:
                            event["data"] = "\n".join(data_lines)
                            yield event
                        event, data_lines = {}, []
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "data":
                        data_lines.append(value)
                    elif field in ("event", "id", "retry"):
                        event[field] = value
        except httpx.HTTPError as exc:
            raise ConnectionFailed(f"stream from {self.profile.base_url} failed: {exc}") from exc


BINARY_TYPES = {
    "application/octet-stream",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/zip",
}


def unwrap(status: int, content: bytes, headers: Any) -> tuple[Any, Any]:
    """Turn a raw response into ``(data, meta)`` or raise ApiError."""
    import json

    ctype = headers.get("content-type", "") if headers is not None else ""
    text = content.decode("utf-8", "replace") if content else ""
    payload: Any = None
    if "json" in ctype or (text[:1] in ("{", "[")):
        try:
            payload = json.loads(text) if text else None
        except ValueError:
            payload = None
    if status >= 400:
        raise ApiError.from_response(status, payload, text)
    if status == 204 or not content:
        return None, None
    if ctype.split(";")[0].strip() in BINARY_TYPES:
        return content, None
    if isinstance(payload, dict) and "success" in payload:
        if payload.get("success") is False:
            raise ApiError.from_response(status, payload, text)
        return payload.get("data"), payload.get("meta")
    if payload is not None:
        return payload, None
    return text, None


def prompt_password(prompt: str = "Password: ") -> str:
    return getpass.getpass(prompt)
