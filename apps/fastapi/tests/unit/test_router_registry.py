"""
Router auto-discovery: modules exposing `router` are found, tags derive from
module names, and package-level PREFIX applies to serving sub-packages.
"""

import sys
import textwrap
from pathlib import Path

import pytest
from app.api.router_registry import (
    discover_routers,
    include_discovered_routers,
    tags_from_module_name,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def fake_routes_package(tmp_path: Path, monkeypatch):
    """A throwaway package tree mimicking app/api/serving/<format>/<module>.py."""
    root = tmp_path / "fakeapi"
    (root / "openai").mkdir(parents=True)
    (root / "ollama").mkdir()
    (root / "__init__.py").write_text("")
    (root / "openai" / "__init__.py").write_text('PREFIX = "/v1"\n')
    (root / "ollama" / "__init__.py").write_text('PREFIX = "/api"\n')
    (root / "openai" / "chat.py").write_text(textwrap.dedent("""
            from fastapi import APIRouter
            router = APIRouter()
            TAGS = ["Chat"]

            @router.post("/chat/completions")
            async def chat():
                return {"ok": True}
            """))
    (root / "openai" / "_private.py").write_text("router = 'ignored'\n")
    (root / "openai" / "helpers.py").write_text("x = 1\n")
    (root / "ollama" / "tags.py").write_text(textwrap.dedent("""
            from fastapi import APIRouter
            router = APIRouter()
            PREFIX = "/custom"

            @router.get("/tags")
            async def tags():
                return {"models": []}
            """))
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "fakeapi"
    for name in list(sys.modules):
        if name == "fakeapi" or name.startswith("fakeapi."):
            del sys.modules[name]


@pytest.mark.unit
def test_tags_from_module_name():
    assert tags_from_module_name("app.api.v1.routes.api_keys") == ["API Keys"]
    assert tags_from_module_name("agent_hub") == ["Agent Hub"]
    assert tags_from_module_name("nodes") == ["Nodes"]


@pytest.mark.unit
def test_discovers_routers_with_package_and_module_prefixes(fake_routes_package):
    found = discover_routers(fake_routes_package)
    by_name = {item.module_name: item for item in found}
    assert set(by_name) == {"fakeapi.openai.chat", "fakeapi.ollama.tags"}
    assert by_name["fakeapi.openai.chat"].prefix == "/v1"  # from openai/__init__.py
    assert by_name["fakeapi.openai.chat"].tags == ["Chat"]
    assert by_name["fakeapi.ollama.tags"].prefix == "/custom"  # module override wins
    assert by_name["fakeapi.ollama.tags"].tags == ["Tags"]


@pytest.mark.unit
def test_include_mounts_routes_on_app(fake_routes_package):
    app = FastAPI()
    include_discovered_routers(app, fake_routes_package)
    client = TestClient(app)
    assert client.post("/v1/chat/completions").json() == {"ok": True}
    assert client.get("/custom/tags").json() == {"models": []}
    assert client.get("/v1/tags").status_code == 404


@pytest.mark.unit
def test_admin_routes_are_discovered_from_real_package():
    found = discover_routers("app.api.v1.routes", default_prefix="/api/v1")
    names = {item.module_name for item in found}
    assert "app.api.v1.routes.health" in names
    assert all(item.prefix == "/api/v1" for item in found)
