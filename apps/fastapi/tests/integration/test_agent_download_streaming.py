"""
Agent build downloads stream quickly over a slow link (MON-TEST: the 12 MB agent binary
crawled at ~7 KB/s through ``ssh -R`` while scp of the same link ran ~700 KB/s).

The hub's download response (``agent_downloads.file_response``, with the hub's
middlewares) is served by uvicorn over TLS behind a relay that adds latency and
limits the bytes in flight like a tunnel's channel window. A 12 MiB build must
arrive intact well within the time a window-limited link allows, and a Range request
(curl resume) must work.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = [pytest.mark.integration, pytest.mark.slow]

RTT = 0.06
WINDOW = 1 << 20  # bytes in flight through the "tunnel"
SIZE = 12 * (1 << 20)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cert_files(tmp_path: Path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hub-test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "hub.pem", tmp_path / "hub.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


class _Server(threading.Thread):
    def __init__(self, app, port: int, certfile: str, keyfile: str) -> None:
        super().__init__(daemon=True)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="off",
            access_log=False,
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
        )
        self.server = uvicorn.Server(config)

    def run(self) -> None:
        self.server.run()

    def start_and_wait(self) -> None:
        self.start()
        deadline = time.time() + 30
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=10)


class WindowedRelay:
    """Downstream bytes take RTT/2 to arrive and at most WINDOW bytes are in flight."""

    def __init__(self, upstream_port: int) -> None:
        self.upstream_port = upstream_port
        self.server = None
        self.tasks: set = set()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _up(self, reader, writer) -> None:  # client -> hub: latency only
        try:
            while data := await reader.read(65536):
                await asyncio.sleep(RTT / 2)
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def _down(self, reader, writer) -> None:  # hub -> client: latency + window
        credit = asyncio.Semaphore(WINDOW // 16384)
        pending: set = set()

        async def deliver(data: bytes, units: int) -> None:
            await asyncio.sleep(RTT / 2)
            writer.write(data)
            await writer.drain()
            await asyncio.sleep(RTT / 2)  # the window update travels back
            for _ in range(units):
                credit.release()

        try:
            while True:
                await credit.acquire()
                data = await reader.read(16384)
                if not data:
                    credit.release()
                    break
                task = asyncio.ensure_future(deliver(data, 1))
                pending.add(task)
                task.add_done_callback(pending.discard)
            await asyncio.gather(*pending, return_exceptions=True)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def _handle(self, reader, writer) -> None:
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.upstream_port)
        for coro in (self._up(reader, up_writer), self._down(up_reader, writer)):
            task = asyncio.ensure_future(coro)
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def test_agent_build_streams_fast_over_a_windowed_link(tmp_path):
    from app.middlewares import core_middlewares, request_limits
    from app.services.nodes import agent_downloads as dl
    from fastapi import FastAPI

    blob = os.urandom(SIZE)
    build = tmp_path / "gpudeck-agent"
    build.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    art = dl.Artifact(
        version="9.9.9",
        target="x86_64-unknown-linux-gnu",
        path=build,
        filename="gpudeck-agent",
        size_bytes=SIZE,
        sha256=digest,
        os="linux",
        arch="x86_64",
        built_at=None,
    )
    app = FastAPI()
    core_middlewares.install(app, "hub")
    request_limits.install(app, "hub")

    @app.get("/agent/v1/downloads/{version}/{target}")
    async def download(version: str, target: str):
        return dl.file_response(art)

    assert dl.file_response(art).chunk_size == dl.DOWNLOAD_CHUNK_BYTES >= 1 << 20
    certfile, keyfile = _cert_files(tmp_path)
    port = _free_port()
    server = _Server(app, port, certfile, keyfile)
    server.start_and_wait()
    relay = WindowedRelay(port)
    url = (
        f"https://127.0.0.1:{await relay.start()}/agent/v1/downloads/9.9.9/x86_64-unknown-linux-gnu"
    )
    try:
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            start = time.perf_counter()
            resp = await client.get(url)
            elapsed = time.perf_counter() - start
            assert resp.status_code == 200
            assert hashlib.sha256(resp.content).hexdigest() == digest
            assert resp.headers["x-checksum-sha256"] == digest
            assert int(resp.headers["content-length"]) == SIZE
            # Window-limited ceiling: WINDOW per RTT (~17 MB/s here). The tunnel case crawled at
            # ~7 KB/s; anything near the ceiling proves the hub is not the bottleneck.
            ceiling = SIZE / (WINDOW / RTT)
            print(
                f"\n12 MiB in {elapsed:.2f} s ({SIZE / elapsed / 1e6:.1f} MB/s, "
                f"ceiling {ceiling:.2f} s)"
            )
            assert elapsed < max(6.0, ceiling * 6), elapsed

            ranged = await client.get(url, headers={"Range": f"bytes={SIZE - 1000}-"})
            assert ranged.status_code == 206 and ranged.content == blob[-1000:]
    finally:
        await relay.stop()
        server.stop()
