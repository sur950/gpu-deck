"""
Security settings: secrets, admin seed, sessions, CSRF, lockout, API keys,
vault keys, IP lists, TLS.

Field names follow libs/contracts/env.md; the
older names (LOGIN_LOCKOUT_*, ADMIN_NAME, SERVING_IP_ALLOWLIST)
stay readable through ``AliasChoices`` so an existing .env keeps working.
"""

from __future__ import annotations

from typing import List, Optional

from app.core.settings.base import DomainSettings
from pydantic import AliasChoices, Field, field_validator


class SecuritySettings(DomainSettings):
    """Secrets and auth knobs. Critical values have no default."""

    SECRET_KEY: str = Field(
        ...,
        min_length=32,
        description="Signs session tokens, derives the session Fernet key, keys the CSRF and "
        "admin-seed HMACs (>= 32 chars)",
    )
    # Vault: comma-separated Fernet keys, newest first. The first key encrypts;
    # all keys decrypt (MultiFernet), so rotation is "prepend a new key".
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # noqa: E501
    VAULT_KEYS: str = Field(
        ...,
        description="Comma-separated Fernet keys for the secrets vault, newest first",
    )
    CACHE_TTL_CREDENTIALS_SECONDS: int = Field(
        default=86400, description="Redis TTL of cached vault ciphertext"
    )

    # Seeded admin — the only login. ADMIN_PASSWORD rotation rule: on boot compare
    # HMAC(SECRET_KEY, ADMIN_PASSWORD) with
    # admin_users.env_secret_hash and re-apply the password only when it changed.
    ADMIN_USERNAME: str = Field(default="admin", description="Seeded admin login name")
    ADMIN_PASSWORD: str = Field(..., min_length=8, description="Seeded admin password")
    ADMIN_NAME: str = Field(
        default="Administrator",
        validation_alias=AliasChoices("ADMIN_NAME", "ADMIN_DISPLAY_NAME"),
        description="Seeded admin display name (env.md: ADMIN_DISPLAY_NAME)",
    )

    # Sessions (opaque Fernet token in an HttpOnly cookie, SHA-256 stored)
    SESSION_COOKIE_NAME: str = Field(
        default="gpudeck_session",
        description="Session cookie (admin.yaml: gpudeck_session)",
    )
    SESSION_EXPIRE_HOURS: int = Field(default=12, description="Session TTL in hours (hard expiry)")
    SESSION_IDLE_TIMEOUT_MINUTES: int = Field(
        default=60, description="Sliding idle expiry in minutes (0 = off)"
    )
    SESSION_COOKIE_DOMAIN: Optional[str] = Field(
        None, description="Shared parent domain for the cookie; unset for local dev"
    )
    SESSION_COOKIE_SECURE: Optional[bool] = Field(
        None, description="Force the Secure cookie flag (default: only in production)"
    )

    # CSRF (double-submit cookie + header, HMAC-bound to the session)
    CSRF_COOKIE_NAME: str = Field(default="gpudeck_csrf", description="Readable CSRF cookie")
    CSRF_HEADER_NAME: str = Field(default="X-CSRF-Token", description="CSRF header name")
    CSRF_ENABLED: bool = Field(default=True, description="Enforce CSRF on state-changing requests")

    # Login lockout and password policy
    AUTH_LOCKOUT_ATTEMPTS: int = Field(
        default=5,
        validation_alias=AliasChoices("AUTH_LOCKOUT_ATTEMPTS", "LOGIN_LOCKOUT_ATTEMPTS"),
        description="Failed logins before lockout",
    )
    AUTH_LOCKOUT_MINUTES: int = Field(
        default=15,
        validation_alias=AliasChoices("AUTH_LOCKOUT_MINUTES", "LOGIN_LOCKOUT_MINUTES"),
        description="Lockout duration in minutes",
    )
    AUTH_PASSWORD_MIN_LENGTH: int = Field(
        default=12, description="Minimum length for a UI-set password"
    )
    AUTH_REQUIRE_PASSWORD_CHANGE: bool = Field(
        default=False,
        description="Flag must_change_password while the seed password is in use",
    )
    ARGON2_TIME_COST: int = Field(default=3, description="argon2-cffi time cost")
    ARGON2_MEMORY_KIB: int = Field(default=65536, description="argon2-cffi memory cost (KiB)")
    ARGON2_PARALLELISM: int = Field(default=2, description="argon2-cffi parallelism")

    # API keys issued to clients of the serving API
    API_KEY_PREFIX: str = Field(
        default="gd-",
        description="Prefix of generated API keys (serving.md §3: gd- + 48 hex). Keys issued "
        "under an older prefix keep working: lookup is by SHA-256 of the full key",
    )
    API_KEY_CACHE_TTL_SECONDS: int = Field(
        default=60, description="Redis TTL of verified API keys on the serving API"
    )
    API_KEY_LOCAL_CACHE_SECONDS: float = Field(
        default=1.0,
        description="Process-local cache of verified API keys (bounded by the invalidation "
        "pub/sub); keeps the hot path memory-only",
    )

    # Rate limiting (admin API only; serving quotas belong to the AI gateway in front)
    RATE_LIMIT_ENABLED: bool = Field(default=True, description="Enable slowapi rate limiting")
    RATE_LIMIT_PER_MINUTE: int = Field(default=120, description="Default requests per minute")
    RATE_LIMIT_LOGIN_PER_MINUTE: int = Field(default=10, description="Login attempts per minute")

    # IP allow / block lists (comma-separated IPs or CIDRs; empty = no restriction).
    # They are merged into the ip_rules evaluation as implicit rules
    # (GATEWAY_IP_ALLOWLIST becomes implicit api_layer allows).
    IP_ALLOWLIST: str = Field(default="", description="Admin API implicit allow rules")
    IP_BLOCKLIST: str = Field(default="", description="Admin API implicit block rules")
    GATEWAY_IP_ALLOWLIST: str = Field(
        default="",
        validation_alias=AliasChoices("GATEWAY_IP_ALLOWLIST", "SERVING_IP_ALLOWLIST"),
        description="Serving API implicit allow rules (your AI gateway's IPs)",
    )
    TRUSTED_PROXIES: str = Field(
        default="127.0.0.1/32,::1/128",
        description="Comma-separated IPs/CIDRs of reverse proxies (Caddy, load balancer) whose "
        "X-Forwarded-For / X-Real-IP headers are believed when evaluating IP rules. "
        "Headers from any other peer are ignored so clients cannot spoof their address.",
    )
    IP_RULES_CACHE_TTL_SECONDS: int = Field(
        default=30, description="Redis TTL of the per-scope ip_rules snapshot"
    )
    IP_RULES_LOCAL_CACHE_SECONDS: float = Field(
        default=2.0, description="Process-local snapshot age before re-reading Redis"
    )

    # TLS — certificate paths for the listeners that terminate TLS themselves.
    # Empty means plain HTTP (behind Caddy locally).
    TLS_CERT_FILE: Optional[str] = Field(
        None,
        description="Server certificate (PEM) for admin/serving listeners that terminate TLS",
        validation_alias=AliasChoices("TLS_CERT_FILE", "ADMIN_TLS_CERT_PATH"),
    )
    TLS_KEY_FILE: Optional[str] = Field(
        None,
        description="Server private key (PEM)",
        validation_alias=AliasChoices("TLS_KEY_FILE", "ADMIN_TLS_KEY_PATH"),
    )
    TLS_CA_FILE: Optional[str] = Field(None, description="CA bundle used to verify peers")
    AGENT_HUB_TLS_CERT_FILE: Optional[str] = Field(
        None,
        description="Agent hub server cert (empty = issued by the internal CA)",
        validation_alias=AliasChoices("AGENT_HUB_TLS_CERT_FILE", "AGENT_HUB_TLS_CERT_PATH"),
    )
    AGENT_HUB_TLS_KEY_FILE: Optional[str] = Field(
        None,
        description="Agent hub server key",
        validation_alias=AliasChoices("AGENT_HUB_TLS_KEY_FILE", "AGENT_HUB_TLS_KEY_PATH"),
    )
    AGENT_HUB_CLIENT_CA_FILE: Optional[str] = Field(
        None, description="Internal CA that signs node-agent client certificates (mTLS)"
    )
    PKI_CA_DIR: str = Field(
        default="certs", description="Directory holding the internal CA material (dev)"
    )

    @field_validator("SECRET_KEY", "VAULT_KEYS", "ADMIN_PASSWORD", mode="before")
    @classmethod
    def _reject_placeholder(cls, v: str) -> str:
        if isinstance(v, str) and "change-me" in v.lower():
            raise ValueError("replace the placeholder value from .env.example")
        return v

    @field_validator("VAULT_KEYS")
    @classmethod
    def _validate_vault_keys(cls, v: str) -> str:
        """Fail at boot, not at the first encrypt, when a key is not a Fernet key."""
        from cryptography.fernet import Fernet

        keys = [k.strip() for k in v.split(",") if k.strip()]
        if not keys:
            raise ValueError("VAULT_KEYS must contain at least one Fernet key")
        for key in keys:
            try:
                Fernet(key.encode())
            except Exception as exc:  # noqa: BLE001
                raise ValueError("VAULT_KEYS contains an invalid Fernet key") from exc
        return v

    def get_vault_keys(self) -> List[str]:
        return [k.strip() for k in self.VAULT_KEYS.split(",") if k.strip()]

    def _csv(self, value: str) -> List[str]:
        return [v.strip() for v in value.split(",") if v.strip()]

    def get_ip_allowlist(self) -> List[str]:
        return self._csv(self.IP_ALLOWLIST)

    def get_ip_blocklist(self) -> List[str]:
        return self._csv(self.IP_BLOCKLIST)

    def get_trusted_proxies(self) -> List[str]:
        return self._csv(self.TRUSTED_PROXIES)

    def get_gateway_ip_allowlist(self) -> List[str]:
        return self._csv(self.GATEWAY_IP_ALLOWLIST)

    # Kept for callers written against the skeleton name.
    def get_serving_ip_allowlist(self) -> List[str]:
        return self.get_gateway_ip_allowlist()

    @property
    def session_cookie_secure(self) -> bool:
        if self.SESSION_COOKIE_SECURE is not None:
            return self.SESSION_COOKIE_SECURE
        return getattr(self, "APP_ENV", "development") == "production"

    @property
    def session_cookie_samesite(self) -> str:
        return "strict" if getattr(self, "APP_ENV", "development") == "production" else "lax"
