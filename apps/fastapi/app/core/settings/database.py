"""
PostgreSQL connection and pool settings.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class DatabaseSettings(DomainSettings):
    """Postgres connection (asyncpg for the apps, psycopg for Alembic/sqlacodegen)."""

    POSTGRES_HOST: str = Field(default="127.0.0.1", description="PostgreSQL host")
    POSTGRES_PORT: int = Field(default=5432, description="PostgreSQL port")
    POSTGRES_USER: str = Field(default="gpu_deck", description="PostgreSQL user")
    POSTGRES_PASSWORD: str = Field(default="", description="PostgreSQL password")
    POSTGRES_DB: str = Field(default="gpu_deck_dev", description="PostgreSQL database")
    POSTGRES_SCHEMA: str = Field(default="public", description="PostgreSQL schema")
    # When DATABASE_URL is set it wins over POSTGRES_* (bootstrap.dev.sh writes both).
    DATABASE_URL: Optional[str] = Field(
        None, description="Full database URL (postgresql:// or postgresql+asyncpg://)"
    )

    # TLS to Postgres. Applied to both URLs unless DATABASE_URL already carries
    # ssl/sslmode. asyncpg gets ?ssl=<mode>, psycopg ?sslmode=<mode>&sslrootcert=.
    POSTGRES_SSLMODE: Optional[str] = Field(
        None,
        description="disable | allow | prefer | require | verify-ca | verify-full "
        "(empty = driver default, prefer); verify-full in production",
    )
    POSTGRES_SSLROOTCERT: Optional[str] = Field(
        None,
        description="CA bundle for verify-ca / verify-full (exported as PGSSLROOTCERT)",
    )

    # Pool — PER API PROCESS. Keep WORKERS x (POOL_SIZE + MAX_OVERFLOW) plus one
    # connection per running Celery task under Postgres max_connections.
    DB_POOL_SIZE: int = Field(default=5, description="Connections kept open per API process")
    DB_MAX_OVERFLOW: int = Field(default=10, description="Extra connections per process under load")
    DB_POOL_TIMEOUT: int = Field(
        default=60, description="Seconds a request waits for a free pooled connection"
    )
    DB_POOL_PRE_PING: bool = Field(default=True, description="Enable pool pre-ping")
    DB_POOL_RECYCLE: int = Field(default=3600, description="Connection recycle time in seconds")
    DB_ECHO: bool = Field(
        default=False,
        description="Log every SQL statement (very verbose; debugging only)",
    )
    # Postgres session-level safety nets, enforced by Postgres itself on every
    # connection the app opens (asyncpg server_settings). 0 disables.
    DB_IDLE_IN_TRANSACTION_TIMEOUT_MS: int = Field(
        default=30_000,
        description="Postgres idle_in_transaction_session_timeout in ms (0 disables)",
    )
    DB_STATEMENT_TIMEOUT_MS: int = Field(
        default=60_000, description="Postgres statement_timeout in ms (0 disables)"
    )
    DB_IDLE_SESSION_TIMEOUT_MS: int = Field(
        default=300_000,
        description="Postgres idle_session_timeout in ms, applied on PG 14+ (0 leaves it unset)",
    )

    def get_database_url(self) -> str:
        """Async (asyncpg) URL for the apps."""
        if self.DATABASE_URL:
            # A bare postgresql:// DSN would make SQLAlchemy pick psycopg2 (not
            # installed). Only normalize the bare scheme; leave an explicit driver alone.
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        else:
            url = (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return self._with_ssl(url, "ssl")

    def _with_ssl(self, url: str, param: str) -> str:
        if not self.POSTGRES_SSLMODE:
            return url
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        if "ssl" in parsed.query or "sslmode" in parsed.query:
            return url
        query = {param: self.POSTGRES_SSLMODE}
        if self.POSTGRES_SSLROOTCERT:
            import os

            # asyncpg reads the root CA from the environment (no URL parameter).
            os.environ.setdefault("PGSSLROOTCERT", self.POSTGRES_SSLROOTCERT)
            if param == "sslmode":
                query["sslrootcert"] = self.POSTGRES_SSLROOTCERT
        return parsed.update_query_dict(query).render_as_string(hide_password=False)

    def get_sync_database_url(self) -> str:
        """Sync (psycopg) URL for Alembic and sqlacodegen — the same database."""
        from sqlalchemy.engine import make_url

        url = make_url(self.get_database_url()).set(drivername="postgresql+psycopg")
        if "ssl" in url.query:  # asyncpg spelling -> libpq spelling
            mode = url.query["ssl"]
            url = url.difference_update_query(["ssl"]).update_query_dict({"sslmode": mode})
            if self.POSTGRES_SSLROOTCERT and "sslrootcert" not in url.query:
                url = url.update_query_dict({"sslrootcert": self.POSTGRES_SSLROOTCERT})
        return url.render_as_string(hide_password=False)
