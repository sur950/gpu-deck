"""Output rendering for the gpudeck CLI: rich tables (default on a TTY), JSON, YAML, CSV."""

from __future__ import annotations

import csv
import datetime as dt
import enum
import json
import logging
import sys
import uuid
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

console = Console()
err_console = Console(stderr=True)

FORMATS = ("table", "json", "yaml", "csv")
PREFERRED_COLUMNS = (
    "id",
    "name",
    "username",
    "type",
    "kind",
    "status",
    "state",
    "health",
    "enabled",
    "vendor",
    "version",
    "replicas",
    "ready_replicas",
    "priority",
    "scope",
    "action",
    "cidr",
    "severity",
    "outcome",
    "event_type",
    "node_name",
    "model_name",
    "deployment_name",
    "engine_name",
    "cluster_id",
    "node_id",
    "expires_at",
    "created_at",
)
MAX_AUTO_COLUMNS = 9


def to_plain(value: Any) -> Any:
    """Convert enums / UUIDs / datetimes / objects with ``to_dict`` to JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_plain(value.to_dict())
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_plain(v) for v in value]
    return str(value)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, separators=(",", ":"), default=str)
        return text if len(text) <= 60 else text[:57] + "..."
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1e6 else f"{value:.0f}"
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


def choose_columns(rows: Sequence[dict[str, Any]], fields: Sequence[str] | None) -> list[str]:
    if fields:
        return list(fields)
    keys: list[str] = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    preferred = [k for k in PREFERRED_COLUMNS if k in keys]
    rest = [k for k in keys if k not in preferred and not isinstance(rows[0].get(k), (dict, list))]
    cols = preferred + rest
    return cols[:MAX_AUTO_COLUMNS]


def render(
    data: Any,
    meta: Any = None,
    *,
    fmt: str = "table",
    fields: Sequence[str] | None = None,
    title: str | None = None,
) -> None:
    """Print ``data`` in the requested format. Tables fall back to JSON for scalars."""
    if isinstance(data, bytes):
        # binary download (agent tarball): raw bytes, whatever the output format
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return
    if isinstance(data, str) and meta is None:
        # raw text body (CSV, PEM, Prometheus exposition, sha256): print as-is in every format,
        # so `gpudeck audit export > audit.csv` works when stdout is not a terminal
        sys.stdout.write(data)
        if not data.endswith("\n"):
            sys.stdout.write("\n")
        return
    data = to_plain(data)
    meta = to_plain(meta)
    if fmt == "json":
        payload = data if meta is None else {"data": data, "meta": meta}
        if sys.stdout.isatty():
            console.print_json(json.dumps(payload, default=str), highlight=console.is_terminal)
        else:
            # Piped or redirected: plain JSON even when FORCE_COLOR / CI make rich colour output.
            sys.stdout.write(json.dumps(payload, default=str, indent=2, ensure_ascii=False) + "\n")
        return
    if fmt == "yaml":
        import yaml

        payload = data if meta is None else {"data": data, "meta": meta}
        sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return
    if fmt == "csv":
        _csv(data, fields)
        return
    if isinstance(data, list):
        if not data:
            console.print("(no results)")
        elif all(isinstance(r, dict) for r in data):
            _table(data, fields, title)
        else:
            for item in data:
                console.print(_cell(item))
        if isinstance(meta, dict) and "total" in meta:
            shown = len(data)
            console.print(
                f"[dim]{shown} of {meta.get('total')} (page {meta.get('page')}, "
                f"page_size {meta.get('page_size')}"
                f"{', more available' if meta.get('has_more') else ''})[/dim]"
            )
        return
    if isinstance(data, dict):
        _kv_table(data, fields, title)
        return
    if data is None:
        console.print("[green]ok[/green]")
        return
    # raw text (CSV, PEM, Prometheus exposition)
    sys.stdout.write(str(data))
    if not str(data).endswith("\n"):
        sys.stdout.write("\n")


def _csv(data: Any, fields: Sequence[str] | None) -> None:
    """Rows as CSV (header from ``--fields`` or every key); nested values as compact JSON."""
    rows = data if isinstance(data, list) else ([data] if data is not None else [])
    if rows and not all(isinstance(r, dict) for r in rows):
        rows = [{"value": r} for r in rows]
    cols = list(fields) if fields else []
    if not cols:
        for row in rows:
            cols.extend(k for k in row if k not in cols)
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(cols)
    for row in rows:
        out = []
        for c in cols:
            v = _dig(row, c)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, separators=(",", ":"), default=str)
            out.append("" if v is None else v)
        writer.writerow(out)


def _table(rows: Sequence[dict[str, Any]], fields: Sequence[str] | None, title: str | None) -> None:
    cols = choose_columns(rows, fields)
    table = Table(title=title, show_lines=False, header_style="bold", box=None, pad_edge=False)
    for c in cols:
        table.add_column(c)
    for row in rows:
        table.add_row(*[_cell(_dig(row, c)) for c in cols])
    console.print(table)


def _kv_table(obj: dict[str, Any], fields: Sequence[str] | None, title: str | None) -> None:
    table = Table(title=title, show_header=False, box=None, pad_edge=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    keys = list(fields) if fields else list(obj.keys())
    for k in keys:
        v = _dig(obj, k)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, indent=2, default=str)
            if len(v) > 2000:
                v = v[:1997] + "..."
        table.add_row(k, _cell(v) if not isinstance(v, str) else v)
    console.print(table)


def _dig(obj: dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def parse_fields(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [f.strip() for f in value.split(",") if f.strip()]


def print_warning(message: str) -> None:
    err_console.print(f"[yellow]warning:[/yellow] {message}")


def print_error(message: str) -> None:
    err_console.print(f"[red]error:[/red] {message}")


def print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        console.print(line)
