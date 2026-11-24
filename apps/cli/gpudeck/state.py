"""Per-invocation state shared between the root callback and commands (``ctx.obj``).

The root group needs the state before its callback runs (click resolves the subcommand first,
and the subcommand tree comes from the spec), so ``get_state`` builds it from the root context's
parsed parameters on first use.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from .config import Config, Profile
from .errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover
    import click

    from .http import Session
    from .spec import LoadedSpec

logger = logging.getLogger(__name__)

_LOGGING_READY = False


class State:
    def __init__(
        self,
        config: Config,
        profile_name: str | None,
        endpoint: str | None,
        fmt: str,
        fields: list[str] | None,
        insecure: bool,
        refresh_spec: bool = False,
        validate: bool = True,
    ) -> None:
        self.config = config
        self.profile_name = profile_name
        self.endpoint_override = endpoint
        self.fmt = fmt
        self.fields = fields
        self.insecure = insecure
        self.refresh_spec = refresh_spec
        self.validate = validate
        self._session: Session | None = None
        self._spec: LoadedSpec | None = None
        self._spec_fetch_allowed = False
        self._tree: dict[str, Any] | None = None

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "State":
        """Build from the root command's parsed options (missing ones take their defaults)."""
        from . import output

        global _LOGGING_READY
        fmt = params.get("fmt")
        if fmt is None:
            # piped output defaults to JSON so scripts get something parseable
            fmt = "table" if sys.stdout.isatty() else "json"
        if fmt not in output.FORMATS:
            raise ConfigError(f"--output must be one of {', '.join(output.FORMATS)}")
        if not _LOGGING_READY:
            logging.basicConfig(
                level=logging.DEBUG if params.get("verbose") else logging.WARNING,
                format="%(levelname)s %(name)s: %(message)s",
                stream=sys.stderr,
            )
            _LOGGING_READY = True
        return cls(
            config=Config.load(),
            profile_name=params.get("profile"),
            endpoint=params.get("endpoint"),
            fmt=fmt,
            fields=output.parse_fields(params.get("fields")),
            insecure=bool(params.get("insecure")),
            refresh_spec=bool(params.get("refresh_spec")),
            validate=not params.get("no_validate"),
        )

    def profile(self) -> Profile:
        prof = self.config.get(self.profile_name)
        if self.endpoint_override:
            prof.endpoint = self.endpoint_override
        if self.insecure:
            prof.verify_tls = False
        return prof

    def session(self) -> "Session":
        from .http import Session

        if self._session is None:
            prof = self.profile()
            self._session = Session.from_config(self.config, self.profile_name)
            self._session.profile = prof
        return self._session

    # ----- spec ---------------------------------------------------------------------------
    def spec(self, allow_fetch: bool = True) -> "LoadedSpec":
        """The OpenAPI spec for this run (loaded once; see gpudeck.spec for the sources)."""
        from . import spec as spec_mod

        current = self._spec
        # a spec loaded without network access (root --help) is upgraded once a command needs it
        settled = current is not None and (
            not allow_fetch
            or self._spec_fetch_allowed
            or current.source in ("live", "cache", "file")
        )
        if current is not None and settled:
            return current
        try:
            loaded = spec_mod.load(
                self.profile(), refresh=self.refresh_spec, allow_fetch=allow_fetch
            )
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot load the API spec: {exc}") from exc
        self._spec, self._spec_fetch_allowed, self._tree = loaded, allow_fetch, None
        logger.debug("spec: %s %s (%s)", loaded.source, loaded.version, loaded.location)
        return loaded

    def tree(self, allow_fetch: bool = True) -> dict[str, Any]:
        """Nested ``{group: {sub: {command: Operation}}}`` for the loaded spec."""
        from .registry import build_tree

        loaded = self.spec(allow_fetch=allow_fetch)
        if self._tree is None:
            self._tree = build_tree(loaded.spec)
        return self._tree


def get_state(ctx: "click.Context") -> State:
    root = ctx.find_root()
    if not isinstance(root.obj, State):
        root.obj = State.from_params(root.params or {})
    if ctx.obj is not root.obj:
        ctx.obj = root.obj
    return root.obj
