"""
Unit tests: vault MultiFernet encryption/rotation/masking and the audit
``record`` helper (never raises, fills context, JSON-safe payloads).
"""

import uuid
from datetime import datetime, timezone

import pytest
from app.core.request_context import set_request_actor, set_request_context
from app.core.settings import settings
from app.services.audit import audit
from app.services.audit.service import _json_safe
from app.services.vault import crypto
from cryptography.fernet import Fernet, InvalidToken

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Vault crypto
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip():
    ct = crypto.encrypt_value("hf_secret_token_value")
    assert ct != "hf_secret_token_value"
    assert crypto.decrypt_value(ct) == "hf_secret_token_value"
    assert crypto.key_version_of(ct) == 1
    assert not crypto.needs_rotation(ct)


def test_rotation_with_prepended_key(monkeypatch):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "VAULT_KEYS", old_key)
    ct_old = crypto.encrypt_value("rotate-me-please")

    monkeypatch.setattr(settings, "VAULT_KEYS", f"{new_key},{old_key}")
    assert crypto.decrypt_value(ct_old) == "rotate-me-please"  # old key still decrypts
    assert crypto.key_version_of(ct_old) == 2
    assert crypto.needs_rotation(ct_old)
    ct_new = crypto.rotate_ciphertext(ct_old)
    assert not crypto.needs_rotation(ct_new)

    monkeypatch.setattr(settings, "VAULT_KEYS", new_key)  # old key retired
    assert crypto.decrypt_value(ct_new) == "rotate-me-please"
    with pytest.raises(InvalidToken):
        crypto.decrypt_value(ct_old)
    assert crypto.key_version_of(ct_old) == 0


@pytest.mark.parametrize(
    "value,masked",
    [
        ("sk-abc123def456", "sk-abc...f456"),
        ("short1234", "sho...1234"),
        ("abcd", "****"),
        ("", "****"),
        (None, "****"),
    ],
)
def test_mask_value(value, masked):
    assert crypto.mask_value(value) == masked


def test_settings_reject_invalid_vault_key():
    from app.core.settings.security import SecuritySettings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SecuritySettings(
            SECRET_KEY="x" * 40,
            VAULT_KEYS="not-a-fernet-key",
            ADMIN_PASSWORD="long-enough-1",
        )


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def test_record_fills_actor_and_request_context():
    admin_id = str(uuid.uuid4())
    set_request_context("203.0.113.5", "pytest-agent", str(uuid.uuid4()))
    set_request_actor(f"admin:{admin_id}")
    try:
        with audit.capture() as events:
            audit.record(
                "api_key.revoked",
                resource_type="api_key",
                resource_id=uuid.uuid4(),
                before_state={"status": "active", "at": datetime.now(timezone.utc)},
                after_state={"status": "revoked"},
            )
    finally:
        set_request_actor(None)
        set_request_context(None, None, None)
    assert len(events) == 1
    event = events[0]
    assert event["actor_type"] == "admin" and event["actor_id"] == admin_id
    assert event["ip_address"] == "203.0.113.5"
    assert event["user_agent"] == "pytest-agent"
    assert event["outcome"] == "success"
    assert isinstance(event["before_state"]["at"], str)


def test_record_defaults_to_system_and_normalises_outcome():
    with audit.capture() as events:
        audit.record("vault.keys_rotated", actor_type="martian", outcome="error")
    assert events[0]["actor_type"] == "system"
    assert events[0]["outcome"] == "failure"


def test_record_never_raises():
    def broken_sink(event):
        raise RuntimeError("broker on fire")

    audit.set_sink(broken_sink)
    try:
        audit.record("auth.login")  # must not raise
    finally:
        audit.set_sink(None)

    class Unprintable:
        def __str__(self):
            raise ValueError("nope")

    with audit.capture() as events:
        audit.record("x", metadata={"bad": Unprintable()})  # build failure is swallowed
    assert events == []


def test_json_safe_handles_nested_types():
    uid = uuid.uuid4()
    out = _json_safe({"u": uid, "l": [uid, {1, 2}], "n": None, 1: "int-key"})
    assert out["u"] == str(uid)
    assert out["l"][0] == str(uid)
    assert sorted(out["l"][1]) == [1, 2]
    assert out["1"] == "int-key"
