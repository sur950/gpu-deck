"""The command tree, built at runtime from the admin API's OpenAPI document.

``COMMAND_MAP`` places every operation (keyed by its contract operationId in snake_case) in the
tree (``"group [subgroup] command"``). Groups are click groups that read the spec lazily
(``SpecGroup``): ``gpudeck --help`` never touches the network, and a command's parameters are
created only when that command is resolved. Each operation becomes a command with:

* path parameters as positional arguments,
* query (and header) parameters as ``--options`` with types, enum choices, defaults and
  required flags from the schema; arrays are repeatable,
* for JSON bodies: ``--data/-d`` (``@file``, ``-`` = stdin, or inline JSON), ``--file/-f``
  (JSON or YAML), repeatable ``--set key.path=value`` and one ``--<field>`` option for every
  simple top-level body field,
* ``--all`` on paginated lists to follow ``meta.has_more``.

Operations a newer server adds without a ``COMMAND_MAP`` entry still appear, under the group
named after their first tag. Hand-written commands (``gpudeck.commands``) live in the same groups
and take precedence; the operations they replace are listed in ``OVERRIDDEN``. The coverage test
(tests/test_coverage.py) asserts that every operation in admin.yaml is mapped and reachable.
"""

from __future__ import annotations

import datetime as dt
import logging
from difflib import get_close_matches
from typing import Any

import click
import typer
from typer.core import TyperArgument, TyperCommand, TyperGroup, TyperOption

from .errors import ConfigError
from .openapi import Operation, Spec, kebab, snake
from .state import get_state

logger = logging.getLogger(__name__)

# contract operationId (snake_case) -> "group [subgroup] command"
COMMAND_MAP: dict[str, str] = {
    # auth
    "login": "auth login",
    "logout": "auth logout",
    "get_me": "auth whoami",
    "get_csrf_token": "auth csrf",
    "change_password": "auth change-password",
    "list_sessions": "auth sessions list",
    "revoke_session": "auth sessions revoke",
    "logout_all": "auth logout-all",
    # api keys
    "list_api_keys": "api-keys list",
    "create_api_key": "api-keys create",
    "get_api_key_stats": "api-keys stats",
    "get_api_key": "api-keys get",
    "update_api_key": "api-keys update",
    "delete_api_key": "api-keys delete",
    "revoke_api_key": "api-keys revoke",
    "suspend_api_key": "api-keys suspend",
    "activate_api_key": "api-keys activate",
    "rotate_api_key": "api-keys rotate",
    # audit
    "list_audit_logs": "audit list",
    "export_audit_csv": "audit export",
    "get_audit_log": "audit get",
    # ip rules
    "list_ip_rules": "ip-rules list",
    "create_ip_rule": "ip-rules create",
    "check_ip": "ip-rules check",
    "get_ip_rule": "ip-rules get",
    "update_ip_rule": "ip-rules update",
    "delete_ip_rule": "ip-rules delete",
    # vault
    "list_credentials": "vault list",
    "create_credential": "vault create",
    "get_vault_metadata": "vault metadata",
    "get_vault_stats": "vault stats",
    "get_credential": "vault get",
    "update_credential": "vault update",
    "delete_credential": "vault delete",
    "reveal_credential": "vault reveal",
    "test_credential": "vault test",
    "get_credential_usage": "vault usage",
    # clusters
    "list_clusters": "clusters list",
    "create_cluster": "clusters create",
    "get_cluster": "clusters get",
    "update_cluster": "clusters update",
    "delete_cluster": "clusters delete",
    "list_cluster_nodes": "clusters nodes",
    # nodes
    "list_nodes": "nodes list",
    "list_join_tokens": "nodes join-tokens list",
    "create_join_token": "nodes join-tokens create",
    "revoke_join_token": "nodes join-tokens revoke",
    "get_node": "nodes get",
    "update_node": "nodes update",
    "remove_node": "nodes remove",
    "drain_node": "nodes drain",
    "undrain_node": "nodes undrain",
    "list_node_devices": "nodes devices",
    "list_node_instances": "nodes instances",
    "get_node_cache": "nodes cache",
    "list_node_commands": "nodes commands",
    "get_node_command": "nodes command",
    "scan_node_path": "nodes scan",
    "hash_node_files": "nodes hash",
    "list_node_certificates": "nodes certificates list",
    "rotate_node_certificate": "nodes certificates rotate",
    "revoke_node_certificate": "nodes certificates revoke",
    # devices
    "list_devices": "devices list",
    "get_device": "devices get",
    "list_device_slices": "devices slices",
    "list_all_device_slices": "devices all-slices",
    "create_device_slice": "devices slices-create",
    "update_device_slice": "devices slices-update",
    "delete_device_slice": "devices slices-delete",
    # topology
    "get_topology": "topology show",
    # catalog + models
    "search_catalog": "catalog search",
    "list_remote_files": "catalog remote-files",
    "scan_local_path": "catalog scan",
    "list_models": "models list",
    "register_model": "models register",
    "get_model": "models get",
    "update_model": "models update",
    "delete_model": "models delete",
    "list_model_versions": "models versions list",
    "add_model_version": "models versions add",
    "get_model_version": "models versions get",
    "update_model_version": "models versions update",
    "delete_model_version": "models versions delete",
    "list_model_files": "models versions files",
    "verify_model_version": "models versions verify",
    "list_model_version_cache": "models versions cache",
    # engines
    "list_engines": "engines list",
    "get_engine": "engines get",
    "update_engine": "engines update",
    "list_engine_versions": "engines versions list",
    "create_engine_version": "engines versions create",
    "get_engine_version": "engines versions get",
    "update_engine_version": "engines versions update",
    "delete_engine_version": "engines versions delete",
    "run_compat_check": "engines compat-check",
    "list_compat_checks": "engines compat-checks",
    "auto_pick_engine": "engines auto-pick",
    # deployments
    "list_deployments": "deployments list",
    "create_deployment": "deployments create",
    "preview_placement": "deployments preview",
    "get_deployment": "deployments get",
    "update_deployment": "deployments update",
    "delete_deployment": "deployments delete",
    "scale_deployment": "deployments scale",
    "restart_deployment": "deployments restart",
    "stop_deployment": "deployments stop",
    "start_deployment": "deployments start",
    "set_traffic_weights": "deployments traffic",
    "list_deployment_instances": "deployments instances",
    "list_deployment_swaps": "deployments swaps",
    # instances
    "list_instances": "instances list",
    "get_instance": "instances get",
    "update_instance": "instances update",
    "stop_instance": "instances stop",
    "drain_instance": "instances drain",
    "restart_instance": "instances restart",
    "get_instance_sessions": "instances sessions",
    # model cache
    "list_cache_items": "cache list",
    "get_cache_summary": "cache summary",
    "prefetch_model": "cache prefetch",
    "evict_cache": "cache evict",
    "delete_cache_item": "cache delete",
    "pin_cache_item": "cache pin",
    "unpin_cache_item": "cache unpin",
    # layouts
    "list_layouts": "layouts list",
    "create_layout": "layouts create",
    "capture_layout": "layouts capture",
    "get_layout": "layouts get",
    "update_layout": "layouts update",
    "delete_layout": "layouts delete",
    "preview_layout": "layouts preview",
    "apply_layout": "layouts apply",
    # rules
    "list_rules": "rules list",
    "create_rule": "rules create",
    "get_rule_schemas": "rules schemas",
    "get_rule": "rules get",
    "update_rule": "rules update",
    "delete_rule": "rules delete",
    "enable_rule": "rules enable",
    "disable_rule": "rules disable",
    "dry_run_rule": "rules dry-run",
    "run_rule_now": "rules run",
    "list_rule_runs": "rules runs",
    "list_all_rule_runs": "rule-runs list",
    "get_rule_run": "rule-runs get",
    # swaps
    "list_swaps": "swaps list",
    "create_swap": "swaps create",
    "get_swap": "swaps get",
    "cancel_swap": "swaps cancel",
    "retry_swap": "swaps retry",
    # alerts
    "list_alert_channels": "alerts channels list",
    "create_alert_channel": "alerts channels create",
    "get_alert_channel": "alerts channels get",
    "update_alert_channel": "alerts channels update",
    "delete_alert_channel": "alerts channels delete",
    "test_alert_channel": "alerts channels test",
    "list_alert_events": "alerts events list",
    "get_alert_event": "alerts events get",
    "acknowledge_alert_event": "alerts events acknowledge",
    "resolve_alert_event": "alerts events resolve",
    "resend_alert_event": "alerts events resend",
    # metrics
    "get_overview": "metrics overview",
    "query_gpu_metrics": "metrics gpu",
    "query_inference_metrics": "metrics inference",
    "query_session_metrics": "metrics sessions",
    "get_live_sessions": "metrics sessions-live",
    "query_usage_metrics": "metrics usage",
    "list_metric_names": "metrics catalog",
    "get_prometheus_exposition": "metrics prometheus",
    "get_live_latency": "metrics latency-live",
    "query_latency_history": "metrics latency",
    "query_request_timing": "metrics request-timing",
    # logs
    "query_logs": "logs list",
    "stream_logs": "logs stream",
    "export_logs": "logs export",
    # benchmarks
    "list_benchmarks": "benchmarks list",
    "create_benchmark": "benchmarks create",
    "get_benchmark": "benchmarks get",
    "update_benchmark": "benchmarks update",
    "delete_benchmark": "benchmarks delete",
    "start_benchmark": "benchmarks start",
    "auto_tune_benchmark": "benchmarks auto-tune",
    "list_benchmark_runs": "benchmarks runs",
    "get_benchmark_run": "benchmark-runs get",
    "cancel_benchmark_run": "benchmark-runs cancel",
    "apply_benchmark_recommendation": "benchmark-runs apply",
    "export_benchmark_run": "benchmark-runs export",
    # Anthropic message batches
    "list_batches": "batches list",
    "get_batch": "batches get",
    "cancel_batch": "batches cancel",
    # agent downloads
    "list_agent_downloads": "agent downloads",
    "download_agent": "agent download",
    "download_agent_sha_256": "agent sha256",
    "agent_install_script": "agent install-script",
    # SSH GPU instances
    "list_gpu_instances": "gpu-instances list",
    "create_gpu_instance": "gpu-instances create",
    "get_gpu_instance": "gpu-instances get",
    "update_gpu_instance": "gpu-instances update",
    "delete_gpu_instance": "gpu-instances delete",
    "get_gpu_instance_connection": "gpu-instances connection",
    "stop_gpu_instance": "gpu-instances stop",
    "start_gpu_instance": "gpu-instances start",
    # cloud GPU VMs
    "list_cloud_providers": "cloud providers list",
    "check_cloud_credential": "cloud providers check-credential",
    "list_cloud_regions": "cloud providers regions",
    "list_cloud_instance_types": "cloud providers instance-types",
    "list_cloud_quotas": "cloud providers quotas",
    "list_cloud_nodes": "cloud nodes list",
    "create_cloud_nodes": "cloud nodes create",
    "get_cloud_node": "cloud nodes get",
    "update_cloud_node": "cloud nodes update",
    "delete_cloud_node": "cloud nodes delete",
    "stop_cloud_node": "cloud nodes stop",
    "start_cloud_node": "cloud nodes start",
    "terminate_cloud_node": "cloud nodes terminate",
    "refresh_cloud_node": "cloud nodes refresh",
    # agent self-update (agent protocol §14)
    "get_agent_update_overview": "agent updates",
    "list_agent_update_runs": "agent update-runs list",
    "get_agent_update_run": "agent update-runs get",
    "list_agent_rollouts": "agent rollouts list",
    "get_agent_rollout": "agent rollouts get",
    "cancel_agent_rollout": "agent rollouts cancel",
    "get_node_agent_update": "nodes agent-update",
    "update_node_agent": "nodes update-agent",
    "stage_node_agent_update": "nodes stage-agent-update",
    "get_cluster_agent_update": "clusters agent-update",
    "start_cluster_agent_rollout": "clusters rolling-update",
    "set_cluster_agent_update_policy": "clusters agent-update-policy",
    # pki
    "get_ca_certificate": "pki ca",
    "get_crl": "pki crl",
    "list_certificates": "pki certificates list",
    "issue_service_certificate": "pki issue",
    "revoke_certificate": "pki revoke",
    # system
    "get_health": "system health",
    "get_readiness": "system ready",
    "get_liveness": "system live",
    "get_system_info": "system info",
    "get_branding": "system branding",
    # live
    "stream_live_events": "live events",
    "connect_live_web_socket": "live ws",
}

# Operations implemented by hand in gpudeck.commands (they still appear in the tree).
OVERRIDDEN: set[str] = {
    "login",
    "logout",
    "stream_live_events",
    "stream_logs",
    "connect_live_web_socket",
}

GROUP_HELP: dict[str, str] = {
    "auth": "Login, sessions, CSRF, password",
    "api-keys": "API keys used by your AI gateway or other clients against the serving API",
    "audit": "Immutable audit log",
    "ip-rules": "IP allow/block rules for the admin API and the serving API",
    "vault": "Encrypted credentials (cloud keys, HF/ModelScope tokens, SMTP/Slack secrets)",
    "clusters": "Clusters of GPU machines",
    "nodes": "GPU machines running the node agent (join tokens, drain, certificates)",
    "devices": "Accelerator inventory and slices",
    "topology": "Live cluster → node → device → instance graph",
    "catalog": "Search Hugging Face / ModelScope, scan local paths",
    "models": "Registered models, versions and files",
    "engines": "Engine registry, versions, compatibility checks",
    "deployments": "Deployments: create, scale, start/stop, traffic weights",
    "instances": "Running replicas",
    "cache": "Warm model cache on node disks: prefetch, evict, pin",
    "layouts": "Named GPU layouts (Day/Night) and apply/preview",
    "rules": "Automation rules (6 types) with dry run and history",
    "rule-runs": "Rule evaluation history across all rules",
    "swaps": "Swap manager operations",
    "alerts": "Alert channels (Slack/email/webhook) and events",
    "metrics": "GPU, inference, session and usage time series",
    "logs": "Engine, agent, rule, swap and control-plane logs",
    "benchmarks": "Load tests and auto-tune",
    "benchmark-runs": "Benchmark runs",
    "batches": "Anthropic message batches",
    "pki": "Internal CA: CA certificate, CRL, service certificates",
    "system": "Health, readiness, system info, branding",
    "live": "Live event streams",
    "agent": "Node agent downloads, the install script and agent self-update",
    "gpu-instances": "SSH GPU instances (containers with an sshd on a node)",
    "cloud": "Cloud GPU VMs: providers, regions, instance types, lifecycle",
}

SUBGROUP_HELP: dict[str, str] = {
    "auth sessions": "Admin sessions",
    "nodes join-tokens": "One-time join tokens",
    "nodes certificates": "Node certificates",
    "models versions": "Model versions and files",
    "engines versions": "Engine versions",
    "alerts channels": "Alert channels",
    "alerts events": "Alert events",
    "cloud providers": "Cloud providers, credentials check, regions, types, quotas",
    "cloud nodes": "Cloud GPU VMs",
    "agent update-runs": "Agent self-update runs (steps, outcome)",
    "agent rollouts": "Rolling agent updates of clusters",
}


# ----- parameter types ------------------------------------------------------------------------


def parse_datetime(value: str | dt.datetime) -> dt.datetime:
    """ISO 8601 with ``Z`` or an offset (``2026-11-01T00:00:00Z``), or a bare date."""
    if isinstance(value, dt.datetime):
        return value
    text = value.strip()
    return dt.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)


class DateTimeType(click.ParamType):
    name = "datetime"

    def convert(self, value: Any, param: Any, ctx: Any) -> dt.datetime:
        try:
            return parse_datetime(value)
        except ValueError:
            self.fail(f"{value!r} is not an ISO 8601 date/time", param, ctx)


class DateType(click.ParamType):
    name = "date"

    def convert(self, value: Any, param: Any, ctx: Any) -> dt.date:
        if isinstance(value, dt.date):
            return value
        try:
            return parse_datetime(value).date()
        except ValueError:
            self.fail(f"{value!r} is not an ISO 8601 date", param, ctx)


_CLICK_TYPES: dict[str, Any] = {
    "string": click.STRING,
    "uuid": click.UUID,
    "datetime": DateTimeType(),
    "date": DateType(),
    "integer": click.INT,
    "number": click.FLOAT,
    "boolean": click.BOOL,
}

META_OPTIONS = {"--file", "-f", "--data", "-d", "--set", "-s", "--all", "--help", "-h"}


def click_type(kind: str, extra: Any) -> Any:
    if kind == "enum":
        return click.Choice([str(v) for v in extra])
    return _CLICK_TYPES[kind]


def _help_text(text: str, limit: int = 300) -> str:
    """First paragraph of a spec description, escaped for rich markup."""
    from rich.markup import escape

    para = " ".join(text.strip().split("\n\n")[0].split())
    if len(para) > limit:
        para = para[: limit - 3].rstrip() + "..."
    return escape(para)


# ----- commands -------------------------------------------------------------------------------


class Binding:
    """How one click parameter maps back onto the request."""

    __slots__ = ("kind", "name", "scalar")

    def __init__(self, kind: str, name: str, scalar: tuple[str, Any] | None = None) -> None:
        self.kind = kind  # path | query | header | body | meta
        self.name = name  # API name (path/query/header/body) or meta key
        self.scalar = scalar


def make_command(name: str, op: Operation, spec: Spec) -> click.Command:
    """Build the click command for one operation."""
    params: list[click.Parameter] = []
    bindings: dict[str, Binding] = {}
    used: set[str] = set(META_OPTIONS)

    for i, p in enumerate(op.path_params):
        scalar: tuple[str, Any] | None = spec.scalar(p.schema) or ("string", None)
        assert scalar is not None
        kind = scalar[0] if scalar[0] in ("uuid", "integer", "enum") else "string"
        py = f"p{i}"
        params.append(
            TyperArgument(
                param_decls=[py],
                type=click_type(kind, scalar[1]),
                required=True,
                metavar=snake(p.name).upper(),
                help=_help_text(p.description) or None,
            )
        )
        bindings[py] = Binding("path", p.name, scalar)

    for i, p in enumerate(op.params):
        if p.location == "path":
            continue
        scalar = spec.scalar(p.schema)
        opt = "--" + kebab(p.name)
        if opt in used:
            continue
        used.add(opt)
        py = f"q{i}"
        # objects in a query string: pass the raw value through
        q_scalar = scalar if scalar is not None else ("string", None)
        params.append(_option(opt, py, q_scalar, p.required, p.default, p.description))
        bindings[py] = Binding(p.location, p.name, q_scalar)

    if op.body is not None:
        for i, f in enumerate(op.body.fields):
            scalar = spec.scalar(f.schema)
            opt = "--" + kebab(f.name)
            if scalar is None or opt in used:
                continue
            used.add(opt)
            py = f"b{i}"
            desc = f.description or f"Body field {f.name}"
            if f.required:
                desc += " (required in the body)"
            params.append(_option(opt, py, scalar, False, None, desc))
            bindings[py] = Binding("body", f.name, scalar)
        params.append(
            TyperOption(
                param_decls=["--data", "-d", "body_data"],
                required=False,
                help="Request body: inline JSON, @file.json|yaml, or - for stdin",
            )
        )
        params.append(
            TyperOption(
                param_decls=["--file", "-f", "body_file"],
                required=False,
                help="JSON/YAML body file ('-' = stdin)",
            )
        )
        params.append(
            TyperOption(
                param_decls=["--set", "-s", "body_set"],
                multiple=True,
                required=False,
                help="Set a body field: key.path=json-or-string (repeatable)",
            )
        )
        bindings["body_data"] = Binding("meta", "data")
        bindings["body_file"] = Binding("meta", "file")
        bindings["body_set"] = Binding("meta", "set")

    if op.paginated:
        params.append(
            TyperOption(
                param_decls=["--all", "fetch_all"],
                is_flag=True,
                required=False,
                default=False,
                help="Follow pagination and return every page",
            )
        )
        bindings["fetch_all"] = Binding("meta", "all")

    def callback(**values: Any) -> None:
        from .execute import run_operation

        run_operation(click.get_current_context(), op, bindings, values)

    summary = op.summary or op.key.replace("_", " ")
    text = _help_text(summary, 200)
    if op.description:
        text += "\n\n" + _help_text(op.description, 1200)
    text += f"\n\n[dim]{op.method} {op.path}[/dim]"
    return TyperCommand(
        name,
        params=params,
        callback=callback,
        help=text,
        short_help=_help_text(summary, 200),
        rich_markup_mode="rich",
        context_settings={"help_option_names": ["-h", "--help"]},
    )


def _option(
    opt: str, py: str, scalar: tuple[str, Any], required: bool, default: Any, desc: str
) -> click.Option:
    kind, extra = scalar
    help_text = _help_text(desc) or None
    if kind == "boolean":
        return TyperOption(
            param_decls=[f"{opt}/--no-{opt[2:]}", py],
            is_flag=True,
            default=default if isinstance(default, bool) else None,
            required=required,
            show_default=isinstance(default, bool),
            help=help_text,
        )
    if kind == "array":
        item_kind, item_extra = extra
        return TyperOption(
            param_decls=[opt, py],
            type=click_type(item_kind, item_extra),
            multiple=True,
            required=required,
            default=tuple(default) if isinstance(default, list) and default else None,
            help=help_text,
        )
    usable_default = default if default is not None and not required else None
    if usable_default is not None and kind == "enum" and str(usable_default) not in extra:
        usable_default = None
    return TyperOption(
        param_decls=[opt, py],
        type=click_type(kind, extra),
        required=required,
        default=usable_default,
        show_default=usable_default is not None,
        help=help_text,
    )


# ----- tree -----------------------------------------------------------------------------------


def build_tree(spec: Spec) -> dict[str, Any]:
    """``{group: {sub: {command: Operation}}}`` in COMMAND_MAP order (unmapped operations last)."""
    order = {k: i for i, k in enumerate(COMMAND_MAP)}
    ops = sorted(spec.operations(), key=lambda o: order.get(o.key, len(order)))
    tree: dict[str, Any] = {}
    for op in ops:
        if op.key in OVERRIDDEN:
            continue
        target = COMMAND_MAP.get(op.key)
        if target is None:
            if op.streaming:
                logger.debug("skipping unmapped streaming operation %s", op.key)
                continue
            group = kebab(op.tags[0]) if op.tags else "misc"
            target = f"{group} {kebab(op.key)}"
            logger.debug("operation %s not in COMMAND_MAP; registering as %s", op.key, target)
        *group_path, name = target.split()
        node: Any = tree
        for part in group_path:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                break
        if not isinstance(node, dict):
            logger.debug("cannot place %s at %s", op.key, target)
            continue
        if name in node:
            name = f"{name}-{op.method.lower()}"
            if name in node:
                continue
        node[name] = op
    return tree


def group_help(path: list[str], spec: Spec | None) -> str:
    key = " ".join(path)
    text = GROUP_HELP.get(key) if len(path) == 1 else SUBGROUP_HELP.get(key)
    if text:
        return text
    if spec is not None and len(path) == 1:
        for tag in spec.doc.get("tags") or []:
            if isinstance(tag, dict) and kebab(str(tag.get("name", ""))) == path[0]:
                return _help_text(str(tag.get("description") or tag.get("name")))
    return key


def _ctx_path(ctx: click.Context) -> list[str]:
    names: list[str] = []
    cur: click.Context | None = ctx
    while cur is not None and cur.parent is not None:
        names.append(cur.info_name or "")
        cur = cur.parent
    return list(reversed(names))


class SpecGroup(TyperGroup):
    """A group whose subcommands are its hand-written commands plus the spec's operations."""

    def _node(self, ctx: click.Context, allow_fetch: bool = True) -> dict[str, Any]:
        try:
            node: Any = get_state(ctx).tree(allow_fetch=allow_fetch)
        except ConfigError as exc:
            # hand-written commands (login, config, live) keep working without a spec
            logger.warning("%s", exc.message)
            return {}
        for part in _ctx_path(ctx):
            node = node.get(part) if isinstance(node, dict) else None
        return node if isinstance(node, dict) else {}

    def list_commands(self, ctx: click.Context) -> list[str]:
        # The root listing (gpudeck --help) never fetches; groups may refresh the spec.
        node = self._node(ctx, allow_fetch=ctx.parent is not None)
        return list(node) + [n for n in self.commands if n not in node]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = self.commands.get(cmd_name)
        if cmd is not None:
            return cmd
        # A group needs no network: its own children are resolved (and the spec refreshed) only
        # when it is used, so `gpudeck --help` lists groups from the cache or bundled spec.
        child = self._node(ctx, allow_fetch=False).get(cmd_name)
        if not isinstance(child, dict):
            child = self._node(ctx).get(cmd_name)
        if isinstance(child, dict):
            spec = get_state(ctx).spec(allow_fetch=False).spec
            return SpecGroup(
                name=cmd_name,
                help=group_help(_ctx_path(ctx) + [cmd_name], spec),
                no_args_is_help=True,
                rich_markup_mode="rich",
                context_settings={"help_option_names": ["-h", "--help"]},
            )
        if isinstance(child, Operation):
            return make_command(cmd_name, child, get_state(ctx).spec().spec)
        return None

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return click.Group.resolve_command(self, ctx, args)
        except click.UsageError as exc:
            if args and not ctx.resilient_parsing:
                matches = get_close_matches(args[0], self.list_commands(ctx))
                if matches:
                    exc.message = (  # type: ignore[misc]
                        f"{exc.message.rstrip('.')}. Did you mean "
                        + ", ".join(repr(m) for m in matches)
                        + "?"
                    )
            raise


def ensure_group(root: typer.Typer, groups: dict[str, typer.Typer], path: list[str]) -> typer.Typer:
    """Return (creating on demand) the Typer app for ``path`` (``["nodes", "join-tokens"]``)."""
    key = " ".join(path)
    if key in groups:
        return groups[key]
    parent = root if len(path) == 1 else ensure_group(root, groups, path[:-1])
    app = typer.Typer(
        cls=SpecGroup,
        help=group_help(path, None),
        no_args_is_help=True,
        rich_markup_mode="rich",
    )
    parent.add_typer(app, name=path[-1])
    groups[key] = app
    return app


def command_tree(app: typer.Typer) -> list[str]:
    """Flatten the CLI into ``group sub command`` paths (used by tests and docs)."""
    from typer.main import get_command

    out: list[str] = []

    def walk(cmd: click.Command, prefix: list[str], ctx: click.Context) -> None:
        if isinstance(cmd, click.Group):
            for name in cmd.list_commands(ctx):
                sub = cmd.get_command(ctx, name)
                if sub is not None:
                    walk(sub, prefix + [name], click.Context(sub, parent=ctx, info_name=name))
        else:
            out.append(" ".join(prefix))

    root = get_command(app)
    walk(root, [], click.Context(root, info_name="gpudeck"))
    return out


__all__ = [
    "COMMAND_MAP",
    "GROUP_HELP",
    "OVERRIDDEN",
    "SUBGROUP_HELP",
    "SpecGroup",
    "build_tree",
    "command_tree",
    "ensure_group",
    "make_command",
    "parse_datetime",
]
