"""
Argon2id PasswordHasher interface.
"""

import pytest
from app.utils.password import PasswordHasher


@pytest.mark.unit
def test_hash_and_verify_roundtrip():
    hashed = PasswordHasher.hash("Admin@GpuDeck2026")
    assert hashed.startswith("$argon2id$")
    assert PasswordHasher.verify("Admin@GpuDeck2026", hashed)
    assert not PasswordHasher.verify("wrong", hashed)


@pytest.mark.unit
def test_verify_never_raises_on_bad_input():
    assert PasswordHasher.verify("x", None) is False
    assert PasswordHasher.verify("x", "") is False
    assert PasswordHasher.verify("x", "$2b$12$not-argon") is False
    assert PasswordHasher.is_usable("$2b$12$not-argon") is False
    assert PasswordHasher.needs_update("garbage") is True
