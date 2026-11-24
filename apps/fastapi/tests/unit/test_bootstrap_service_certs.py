"""
``infra/scripts/bootstrap.dev.sh::ensure_service_certs`` re-issues the API-layer /
control-plane client certificates when the internal CA changed (database reset), and keeps
them while the current CA still signs them. The function runs in bash with stubbed helpers
and a fake ``venv/bin/python`` that plays the PKI service with throwaway openssl CAs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from app.core.logger import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "scripts" / "bootstrap.dev.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None or shutil.which("bash") is None,
    reason="needs bash and openssl",
)

FAKE_PYTHON = r"""#!/bin/bash
# stand-in for the venv python: `- <ca_out>` writes the current CA, `- <kind> <dir>` issues.
cat >/dev/null
if [ "$#" -eq 2 ]; then
  cp "$FAKE_CA_DIR/current.crt" "$2"
elif [ "$#" -eq 3 ]; then
  kind="$2"; out="$3"
  openssl req -newkey rsa:2048 -nodes -keyout "$out/$kind.key" -subj "/CN=$kind" \
    -out "$out/$kind.csr" >/dev/null 2>&1
  openssl x509 -req -in "$out/$kind.csr" -CA "$FAKE_CA_DIR/current.crt" \
    -CAkey "$FAKE_CA_DIR/current.key" -CAcreateserial -days 30 -out "$out/$kind.crt" \
    >/dev/null 2>&1
  rm -f "$out/$kind.csr"
  cp "$FAKE_CA_DIR/current.crt" "$out/ca.pem"
  echo "$kind" >> "$FAKE_CA_DIR/issued.log"
fi
"""


def _function_source() -> str:
    text = SCRIPT.read_text()
    match = re.search(r"^ensure_service_certs\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert match, "ensure_service_certs not found in bootstrap.dev.sh"
    return match.group(0)


def _make_ca(where: Path, name: str) -> None:
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "30",
            "-subj",
            f"/CN={name}",
            "-keyout",
            str(where / f"{name}.key"),
            "-out",
            str(where / f"{name}.crt"),
        ],
        check=True,
        capture_output=True,
    )


def _use_ca(ca_dir: Path, name: str) -> None:
    shutil.copy(ca_dir / f"{name}.crt", ca_dir / "current.crt")
    shutil.copy(ca_dir / f"{name}.key", ca_dir / "current.key")


def _run(tmp: Path, ca_dir: Path) -> subprocess.CompletedProcess:
    harness = tmp / "harness.sh"
    harness.write_text(
        "set -e\n"
        'print_step() { :; }\nprint_success() { echo "ok: $1"; }\n'
        'print_error() { echo "error: $1"; }\nprint_info() { echo "info: $1"; }\n'
        "_set_env() { :; }\nload_env() { :; }\n"
        f"RUN_DIR={tmp / 'run'}\nFASTAPI_DIR={tmp}\nVENV={tmp / 'venv'}\n"
        + _function_source()
        + "ensure_service_certs\n"
    )
    env = {**os.environ, "FAKE_CA_DIR": str(ca_dir)}
    return subprocess.run(["bash", str(harness)], capture_output=True, text=True, env=env)


def _issued(ca_dir: Path) -> list:
    log = ca_dir / "issued.log"
    return log.read_text().split() if log.exists() else []


def _verifies(cert: Path, ca: Path) -> bool:
    return (
        subprocess.run(
            ["openssl", "verify", "-CAfile", str(ca), str(cert)], capture_output=True
        ).returncode
        == 0
    )


def test_service_certs_reissued_after_ca_change(tmp_path):
    ca_dir = tmp_path / "ca"
    ca_dir.mkdir()
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    python = tmp_path / "venv" / "bin" / "python"
    python.write_text(FAKE_PYTHON)
    python.chmod(0o755)
    _make_ca(ca_dir, "old")
    _make_ca(ca_dir, "new")
    certs = tmp_path / "run" / "certs"

    _use_ca(ca_dir, "old")
    first = _run(tmp_path, ca_dir)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _issued(ca_dir) == ["api_layer", "control_plane"]

    # Same CA: the certificates are kept.
    again = _run(tmp_path, ca_dir)
    assert again.returncode == 0, again.stdout + again.stderr
    assert _issued(ca_dir) == ["api_layer", "control_plane"]
    assert "certificate present" in again.stdout

    # Database reset = new CA: both are re-issued and chain to it.
    _use_ca(ca_dir, "new")
    reset = _run(tmp_path, ca_dir)
    assert reset.returncode == 0, reset.stdout + reset.stderr
    assert "issued by another CA" in reset.stdout
    assert _issued(ca_dir) == ["api_layer", "control_plane"] * 2
    for kind in ("api_layer", "control_plane"):
        assert _verifies(certs / f"{kind}.crt", ca_dir / "new.crt")
        assert not _verifies(certs / f"{kind}.crt", ca_dir / "old.crt")
    assert not (certs / ".current-ca.pem").exists()
