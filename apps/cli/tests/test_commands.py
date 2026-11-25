"""Generated resource commands: output formats, pagination, bodies, errors, raw exports."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import Response

from gpudeck.errors import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_USAGE

from .conftest import envelope, error, run, run_json

CID = "11111111-1111-4111-8111-111111111111"
CLUSTER = {
    "id": CID,
    "name": "lab",
    "type": "bare_metal",
    "status": "active",
    "created_at": "2026-11-17T00:00:00Z",
}
META = {"total": 1, "page": 1, "page_size": 50, "has_more": False}


def test_list_json(api, logged_in, capsys):
    route = api.get("/clusters").mock(return_value=envelope([CLUSTER], META))
    data = run_json(["clusters", "list", "--type", "bare_metal", "--search", "la"], capsys)
    assert data["data"][0]["name"] == "lab" and data["meta"]["total"] == 1
    params = dict(route.calls[0].request.url.params)
    assert params["type"] == "bare_metal" and params["search"] == "la" and params["page"] == "1"


def test_list_table_and_fields(api, logged_in, capsys, monkeypatch):
    monkeypatch.setenv("GPUDECK_OUTPUT", "table")
    api.get("/clusters").mock(return_value=envelope([CLUSTER], META))
    code, out, _ = run(["-o", "table", "--fields", "name,status", "clusters", "list"], capsys)
    assert code == 0
    assert "name" in out and "status" in out and "lab" in out and "active" in out
    assert "bare_metal" not in out  # not in --fields


def test_list_yaml(api, logged_in, capsys):
    api.get("/clusters").mock(return_value=envelope([CLUSTER], META))
    code, out, _ = run(["-o", "yaml", "clusters", "list"], capsys)
    assert code == 0 and "name: lab" in out


def test_pagination_all(api, logged_in, capsys):
    pages = {
        "1": envelope(
            [{"id": "a"}, {"id": "b"}], {"total": 3, "page": 1, "page_size": 2, "has_more": True}
        ),
        "2": envelope([{"id": "c"}], {"total": 3, "page": 2, "page_size": 2, "has_more": False}),
    }
    api.get("/nodes").mock(side_effect=lambda req: pages[req.url.params["page"]])
    data = run_json(["nodes", "list", "--all", "--page-size", "2"], capsys)
    assert [r["id"] for r in data["data"]] == ["a", "b", "c"]
    assert data["meta"]["page_size"] == 3


def test_get_by_id(api, logged_in, capsys):
    api.get(f"/clusters/{CID}").mock(return_value=envelope(CLUSTER))
    data = run_json(["clusters", "get", CID], capsys)
    assert data["id"] == CID


def test_create_with_options_set_and_file(api, logged_in, capsys, tmp_path: Path):
    route = api.post("/clusters").mock(
        return_value=Response(
            201, json={"success": True, "data": CLUSTER, "error": None, "meta": None}
        )
    )
    body_file = tmp_path / "c.yaml"
    body_file.write_text("description: from file\nlabels:\n  zone: office\n")
    run_json(
        [
            "clusters",
            "create",
            "--file",
            str(body_file),
            "--name",
            "lab",
            "--type",
            "bare_metal",
            "--egress-allowlist",
            "a:443",
            "--egress-allowlist",
            "b:443",
            "--set",
            "labels.rack=r1",
            "--set",
            "config.max_nodes=5",
        ],
        capsys,
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["name"] == "lab" and sent["type"] == "bare_metal"
    assert sent["description"] == "from file"
    assert sent["labels"] == {"zone": "office", "rack": "r1"}
    assert sent["config"] == {"max_nodes": 5}
    assert sent["egress_allowlist"] == ["a:443", "b:443"]


def test_create_from_stdin(api, logged_in, capsys, monkeypatch):
    route = api.post("/rules").mock(return_value=envelope({"id": "r1"}))
    monkeypatch.setattr(
        "sys.stdin",
        _Stdin(json.dumps({"name": "idle", "type": "idle_unload", "params": {"idle_minutes": 30}})),
    )
    run_json(["rules", "create", "-f", "-"], capsys)
    assert json.loads(route.calls[0].request.content)["params"] == {"idle_minutes": 30}


def test_missing_required_body_field_is_usage_error(api, logged_in, capsys):
    code, _, err = run(["clusters", "create", "--name", "x"], capsys)
    assert code == EXIT_USAGE and "type" in err


def test_api_key_create_shows_key_once(api, logged_in, capsys):
    api.post("/api-keys").mock(
        return_value=envelope(
            {
                "id": "k1",
                "name": "gw",
                "key_prefix": "gd-abc",
                "status": "active",
                "total_requests": 0,
                "created_at": "2026-11-17T00:00:00Z",
                "key": "gd-abc-full-secret",
            }
        )
    )
    data = run_json(["api-keys", "create", "--name", "gw"], capsys)
    assert data["key"] == "gd-abc-full-secret"


def test_rule_dry_run_and_204(api, logged_in, capsys):
    rid = "22222222-2222-4222-8222-222222222222"
    api.post(f"/rules/{rid}/dry-run").mock(
        return_value=envelope({"id": "run1", "outcome": "dry_run", "actions": []})
    )
    data = run_json(["rules", "dry-run", rid], capsys)
    assert data["outcome"] == "dry_run"
    api.delete(f"/rules/{rid}").mock(return_value=Response(204))
    code, out, _ = run(["-o", "table", "rules", "delete", rid], capsys)
    assert code == 0 and "ok" in out


def test_join_token_create(api, logged_in, capsys):
    route = api.post("/nodes/join-tokens").mock(
        return_value=envelope(
            {
                "id": "t1",
                "cluster_id": CID,
                "token_prefix": "gdj_",
                "expires_at": "x",
                "created_at": "x",
                "token": "gdj_secret",
                "join_command": "gpudeck-agent join ...",
            }
        )
    )
    data = run_json(
        ["nodes", "join-tokens", "create", "--cluster-id", CID, "--ttl-minutes", "5"], capsys
    )
    assert data["join_command"].startswith("gpudeck-agent join")
    assert json.loads(route.calls[0].request.content) == {"cluster_id": CID, "ttl_minutes": 5}


def test_metrics_query_params(api, logged_in, capsys):
    route = api.get("/metrics/gpu").mock(return_value=envelope({"series": []}))
    run_json(
        [
            "metrics",
            "gpu",
            "--metrics",
            "gpu_utilization_pct",
            "--metrics",
            "gpu_power_watts",
            "--from",
            "2026-11-17T00:00:00",
            "--aggregate",
            "avg",
        ],
        capsys,
    )
    params = route.calls[0].request.url.params
    assert params.get_list("metrics") == ["gpu_utilization_pct", "gpu_power_watts"]
    assert params["aggregate"] == "avg" and params["from"].startswith("2026-11-17T00:00:00")


def test_raw_text_export(api, logged_in, capsys):
    api.get("/audit/export").mock(
        return_value=Response(200, text="id,event\n1,login\n", headers={"content-type": "text/csv"})
    )
    code, out, _ = run(["-o", "table", "audit", "export"], capsys)
    assert code == 0 and out.startswith("id,event")


def test_prometheus_exposition(api, logged_in, capsys):
    api.get("/metrics/prometheus").mock(
        return_value=Response(
            200, text="gpudeck_gpu_utilization_ratio 0.5\n", headers={"content-type": "text/plain"}
        )
    )
    code, out, _ = run(["-o", "table", "metrics", "prometheus"], capsys)
    assert code == 0 and "gpudeck_gpu_utilization_ratio" in out


def test_error_envelope_exit_codes(api, logged_in, capsys):
    api.get(f"/clusters/{CID}").mock(
        return_value=error(404, "RESOURCE_NOT_FOUND", "no such cluster")
    )
    code, _, err = run(["clusters", "get", CID], capsys)
    assert code == EXIT_NOT_FOUND and "no such cluster" in err
    api.get("/clusters").mock(return_value=error(500, "INTERNAL_ERROR"))
    code, _, _ = run(["clusters", "list"], capsys)
    assert code == EXIT_ERROR
    api.post("/clusters").mock(return_value=error(422, "VALIDATION_ERROR", "name too long"))
    code, _, err = run(["clusters", "create", "--name", "x", "--type", "vm"], capsys)
    assert code == EXIT_USAGE and "VALIDATION_ERROR" in err


def test_connection_error(api, logged_in, capsys):
    import httpx

    api.get("/clusters").mock(side_effect=httpx.ConnectError("refused"))
    code, _, err = run(["clusters", "list"], capsys)
    assert code == 5 and "cannot reach" in err


def test_live_events_sse(api, logged_in, capsys):
    body = (
        'event: node.online\ndata: {"node_id": "n1", "name": "mac-1"}\n\nevent: ping\ndata: {}\n\n'
    )
    api.get("/live/events").mock(
        return_value=Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    code, out, _ = run(["live", "events", "--limit", "2", "-t", "nodes"], capsys)
    assert code == 0
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert lines[0]["event"] == "node.online" and lines[0]["data"]["name"] == "mac-1"
    assert lines[1]["event"] == "ping"


def test_config_commands(api, capsys, logged_in):
    code, out, _ = run(
        ["config", "set", "prod", "-e", "https://gpudeck.example.com", "--no-verify-tls"], capsys
    )
    assert code == 0
    data = run_json(["config", "show", "prod"], capsys)
    assert data["endpoint"] == "https://gpudeck.example.com" and data["verify_tls"] is False
    data = run_json(["config", "list"], capsys)
    assert {r["name"] for r in data} == {"default", "prod"}
    assert run(["config", "use", "default"], capsys)[0] == 0
    assert run(["config", "delete", "prod"], capsys)[0] == 0


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def readline(self) -> str:
        return self._text


def test_required_query_parameter_is_required_option(api, logged_in, capsys):
    code, _, err = run(["metrics", "sessions"], capsys)
    assert code == EXIT_USAGE and "--instance-id" in err
    route = api.get("/metrics/sessions").mock(return_value=envelope({"series": []}))
    run_json(["metrics", "sessions", "--instance-id", CID], capsys)
    assert route.calls[0].request.url.params["instance_id"] == CID


def test_binary_download_writes_raw_bytes(api, logged_in, capsysbinary):
    blob = bytes(range(256)) * 4
    api.get("/agent/downloads/1.1.0/aarch64-apple-darwin").mock(
        return_value=Response(200, content=blob, headers={"content-type": "application/gzip"})
    )
    from gpudeck import main as cli_main

    code = cli_main.main(["agent", "download", "1.1.0", "aarch64-apple-darwin"])
    out, _ = capsysbinary.readouterr()
    assert code == 0 and out == blob


def test_iso_datetime_with_z_and_raw_text_when_piped(api, logged_in, capsys):
    route = api.post("/api-keys").mock(return_value=envelope({"id": CID}))
    run_json(
        ["api-keys", "create", "--name", "nov", "--expires-at", "2026-11-01T00:00:00Z"],
        capsys,
    )
    body = json.loads(route.calls[0].request.content)
    assert body["expires_at"].startswith("2026-11-01T00:00:00")
    assert body["expires_at"].endswith(("Z", "+00:00"))
    route = api.post("/ip-rules").mock(return_value=envelope({"id": CID}))
    run_json(
        [
            "ip-rules",
            "create",
            "--action",
            "block",
            "--scope",
            "admin",
            "--cidr",
            "10.0.0.0/8",
            "--expires-at",
            "2026-12-01",
        ],
        capsys,
    )
    body = json.loads(route.calls[0].request.content)
    assert body["expires_at"].startswith("2026-12-01")
    api.get("/audit/export").mock(
        return_value=Response(200, text="id,event\n1,x\n", headers={"content-type": "text/csv"})
    )
    code, out, _ = run(["audit", "export"], capsys)  # GPUDECK_OUTPUT=json in tests
    assert code == 0 and out == "id,event\n1,x\n"


# ----- runtime spec commands ---------------------------------------------------------------------


def _get_operations():
    from gpudeck.openapi import Spec
    from gpudeck.registry import COMMAND_MAP, OVERRIDDEN
    from gpudeck.spec import bundled_spec_path, read_document

    spec = Spec(read_document(bundled_spec_path()))
    for op in spec.operations():
        required = [p for p in op.params if p.location != "path" and p.required]
        if op.method != "GET" or op.key in OVERRIDDEN or op.streaming or required:
            continue
        yield COMMAND_MAP[op.key], op, spec


def test_every_simple_get_command_calls_its_route(api, logged_in, capsys):
    """One request per GET operation: right method, URL and path parameter substitution."""
    import uuid as uuid_mod

    checked = 0
    for target, op, spec in _get_operations():
        args, path = [], op.path
        for p in op.path_params:
            kind, extra = spec.scalar(p.schema) or ("string", None)
            value = {"uuid": str(uuid_mod.uuid4()), "enum": (extra or ["v1"])[0]}.get(kind, "v1")
            value = "7" if kind == "integer" else value
            args.append(value)
            path = path.replace("{" + p.name + "}", value)
        route = api.get(path).mock(return_value=envelope({"ok": True}))
        code, out, err = run(target.split() + args, capsys)
        assert code == 0, f"{target}: {err}"
        assert route.called, target
        checked += 1
    assert checked >= 90


def test_data_option_inline_file_and_stdin(api, logged_in, capsys, tmp_path, monkeypatch):
    route = api.post("/clusters").mock(return_value=envelope(CLUSTER))
    run_json(["clusters", "create", "--data", '{"name": "a", "type": "vm"}'], capsys)
    assert json.loads(route.calls[-1].request.content) == {"name": "a", "type": "vm"}
    body = tmp_path / "b.yaml"
    body.write_text("name: b\ntype: kubernetes\n")
    run_json(["clusters", "create", "-d", f"@{body}", "--name", "override"], capsys)
    assert json.loads(route.calls[-1].request.content) == {"name": "override", "type": "kubernetes"}
    monkeypatch.setattr("sys.stdin", _Stdin('{"name": "c", "type": "vm"}'))
    run_json(["clusters", "create", "--data", "-"], capsys)
    assert json.loads(route.calls[-1].request.content)["name"] == "c"
    code, _, err = run(["clusters", "create", "--data", "{not json"], capsys)
    assert code == EXIT_USAGE and "--data is not valid JSON" in err


def test_body_is_validated_against_the_schema(api, logged_in, capsys):
    route = api.post("/clusters").mock(return_value=envelope(CLUSTER))
    code, _, err = run(["clusters", "create", "--name", "x", "--set", "type=bogus"], capsys)
    assert code == EXIT_USAGE and "invalid request body at type" in err and not route.called
    code, _, err = run(
        ["clusters", "create", "--name", "x", "--type", "vm", "--set", "unknown_field=1"], capsys
    )
    assert code == EXIT_USAGE and "unknown_field" in err and not route.called
    run_json(["--no-validate", "clusters", "create", "--name", "x", "--set", "type=bogus"], capsys)
    assert json.loads(route.calls[0].request.content)["type"] == "bogus"


def test_csv_output(api, logged_in, capsys):
    api.get("/clusters").mock(
        return_value=envelope([CLUSTER, {**CLUSTER, "name": "b", "labels": {"a": 1}}], META)
    )
    code, out, _ = run(["-o", "csv", "--fields", "name,status,labels", "clusters", "list"], capsys)
    assert code == 0
    assert out.splitlines() == ["name,status,labels", "lab,active,", 'b,active,"{""a"":1}"']


def test_public_endpoint_needs_no_login(api, capsys, isolated_config):
    from .conftest import ENDPOINT

    route = api.get("/health").mock(return_value=envelope({"status": "ok"}))
    data = run_json(["-e", ENDPOINT, "system", "health"], capsys)
    assert data["status"] == "ok" and "authorization" not in route.calls[0].request.headers


def test_header_parameter_option(api, logged_in, capsys):
    route = api.get("/agent/install.sh").mock(
        return_value=Response(
            200, text="#!/bin/sh\n", headers={"content-type": "text/x-shellscript"}
        )
    )
    code, out, _ = run(["agent", "install-script", "--x-gpu-deck-join-token", "gdj_1"], capsys)
    assert code == 0 and out == "#!/bin/sh\n"
    assert route.calls[0].request.headers["x-gpudeck-join-token"] == "gdj_1"


def test_boolean_body_flags(api, logged_in, capsys):
    route = api.post("/rules").mock(return_value=envelope({"id": "r1"}))
    run_json(
        ["rules", "create", "--name", "n", "--type", "idle_unload", "--no-enabled", "--dry-run"]
        + ["--set", "params.idle_minutes=30"],
        capsys,
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["enabled"] is False and sent["dry_run"] is True


def test_all_uses_a_large_page_size_unless_given(api, logged_in, capsys):
    route = api.get("/clusters").mock(
        return_value=envelope([CLUSTER], {"total": 1, "page": 1, "has_more": False})
    )
    run_json(["clusters", "list", "--all"], capsys)
    assert route.calls[0].request.url.params["page_size"] == "200"


def test_unknown_command_suggests(api, logged_in, capsys):
    code, _, err = run(["clusters", "lst"], capsys)
    assert code == EXIT_USAGE and "Did you mean 'list'" in err


def test_help_text_comes_from_the_spec(capsys):
    code, out, _ = run(["clusters", "create", "--help"], capsys)
    assert code == 0 and "Create a cluster" in out and "POST /clusters" in out
    assert "--egress-allowlist" in out and "required in the body" in out
