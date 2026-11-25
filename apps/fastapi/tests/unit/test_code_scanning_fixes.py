"""
Regression tests for the CodeQL code-scanning fixes: agent download path confinement,
no exception text in 500 responses, and linear-time relative-time parsing.
"""

import time

import pytest
from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.settings import settings
from app.utils.api_wrapper import INTERNAL_ERROR_MESSAGE, handle_exceptions
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

TARGET = "aarch64-apple-darwin"

# ------------------------------------------------------------ path injection


@pytest.fixture
def dl(tmp_path, monkeypatch):
    from app.services.nodes import agent_downloads as mod

    dist = tmp_path / "dist"
    good = dist / "1.0.0" / TARGET
    good.mkdir(parents=True)
    (good / "gpudeck-agent").write_bytes(b"binary 1.0.0")
    monkeypatch.setattr(mod.download_settings, "AGENT_DIST_DIR", str(dist))
    monkeypatch.setattr(mod.download_settings, "AGENT_CACHE_DIR", str(tmp_path / "cache"))
    return mod


@pytest.mark.unit
@pytest.mark.parametrize(
    "version,target",
    [
        ("..", TARGET),
        ("../etc", TARGET),
        ("1.0.0", "../../etc"),
        ("1.0.0\n", TARGET),  # `$` alone would accept a trailing newline
        ("1.0.0/..", TARGET),
        ("/etc", TARGET),
        (".hidden", TARGET),
        ("1.0.0", "a\x00b"),
        ("x" * 200, TARGET),
    ],
)
def test_resolve_rejects_traversal_and_odd_names(dl, version, target):
    with pytest.raises(ValidationError):
        dl.resolve(version, target)


@pytest.mark.unit
def test_confined_keeps_paths_inside_root(dl, tmp_path):
    root = tmp_path / "dist"
    inside = dl._confined(root, "1.0.0", TARGET)
    assert inside.is_relative_to(root.resolve())
    for bad in ("..", ".", "a/b", "", "a\\..\\b"):
        with pytest.raises(ValidationError):
            dl._confined(root, bad)


@pytest.mark.unit
def test_symlink_escaping_the_dist_dir_is_refused(dl, tmp_path):
    outside = tmp_path / "outside" / TARGET
    outside.mkdir(parents=True)
    (outside / "gpudeck-agent").write_bytes(b"not ours")
    (tmp_path / "dist" / "6.6.6").symlink_to(tmp_path / "outside", target_is_directory=True)

    assert [a.version for a in dl.list_artifacts()] == ["1.0.0"]  # skipped, listing still works
    with pytest.raises(ValidationError):
        dl.resolve("6.6.6", TARGET)
    assert dl.resolve("1.0.0", TARGET).path.read_bytes() == b"binary 1.0.0"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_release_build_rejects_bad_names_before_any_io(dl, monkeypatch):
    monkeypatch.setattr(dl.download_settings, "AGENT_RELEASE_URL", "https://example.invalid")

    async def _never(version):  # pragma: no cover - must not be reached
        raise AssertionError("network touched")

    monkeypatch.setattr(dl, "release_checksums", _never)
    with pytest.raises(ValidationError):
        await dl.fetch_release_build("../1.0.0", TARGET)
    with pytest.raises(ResourceNotFoundError):
        await dl.fetch_release_build("1.0.0", "riscv64gc-unknown-linux-gnu")


# ------------------------------------------------------- stack-trace exposure


@pytest.mark.unit
@pytest.mark.parametrize("debug", [False, True])
def test_unhandled_exception_text_never_reaches_the_client(monkeypatch, debug):
    monkeypatch.setattr(settings, "DEBUG", debug)
    app = FastAPI()
    router = APIRouter()

    @router.get("/boom")
    @handle_exceptions
    async def boom():
        raise RuntimeError("password=hunter2 at /srv/app/secret.py line 7")

    app.include_router(router)
    r = TestClient(app).get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == INTERNAL_ERROR_MESSAGE
    assert "hunter2" not in r.text and "secret.py" not in r.text
    assert body["error"]["details"] == ({"exception_type": "RuntimeError"} if debug else None)


# ------------------------------------------------------------------- ReDoS


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,seconds",
    [
        ("-15m", 900),
        ("now-1h", 3600),
        ("now - 2 d", 172800),
        ("  -30s  ", 30),
        ("now-", None),
        ("-m", None),
        ("-1x", None),
        ("now+1h", None),
        ("-１m", None),  # non-ASCII digit
        ("2026-01-01T00:00:00Z", None),
    ],
)
def test_parse_relative(raw, seconds):
    from app.services.metrics.service import parse_relative

    assert parse_relative(raw) == seconds


@pytest.mark.unit
def test_parse_time_is_linear_on_adversarial_input():
    from app.services.metrics.service import parse_relative, parse_time

    n = 200_000
    payloads = ["now" + " " * n + "x", "-" + " " * n + "1", "-1" + " " * n + "x", " " * n]
    started = time.perf_counter()
    for raw in payloads:
        assert parse_relative(raw) is None
        with pytest.raises(ValidationError):
            parse_time(raw + "!", default=None)
    assert time.perf_counter() - started < 1.0
