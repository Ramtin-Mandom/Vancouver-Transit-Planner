"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "extracted"


class ConfigurationError(ValueError):
    """Raised when required database configuration is missing or invalid."""


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection settings for PostgreSQL."""

    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_environment(cls) -> DatabaseConfig:
        """Load a validated configuration without exposing secret values."""
        load_dotenv(PROJECT_ROOT / ".env")
        names = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
        values = {name: os.getenv(name) for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigurationError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        try:
            port = int(values["DB_PORT"] or "")
        except ValueError as exc:
            raise ConfigurationError("DB_PORT must be an integer") from exc
        return cls(
            host=values["DB_HOST"] or "",
            port=port,
            dbname=values["DB_NAME"] or "",
            user=values["DB_USER"] or "",
            password=values["DB_PASSWORD"] or "",
        )

    def connection_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by psycopg.connect."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
        }
