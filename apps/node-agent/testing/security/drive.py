"""
Control-plane side of the security verification (run.sh): drives the admin API, the serving API
and the PKI of a local control plane (the dev stack). Run with the backend venv from
apps/fastapi:

    venv/bin/python ../node-agent/testing/security/drive.py <command> [args]

Commands print one JSON document on stdout. The admin password is read from apps/fastapi/.env
(ADMIN_USERNAME / ADMIN_PASSWORD) and never printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

ADMIN = os.environ.get("GPUDECK_ADMIN_URL", "http://localhost:8180/api/v1")
SERVING = os.environ.get("GPUDECK_SERVING_URL", "http://localhost:8181")
STATE = Path(os.environ.get("SECTEST_STATE", "/tmp/gpudeck-sectest/state.json"))


def _env() -> Dict[str, str]:
    out = {}
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _state() -> Dict[str, Any]:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save(st: Dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def admin() -> httpx.Client:
    env = _env()
    c = httpx.Client(base_url=ADMIN, timeout=60)
    r = c.post(
        "/auth/login",
        json={"username": env.get("ADMIN_USERNAME", "admin"), "password": env["ADMIN_PASSWORD"]},
    )
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = r.json()["data"]["csrf_token"]
    return c


def data(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url}: {r.status_code} {r.text[:500]}")
    return r.json().get("data") if r.content else None


def setup(proxy_cidrs: str) -> None:
    c = admin()
    tag = str(int(time.time()))
    cluster = data(
        c.post(
            "/clusters",
            json={
                "name": f"sectest-{tag}",
                "type": "bare_metal",
                "config": {
                    "node_security": {
                        "proxy_allowed_source_cidrs": [s for s in proxy_cidrs.split(",") if s],
                        "ssh_default_allowed_cidrs": [],
                    }
                },
            },
        )
    )
    tokens = {}
    for key in ("tunnel", "direct"):
        t = data(c.post("/nodes/join-tokens", json={"cluster_id": cluster["id"]}))
        tokens[key] = {
            "token": t["token"],
            "install_command": t["install_command"],
            "ca_fingerprint": t["ca_fingerprint_sha256"],
        }
    st = {"cluster_id": cluster["id"], "tokens": tokens, "deployments": [], "models": []}
    _save(st)
    print(json.dumps({"cluster_id": cluster["id"], "tokens": tokens}))


def wait_nodes(timeout: float = 180) -> None:
    c = admin()
    st = _state()
    deadline = time.time() + timeout
    while True:
        nodes = data(c.get("/nodes", params={"cluster_id": st["cluster_id"], "page_size": 50}))
        online = {n["name"]: n for n in nodes if n["status"] == "online"}
        if len(online) >= 2:
            break
        if time.time() > deadline:
            raise SystemExit(f"nodes not online: {[(n['name'], n['status']) for n in nodes]}")
        time.sleep(3)
    out = {
        name: {
            "id": n["id"],
            "serving_mode": (n.get("metadata") or {}).get("serving_mode"),
            "proxy_address": n.get("proxy_address"),
            "proxy_bind": (n.get("metadata") or {}).get("proxy_bind"),
        }
        for name, n in online.items()
    }
    st["nodes"] = out
    _save(st)
    print(json.dumps(out))


def deploy(node_name: str, model_dir: str, model_name: str) -> None:
    c = admin()
    st = _state()
    node = st["nodes"][node_name]
    cands = data(c.post("/catalog/local/scan", json={"path": model_dir, "node_id": node["id"]}))
    gguf = [x for x in cands if x["format"] == "gguf"]
    if not gguf:
        raise SystemExit(f"no GGUF candidate under {model_dir} on {node_name}: {cands}")
    cand = sorted(gguf, key=lambda x: x["total_size_bytes"])[0]
    model = data(
        c.post(
            "/models",
            json={
                "name": model_name,
                "source": "local_path",
                "repo_id": cand["path"],
                "type": "chat",
                "priority": "normal",
                "metadata": {"node_id": node["id"]},
                "initial_version": {
                    "version": "main",
                    "is_default": True,
                    "files": [],
                    "local_path": cand["path"],
                },
            },
        )
    )
    st["models"].append(model["id"])
    _save(st)
    dep = data(
        c.post(
            "/deployments",
            json={
                "name": model_name,
                "model_id": model["id"],
                "cluster_id": st["cluster_id"],
                "replicas": 1,
                "context_size": 2048,
            },
        )
    )
    st["deployments"].append(dep["id"])
    _save(st)
    deadline = time.time() + 240
    while True:
        insts = data(c.get(f"/deployments/{dep['id']}/instances"))
        if any(i["state"] == "ready" for i in insts):
            break
        if time.time() > deadline:
            raise SystemExit(f"{model_name}: instance not ready: {insts}")
        time.sleep(3)
    print(json.dumps({"model": model_name, "deployment": dep["id"], "node": node_name}))


def api_key() -> None:
    c = admin()
    st = _state()
    k = data(c.post("/api-keys", json={"name": f"sectest-{int(time.time())}"}))
    st["api_key_id"] = k.get("id") or (k.get("api_key") or {}).get("id")
    st["api_key"] = k.get("key") or k.get("token") or k.get("plaintext")
    _save(st)
    print(json.dumps({"api_key_id": st["api_key_id"]}))


async def _chat(model: str, n: int) -> Dict[str, Any]:
    st = _state()
    ttfb: List[float] = []
    async with httpx.AsyncClient(base_url=SERVING, timeout=60) as c:
        hdr = {"Authorization": f"Bearer {st['api_key']}"}
        body = {
            "model": model,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }
        last = ""
        for _ in range(n):
            t0 = time.perf_counter()
            first = None
            async with c.stream("POST", "/v1/chat/completions", json=body, headers=hdr) as r:
                status = r.status_code
                node = r.headers.get("x-gpudeck-node")
                async for chunk in r.aiter_bytes():
                    if chunk and first is None:
                        first = time.perf_counter() - t0
                    last = chunk.decode(errors="replace")[-80:]
            if status != 200:
                return {"model": model, "status": status, "body": last}
            ttfb.append((first or 0) * 1000)
    ttfb.sort()
    return {
        "model": model,
        "status": 200,
        "requests": n,
        "ttfb_p50_ms": round(ttfb[len(ttfb) // 2], 2),
        "ttfb_p95_ms": round(ttfb[int(len(ttfb) * 0.95) - 1], 2),
        "served_by_node": node,
        "tail": last.strip()[-40:],
    }


def chat(model: str, n: str = "20") -> None:
    print(json.dumps(asyncio.run(_chat(model, int(n)))))


def health() -> None:
    r = httpx.get(f"{SERVING}/health", timeout=10).json()
    print(json.dumps(r["checks"].get("agent_tunnels")))


def issue_service_cert(out_dir: str) -> None:
    """
    Valid GPU Deck service certificates that are NOT the pinned identities: a control-plane
    client certificate (for the direct proxy) and a hub-type server certificate for
    ``localhost`` with a fresh key (to impersonate the hub / API layer towards an agent).
    """
    sys.path.insert(0, ".")
    from app.db.postgres.connection import get_session_maker
    from app.services.pki.service import PkiService

    async def run():
        async with get_session_maker()() as s:
            pki = PkiService.from_session(s)
            cp = await pki.issue_service_certificate("control_plane")
            hub = await pki.issue_service_certificate("agent_hub", sans=["localhost"])
            await s.commit()
        return cp, hub

    cp, hub = asyncio.run(run())
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, issued in (("cp", cp), ("fakehub", hub)):
        (d / f"{name}.crt").write_text(issued["certificate_pem"])
        (d / f"{name}.key").write_text(issued["private_key_pem"])
    (d / "ca.pem").write_text(cp["ca_bundle_pem"])
    st = _state()
    st["service_cert_serials"] = [cp["certificate"].serial, hub["certificate"].serial]
    _save(st)
    print(json.dumps({"serials": st["service_cert_serials"]}))


def cleanup() -> None:
    st = _state()
    if not st:
        print("{}")
        return
    c = admin()
    for dep in st.get("deployments", []):
        c.delete(f"/deployments/{dep}")
    time.sleep(3)
    for model in st.get("models", []):
        c.delete(f"/models/{model}")
    for node in (st.get("nodes") or {}).values():
        c.delete(f"/nodes/{node['id']}", params={"force": "true"})
    if st.get("api_key_id"):
        c.delete(f"/api-keys/{st['api_key_id']}")
    if st.get("cluster_id"):
        time.sleep(2)
        c.delete(f"/clusters/{st['cluster_id']}")
    serials = st.get("service_cert_serials") or []
    if serials:
        # The test-only service certificates: revoke them (they stay listed as revoked).
        certs = data(c.get("/pki/certificates", params={"page_size": 200})) or []
        for cert in certs:
            if cert.get("serial") in serials and not cert.get("revoked_at"):
                c.post(f"/pki/certificates/{cert['id']}/revoke", json={"reason": "sectest"})
    print(json.dumps({"cleaned": True}))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {
        "setup": setup,
        "wait-nodes": wait_nodes,
        "deploy": deploy,
        "api-key": api_key,
        "chat": chat,
        "health": health,
        "issue-service-cert": issue_service_cert,
        "cleanup": cleanup,
    }[cmd](*args)
