"""`gpudeck` entry point: root Typer app, global options, error handling.

Global options (before the command): ``--profile``, ``--endpoint``, ``--output/-o``,
``--fields``, ``--insecure``, ``--verbose``, ``--refresh-spec``, ``--no-validate``. Every admin API
operation in the server's OpenAPI document is a command (see gpudeck.registry and gpudeck.spec).
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import typer

from . import __version__, output
from .commands import REGISTRARS
from .errors import EXIT_USAGE, GpudeckError
from .registry import SpecGroup
from .state import get_state

logger = logging.getLogger(__name__)

app = typer.Typer(
    cls=SpecGroup,
    name="gpudeck",
    help="GPU Deck control plane CLI. Run `gpudeck login` first.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
)
GROUPS: dict[str, typer.Typer] = {}


def _version(value: bool) -> None:
    if value:
        output.console.print(f"gpudeck {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(  # noqa: UP007
        None, "--profile", "-P", envvar="GPUDECK_PROFILE", help="Config profile"
    ),
    endpoint: Optional[str] = typer.Option(  # noqa: UP007
        None, "--endpoint", "-e", envvar="GPUDECK_ENDPOINT", help="Admin API endpoint override"
    ),
    fmt: Optional[str] = typer.Option(  # noqa: UP007
        None,
        "--output",
        "-o",
        envvar="GPUDECK_OUTPUT",
        help="table | json | yaml | csv (default: table on a terminal, json when piped)",
    ),
    fields: Optional[str] = typer.Option(  # noqa: UP007
        None, "--fields", help="Comma-separated columns/keys to show (dotted paths allowed)"
    ),
    insecure: bool = typer.Option(
        False, "--insecure", "-k", envvar="GPUDECK_INSECURE", help="Skip TLS verification"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging to stderr"),
    refresh_spec: bool = typer.Option(
        False, "--refresh-spec", help="Re-download the server's OpenAPI spec (ignore the cache)"
    ),
    no_validate: bool = typer.Option(
        False, "--no-validate", help="Do not validate request bodies against the spec"
    ),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    # The state may already exist: resolving the subcommand (which needs the spec) runs before
    # this callback.
    get_state(ctx)


def _build() -> None:
    for reg in REGISTRARS:
        reg(app, GROUPS)


_build()


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and translate GpudeckError into exit codes (used by tests and the script)."""
    try:
        app(args=argv, standalone_mode=False)
    except GpudeckError as exc:
        output.print_error(exc.message)
        details = getattr(exc, "details", None)
        if details:
            output.err_console.print(output.to_plain(details))
        return exc.exit_code
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except typer.Abort:
        return EXIT_USAGE
    except (typer.BadParameter, Exception) as exc:  # click.UsageError subclasses
        import click

        if isinstance(exc, click.ClickException):
            exc.show()
            return exc.exit_code
        raise
    return 0


def run() -> None:  # console_scripts entry
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
