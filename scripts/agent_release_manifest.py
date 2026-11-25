#!/usr/bin/env python3
"""
Signed node-agent release manifest for agent self-update (agent protocol §14).

    # once, on a trusted machine (never in CI, never committed):
    python3 scripts/agent_release_manifest.py keygen --out agent-release-signing.key
    #   prints the public key: set it as AGENT_RELEASE_PUBLIC_KEYS on control planes (and as
    #   the repository variable AGENT_RELEASE_PUBLIC_KEY), store the private key file's content
    #   as the repository secret AGENT_RELEASE_SIGNING_KEY, then delete the file.

    # release workflow (publish job), over the flat release files:
    python3 scripts/agent_release_manifest.py manifest DIR VERSION
    python3 scripts/agent_release_manifest.py sign DIR/gpudeck-agent-VERSION-manifest.json \\
        [--expect-public-key KEY]                 # key from env AGENT_RELEASE_SIGNING_KEY
    python3 scripts/agent_release_manifest.py verify FILE --public-key KEY

``manifest`` reads every ``gpudeck-agent-<VERSION>-<target>.tar.gz`` in DIR and writes
``gpudeck-agent-<VERSION>-manifest.json``: per target the archive (name, size, sha256) and the
``gpudeck-agent`` binary inside it (sha256, size; what nodes stage). ``sign`` writes
``<manifest>.sig``: the standard base64 of the raw 64-byte ed25519 signature over the exact file
bytes. The signing key is the standard base64 of the 32-byte ed25519 private seed (or a PKCS#8
PEM); public keys are the standard base64 of the 32-byte raw public key.

``manifest`` and ``verify`` need only the standard library plus ``cryptography`` for verify;
``keygen`` and ``sign`` need ``cryptography``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE = re.compile(r"^gpudeck-agent-(?P<version>.+)-(?P<target>(?:x86_64|aarch64)-[a-z0-9_-]+)\.tar\.gz$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _binary(archive: Path):
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and os.path.basename(member.name) == "gpudeck-agent":
                src = tar.extractfile(member)
                assert src is not None
                h = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk)
                    size += len(chunk)
                return h.hexdigest(), size
    raise SystemExit(f"{archive.name}: no gpudeck-agent binary inside")


def cmd_manifest(args) -> int:
    folder = Path(args.dir)
    artifacts = []
    for path in sorted(folder.iterdir()):
        m = ARCHIVE.match(path.name)
        if not path.is_file() or not m or m["version"] != args.version:
            continue
        binary_sha, binary_size = _binary(path)
        artifacts.append(
            {
                "target": m["target"],
                "file": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "binary_sha256": binary_sha,
                "binary_size_bytes": binary_size,
            }
        )
    if not artifacts:
        raise SystemExit(f"no gpudeck-agent-{args.version}-<target>.tar.gz in {folder}")
    doc = {
        "schema": 1,
        "product": "gpudeck-agent",
        "version": args.version,
        "protocol_version": 1,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": artifacts,
    }
    out = folder / f"gpudeck-agent-{args.version}-manifest.json"
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out} ({len(artifacts)} target(s))")
    return 0


def _private_key(raw: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = raw.strip()
    if raw.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(raw.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SystemExit("the signing key is not an ed25519 key")
        return key
    seed = base64.b64decode(raw, validate=True)
    if len(seed) != 32:
        raise SystemExit("the signing key must be base64 of a 32-byte ed25519 seed (or PEM)")
    return Ed25519PrivateKey.from_private_bytes(seed)


def _public_b64(key) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def cmd_keygen(args) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} exists; refusing to overwrite a key")
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(base64.b64encode(seed).decode() + "\n")
    print(f"private key written to {out} (0600): store it as the AGENT_RELEASE_SIGNING_KEY secret")
    print("and delete the file. Public key (AGENT_RELEASE_PUBLIC_KEYS on control planes):")
    print(_public_b64(key))
    return 0


def cmd_sign(args) -> int:
    raw = os.environ.get(args.key_env, "")
    if not raw.strip():
        raise SystemExit(f"{args.key_env} is empty")
    key = _private_key(raw)
    public = _public_b64(key)
    if args.expect_public_key and args.expect_public_key.strip() != public:
        raise SystemExit("the signing key does not match the expected public key")
    path = Path(args.manifest)
    data = path.read_bytes()
    json.loads(data)  # a manifest, not something else
    sig = base64.b64encode(key.sign(data)).decode()
    Path(f"{path}.sig").write_text(sig + "\n")
    print(f"signed {path.name} with key {public}")
    return 0


def cmd_verify(args) -> int:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    path = Path(args.manifest)
    data = path.read_bytes()
    sig = base64.b64decode("".join(Path(f"{path}.sig").read_text().split()))
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(args.public_key)).verify(sig, data)
    except InvalidSignature:
        print(f"{path.name}: signature does NOT verify", file=sys.stderr)
        return 1
    print(f"{path.name}: signature OK")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest", help="write gpudeck-agent-VERSION-manifest.json")
    m.add_argument("dir")
    m.add_argument("version")
    m.set_defaults(func=cmd_manifest)
    k = sub.add_parser("keygen", help="create a release signing key (keep it out of the repo)")
    k.add_argument("--out", required=True)
    k.set_defaults(func=cmd_keygen)
    s = sub.add_parser("sign", help="write <manifest>.sig")
    s.add_argument("manifest")
    s.add_argument("--key-env", default="AGENT_RELEASE_SIGNING_KEY")
    s.add_argument("--expect-public-key", default="")
    s.set_defaults(func=cmd_sign)
    v = sub.add_parser("verify", help="check <manifest>.sig")
    v.add_argument("manifest")
    v.add_argument("--public-key", required=True)
    v.set_defaults(func=cmd_verify)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
