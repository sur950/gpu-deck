"""
PKI developer CLI::

    python -m app.services.pki.cli ca                       # print CA cert + fingerprint
    python -m app.services.pki.cli ca --out certs/          # also write ca.pem
    python -m app.services.pki.cli issue --kind api_layer --out certs/api-layer
    python -m app.services.pki.cli issue --kind control_plane --out certs/control-plane
    python -m app.services.pki.cli issue --kind agent_hub --san hub.local --out certs/hub
    python -m app.services.pki.cli crl
    python -m app.services.pki.cli pins                     # key pins agents receive
    python -m app.services.pki.cli rotate-key --service api-layer   # or agent-hub

Uses the same vault-backed CA as the running control plane (creates it on
first use), so dev certs issued here are trusted by the hub and the node
proxies. Writes ``<out>.crt``, ``<out>.key`` (0600) and ``<out>-ca.pem``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from app.core.logger import get_logger
from app.db.postgres.connection import close_db, get_session_maker
from app.services.pki.service import PkiService

logger = get_logger(__name__)


def _write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, mode)


async def _cmd_ca(args: argparse.Namespace) -> int:
    async with get_session_maker()() as session:
        service = PkiService.from_session(session)
        info = await service.ca_certificate()
        await session.commit()
    print(f"subject:     {info['subject']}")
    print(f"fingerprint: {info['fingerprint_sha256']}")
    print(f"not_after:   {info['not_after'].isoformat()}")
    if args.out:
        path = Path(args.out) / "ca.pem"
        _write(path, info["certificate_pem"])
        print(f"written:     {path}")
    else:
        print(info["certificate_pem"])
    return 0


async def _cmd_issue(args: argparse.Namespace) -> int:
    async with get_session_maker()() as session:
        service = PkiService.from_session(session)
        issued = await service.issue_service_certificate(
            args.kind, common_name=args.cn, sans=args.san or [], validity_days=args.days
        )
        await session.commit()
    base = Path(args.out)
    _write(base.with_suffix(".crt"), issued["certificate_pem"])
    _write(base.with_suffix(".key"), issued["private_key_pem"], 0o600)
    _write(base.parent / f"{base.name}-ca.pem", issued["ca_bundle_pem"])
    cert = issued["certificate"]
    print(f"issued {args.kind} cert serial={cert.serial} subject={cert.subject}")
    print(f"  cert: {base.with_suffix('.crt')}")
    print(f"  key:  {base.with_suffix('.key')}")
    print(f"  ca:   {base.parent / f'{base.name}-ca.pem'}")
    return 0


async def _cmd_crl(args: argparse.Namespace) -> int:
    async with get_session_maker()() as session:
        service = PkiService.from_session(session)
        crl = await service.crl(with_pem=True)
        await session.commit()
    print(f"generated_at: {crl['generated_at'].isoformat()}")
    print(f"serials:      {', '.join(crl['serials']) or '(none)'}")
    if args.out:
        _write(Path(args.out), crl["crl_pem"] or "")
        print(f"written:      {args.out}")
    return 0


_SERVICES = {
    "agent-hub": ("agent-hub-tls", "agent_hub"),
    "api-layer": ("api-layer-tls", "api_layer"),
}


async def _cmd_pins(args: argparse.Namespace) -> int:
    async with get_session_maker()() as session:
        service = PkiService.from_session(session)
        hub = await service.hub_pins()
        api = await service.api_layer_pins()
        await session.commit()
    print("agent hub (SPKI sha256, current / next / previous):")
    for p in hub:
        print(f"  {p}")
    print("API layer:")
    for p in api:
        print(f"  {p}")
    return 0


async def _cmd_rotate_key(args: argparse.Namespace) -> int:
    secret, kind = _SERVICES[args.service]
    async with get_session_maker()() as session:
        service = PkiService.from_session(session)
        data = await service.rotate_service_key(secret, kind)
        await session.commit()
    from app.services.agent_hub.service import AgentHub

    await AgentHub.push_config(None, full=False, sections=["security"])
    print(f"{args.service}: next key promoted (previous key {data['previous_spki'][:16]}… stays")
    print("pinned for SERVICE_KEY_ROTATION_GRACE_HOURS); agents received the new policy.")
    print(f"Restart the {args.service} process(es) to serve with the new key.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.services.pki.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ca = sub.add_parser("ca", help="show (and optionally write) the internal CA certificate")
    ca.add_argument("--out", help="directory to write ca.pem into")
    ca.set_defaults(func=_cmd_ca)
    issue = sub.add_parser("issue", help="issue a service certificate")
    issue.add_argument("--kind", required=True, choices=["api_layer", "control_plane", "agent_hub"])
    issue.add_argument("--cn", help="common name (default per kind)")
    issue.add_argument("--san", action="append", help="DNS name or IP (repeatable)")
    issue.add_argument("--days", type=int, default=None, help="validity days")
    issue.add_argument("--out", required=True, help="output path without extension")
    issue.set_defaults(func=_cmd_issue)
    crl = sub.add_parser("crl", help="print the current revocation list")
    crl.add_argument("--out", help="write the X.509 CRL PEM here")
    crl.set_defaults(func=_cmd_crl)
    pins = sub.add_parser("pins", help="show the hub / API-layer key pins agents receive")
    pins.set_defaults(func=_cmd_pins)
    rot = sub.add_parser(
        "rotate-key", help="promote the pre-published next key of the hub or the API layer"
    )
    rot.add_argument("--service", required=True, choices=sorted(_SERVICES))
    rot.set_defaults(func=_cmd_rotate_key)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    async def _run() -> int:
        try:
            return await args.func(args)
        finally:
            await close_db()

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
