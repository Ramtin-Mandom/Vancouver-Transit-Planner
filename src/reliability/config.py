"""Validated configuration for TransLink realtime reliability data."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.data_ingestion.config import PROJECT_ROOT, ConfigurationError

DEFAULT_FEED_URL = (
    "https://gtfsapi.translink.ca/v3/gtfsrealtime?apikey={api_key}"
)


@dataclass(frozen=True)
class ReliabilityConfig:
    api_key: str
    feed_url_template: str = DEFAULT_FEED_URL
    request_timeout_seconds: float = 20.0
    minimum_samples: int = 20
    simulations: int = 1000
    random_seed: int = 42

    @classmethod
    def from_environment(cls) -> "ReliabilityConfig":
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.getenv("TRANSLINK_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "Missing required environment variable: TRANSLINK_API_KEY"
            )
        template = os.getenv("TRANSLINK_GTFS_RT_URL", DEFAULT_FEED_URL).strip()
        if "{api_key}" not in template:
            raise ConfigurationError(
                "TRANSLINK_GTFS_RT_URL must contain the {api_key} placeholder"
            )
        try:
            timeout = float(os.getenv("GTFS_RT_TIMEOUT_SECONDS", "20"))
            minimum = int(os.getenv("RELIABILITY_MINIMUM_SAMPLES", "20"))
        except ValueError as exc:
            raise ConfigurationError(
                "reliability timeout and minimum samples must be numeric"
            ) from exc
        if timeout <= 0 or minimum < 1:
            raise ConfigurationError(
                "reliability timeout and minimum samples must be positive"
            )
        return cls(api_key, template, timeout, minimum)

    @property
    def feed_url(self) -> str:
        return self.feed_url_template.format(api_key=self.api_key)
