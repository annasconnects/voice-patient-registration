"""Application settings, loaded from environment variables (never hardcoded)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _normalize_db_url(url: str) -> str:
    """Hosted providers (Railway, Render, Heroku) hand out `postgres://` or
    `postgresql://` URLs. SQLAlchemy needs the driver spelled out for psycopg v3."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _normalize_db_url(
            os.getenv("DATABASE_URL", "sqlite:///./data/patients.db")
        )
    )
    # Shared secret Vapi sends in the `x-vapi-secret` header. If unset, the
    # webhook is open (fine for local dev, set it in production).
    vapi_secret: str | None = field(default_factory=lambda: os.getenv("VAPI_SECRET") or None)
    # Optional API key for write endpoints (POST/PUT/DELETE). Unset = open.
    api_key: str | None = field(default_factory=lambda: os.getenv("API_KEY") or None)
    seed_data: bool = field(
        default_factory=lambda: os.getenv("SEED_DATA", "true").lower() in {"1", "true", "yes"}
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    clinic_timezone: str = field(default_factory=lambda: os.getenv("CLINIC_TIMEZONE", "America/New_York"))


settings = Settings()
