"""Every admin API operation is a command, and the tree matches the pre-runtime-spec CLI.

``tests/data/command_tree_v1.json`` is the command tree (paths, argument counts, option names)
of the last CLI built from a generated client. The runtime-spec CLI must keep every command and
every option so scripts and docs written against it keep working; new options may be added.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from typer.main import get_command

from gpudeck.main import app
from gpudeck.openapi import Spec, snake
from gpudeck.registry import COMMAND_MAP, OVERRIDDEN, build_tree, command_tree
from gpudeck.spec import read_document

SPEC = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "openapi" / "admin.yaml"
V1_TREE = Path(__file__).parent / "data" / "command_tree_v1.json"


def _contract() -> Spec:
    return Spec(read_document(SPEC))


def _walk() -> dict[str, click.Command]:
    """Every leaf command, built the way a real invocation builds it."""
    out: dict[str, click.Command] = {}

    def walk(cmd: click.Command, prefix: list[str], ctx: click.Context) -> None:
        if isinstance(cmd, click.Group):
            for name in cmd.list_commands(ctx):
                sub = cmd.get_command(ctx, name)
                assert sub is not None, " ".join(prefix + [name])
                walk(sub, prefix + [name], click.Context(sub, parent=ctx, info_name=name))
        else:
            out[" ".join(prefix)] = cmd

    root = get_command(app)
    walk(root, [], click.Context(root, info_name="gpudeck"))
    return out


def test_every_operation_is_mapped_and_registered():
    ops = _contract().operations()
    assert len(ops) >= 200
    tree = set(command_tree(app))
    missing_map = sorted(op.operation_id for op in ops if op.key not in COMMAND_MAP)
    assert not missing_map, f"operations without a command mapping: {missing_map}"
    missing_cmd = sorted(
        f"{op.operation_id} -> {COMMAND_MAP[op.key]}"
        for op in ops
        if COMMAND_MAP[op.key] not in tree
    )
    assert not missing_cmd, f"mapped but not registered: {missing_cmd}"


def test_operation_keys_are_snake_case_operation_ids():
    for op in _contract().operations():
        assert op.key == snake(op.operation_id)


def test_map_has_no_stale_entries():
    keys = {op.key for op in _contract().operations()}
    stale = sorted(set(COMMAND_MAP) - keys)
    assert not stale, f"COMMAND_MAP entries with no operation in admin.yaml: {stale}"
    assert OVERRIDDEN <= set(COMMAND_MAP)
    targets = list(COMMAND_MAP.values())
    assert len(targets) == len(set(targets)), "two operations map to the same command"


def test_spec_tree_places_every_non_overridden_operation():
    spec = _contract()
    tree = build_tree(spec)
    placed = []

    def walk(node: dict, prefix: list[str]) -> None:
        for name, child in node.items():
            if isinstance(child, dict):
                walk(child, prefix + [name])
            else:
                placed.append((" ".join(prefix + [name]), child.key))

    walk(tree, [])
    assert sorted(k for _, k in placed) == sorted(
        op.key for op in spec.operations() if op.key not in OVERRIDDEN
    )
    assert all(COMMAND_MAP[key] == path for path, key in placed)


def test_tree_matches_the_generated_client_cli():
    """Same commands, same positional arguments, every old option still accepted."""
    v1 = json.loads(V1_TREE.read_text())
    now = _walk()
    missing = sorted(set(v1) - set(now))
    assert not missing, f"commands that disappeared: {missing}"
    problems = []
    for path, old in v1.items():
        cmd = now[path]
        args = [p for p in cmd.params if isinstance(p, click.Argument)]
        opts = {
            o for p in cmd.params if isinstance(p, click.Option) for o in p.opts + p.secondary_opts
        }
        if len(args) != old["arguments"]:
            problems.append(f"{path}: {len(args)} arguments, was {old['arguments']}")
        lost = sorted(set(old["options"]) - opts)
        if lost:
            problems.append(f"{path}: options removed {lost}")
    assert not problems, problems


def test_tree_has_expected_groups():
    tree = command_tree(app)
    for path in (
        "login",
        "logout",
        "auth whoami",
        "api-keys create",
        "api-keys rotate",
        "nodes join-tokens create",
        "nodes drain",
        "rules dry-run",
        "layouts apply",
        "cache prefetch",
        "metering usage summary",
        "billing reports export",
        "metrics prometheus",
        "logs stream",
        "live events",
        "config set",
        "config spec",
        "pki issue",
    ):
        assert path in tree, path
    assert len(tree) >= 250


def test_every_command_help_renders(capsys):
    """`gpudeck <path> --help` works for the whole tree (catches bad option wiring)."""
    from gpudeck.main import main

    failures = []
    for path in command_tree(app):
        code = main(path.split() + ["--help"])
        out, err = capsys.readouterr()
        if code != 0 or "Usage:" not in out:
            failures.append(f"{path}: exit {code} {err[:200]}")
    assert not failures, failures
