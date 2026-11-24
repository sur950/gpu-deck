"""`gpudeck config`: profiles (endpoint, TLS, auth mode) stored in config.toml."""

from __future__ import annotations

import logging
from typing import Optional

import typer

from .. import output
from ..config import Profile, config_path
from ..errors import ConfigError

logger = logging.getLogger(__name__)


def register(root: typer.Typer, groups: dict[str, typer.Typer]) -> None:
    app = typer.Typer(help="CLI profiles and settings", no_args_is_help=True)
    root.add_typer(app, name="config")
    groups["config"] = app

    @app.command("list")
    def list_profiles(ctx: typer.Context) -> None:
        """List configured profiles."""
        state = ctx.obj
        rows = [
            {
                "name": n,
                "current": n == state.config.current_profile,
                "endpoint": p.endpoint,
                "auth_mode": p.auth_mode,
                "verify_tls": p.verify_tls,
                "use_keyring": p.use_keyring,
            }
            for n, p in state.config.profiles.items()
        ]
        output.render(rows, fmt=state.fmt, fields=state.fields)

    @app.command("show")
    def show(ctx: typer.Context, name: Optional[str] = typer.Argument(None)) -> None:  # noqa: UP007
        """Show one profile (default: current) including environment overrides."""
        state = ctx.obj
        prof = state.config.get(name or state.profile_name)
        data = {
            "name": prof.name,
            "endpoint": prof.endpoint,
            "base_url": prof.base_url,
            "auth_mode": prof.auth_mode,
            "verify_tls": prof.verify_tls,
            "ca_bundle": prof.ca_bundle or None,
            "use_keyring": prof.use_keyring,
            "timeout_seconds": prof.timeout_seconds,
            "config_file": str(config_path()),
        }
        output.render(data, fmt=state.fmt, fields=state.fields)

    @app.command("set")
    def set_profile(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Profile name"),
        endpoint: Optional[str] = typer.Option(None, "--endpoint", "-e"),  # noqa: UP007
        auth_mode: Optional[str] = typer.Option(
            None, "--auth-mode", help="bearer | cookie"
        ),  # noqa: UP007
        verify_tls: Optional[bool] = typer.Option(
            None, "--verify-tls/--no-verify-tls"
        ),  # noqa: UP007
        ca_bundle: Optional[str] = typer.Option(None, "--ca-bundle"),  # noqa: UP007
        use_keyring: Optional[bool] = typer.Option(None, "--keyring/--no-keyring"),  # noqa: UP007
        timeout: Optional[float] = typer.Option(None, "--timeout", help="Seconds"),  # noqa: UP007
        make_current: bool = typer.Option(
            True, "--use/--no-use", help="Make it the current profile"
        ),
    ) -> None:
        """Create or update a profile."""
        state = ctx.obj
        prof = state.config.profiles.get(name) or Profile(name=name)
        if endpoint:
            prof.endpoint = endpoint
        if auth_mode:
            if auth_mode not in ("bearer", "cookie"):
                raise ConfigError("--auth-mode must be bearer or cookie")
            prof.auth_mode = auth_mode
        if verify_tls is not None:
            prof.verify_tls = verify_tls
        if ca_bundle is not None:
            prof.ca_bundle = ca_bundle
        if use_keyring is not None:
            prof.use_keyring = use_keyring
        if timeout is not None:
            prof.timeout_seconds = timeout
        state.config.upsert(prof, make_current=make_current)
        state.config.save()
        output.console.print(f"profile '{name}' saved to {config_path()}")

    @app.command("use")
    def use(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
        """Switch the current profile."""
        state = ctx.obj
        if name not in state.config.profiles:
            raise ConfigError(
                f"profile '{name}' does not exist; create it with `gpudeck config set`"
            )
        state.config.current_profile = name
        state.config.save()
        output.console.print(f"current profile: {name}")

    @app.command("spec")
    def spec_info(ctx: typer.Context) -> None:
        """Show which OpenAPI spec the commands come from (server, cache or bundled)."""
        from ..spec import cache_dir

        state = ctx.obj
        loaded = state.spec()
        data = {
            "source": loaded.source,
            "version": loaded.version,
            "location": loaded.location,
            "server": loaded.server,
            "fetched_at": loaded.fetched_at,
            "operations": len(loaded.spec.operations()),
            "cache_dir": str(cache_dir()),
        }
        output.render(data, fmt=state.fmt, fields=state.fields)

    @app.command("delete")
    def delete(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
        """Delete a profile and its stored session."""
        from ..auth import CredentialStore

        state = ctx.obj
        prof = state.config.profiles.pop(name, None)
        if prof is None:
            raise ConfigError(f"profile '{name}' does not exist")
        CredentialStore(use_keyring=prof.use_keyring).delete(name)
        if state.config.current_profile == name:
            state.config.current_profile = next(iter(state.config.profiles), "default")
        state.config.save()
        output.console.print(f"profile '{name}' deleted")
