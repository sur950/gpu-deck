"""`gpudeck login` / `logout`: session capture and credential storage."""

from __future__ import annotations

import ipaddress
import logging
import sys
from typing import Optional
from urllib.parse import urlsplit

import typer

from .. import output
from ..auth import CredentialStore
from ..config import Profile
from ..errors import ApiError, ConfigError
from ..http import Session, login, prompt_password

logger = logging.getLogger(__name__)


def cleartext_endpoint(endpoint: str) -> bool:
    """True when the endpoint is plain http:// to a host other than this machine."""
    parts = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower()
    if host in ("localhost", "") or host.endswith(".localhost"):
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def register(root: typer.Typer, groups: dict[str, typer.Typer]) -> None:
    from ..registry import ensure_group

    auth_app = ensure_group(root, groups, ["auth"])

    def login_cmd(
        ctx: typer.Context,
        endpoint: Optional[str] = typer.Option(  # noqa: UP007
            None, "--endpoint", "-e", help="Admin API endpoint, e.g. http://localhost:8180"
        ),
        username: Optional[str] = typer.Option(
            None, "--username", "-u", prompt=True
        ),  # noqa: UP007
        password: Optional[str] = typer.Option(  # noqa: UP007
            None, "--password", "-p", help="Prefer --password-stdin or the prompt", hide_input=True
        ),
        password_stdin: bool = typer.Option(
            False, "--password-stdin", help="Read password from stdin"
        ),
        profile: Optional[str] = typer.Option(
            None, "--save-as", help="Profile name to save (default: current)"
        ),  # noqa: UP007
        no_keyring: bool = typer.Option(
            False, "--no-keyring", help="Store the session in a 0600 file"
        ),
        ca_bundle: Optional[str] = typer.Option(
            None, "--ca-bundle", help="PEM bundle for TLS verification"
        ),  # noqa: UP007
    ) -> None:
        """Log in and store the session token for this profile."""
        state = ctx.obj
        current = state.profile()
        name = profile or state.profile_name or state.config.current_profile
        prof = Profile(
            name=name,
            endpoint=endpoint or state.endpoint_override or current.endpoint,
            auth_mode="bearer",
            verify_tls=current.verify_tls,
            ca_bundle=ca_bundle or current.ca_bundle,
            use_keyring=current.use_keyring and not no_keyring,
            timeout_seconds=current.timeout_seconds,
        )
        if password is not None and not password_stdin:
            output.err_console.print(
                "warning: --password leaves the password in shell history and the process list; "
                "prefer the prompt or --password-stdin"
            )
        if cleartext_endpoint(prof.endpoint):
            output.err_console.print(
                f"warning: {prof.endpoint} is plain HTTP; the password and session token cross "
                "the network unencrypted"
            )
        if password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        if password is None:
            password = prompt_password()
        if not username or not password:
            raise ConfigError("username and password are required")
        creds = login(prof, username, password)
        store = CredentialStore(use_keyring=prof.use_keyring)
        where = store.save(prof.name, creds)
        state.config.upsert(prof, make_current=True)
        state.config.save()
        msg = (
            f"logged in to {prof.endpoint} as {creds.username} "
            f"(profile '{prof.name}', session in {where})"
        )
        if creds.expires_at:
            msg += f", expires {creds.expires_at}"
        output.console.print(msg)

    def logout_cmd(ctx: typer.Context) -> None:
        """Revoke the current session on the server and forget the stored token."""
        state = ctx.obj
        session: Session = state.session()
        prof = session.profile
        try:
            if session.creds:
                session.request("POST", "/auth/logout")
        except ApiError as exc:
            if exc.status not in (401, 403):
                raise
        if session.store:
            session.store.delete(prof.name)
        output.console.print(f"logged out of profile '{prof.name}'")

    # `gpudeck login` at the root is the common spelling; `gpudeck auth login` also works.
    root.command(name="login", help="Log in and store the session token")(login_cmd)
    root.command(name="logout", help="Log out and forget the session")(logout_cmd)
    auth_app.command(name="login", help="Log in and store the session token")(login_cmd)
    auth_app.command(name="logout", help="Log out and forget the session")(logout_cmd)
