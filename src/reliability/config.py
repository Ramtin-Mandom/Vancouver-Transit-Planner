"""Validated configuration for TransLink realtime reliability data."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.data_ingestion.config import PROJECT_ROOT, ConfigurationError
from .classification import (
    EARLY_THRESHOLD_SECONDS,
    LATE_THRESHOLD_SECONDS,
    SHRINKAGE_STRENGTH,
)
from .policy import DEFAULT_MINIMUM_SAMPLES

DEFAULT_FEED_URL = (
    "https://gtfsapi.translink.ca/v3/gtfsrealtime?apikey={api_key}"
)


@dataclass(frozen=True)
class ReliabilityConfig:
    api_key: str
    feed_url_template: str = DEFAULT_FEED_URL
    request_timeout_seconds: float = 20.0
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES
    simulations: int = 1000
    random_seed: int = 42
    early_threshold_seconds: int = EARLY_THRESHOLD_SECONDS
    late_threshold_seconds: int = LATE_THRESHOLD_SECONDS
    shrinkage_strength: float = SHRINKAGE_STRENGTH

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
            minimum = int(os.getenv(
                "RELIABILITY_MINIMUM_SAMPLES",
                str(DEFAULT_MINIMUM_SAMPLES),
            ))
            early = int(os.getenv("RELIABILITY_EARLY_SECONDS", "-120"))
            late = int(os.getenv("RELIABILITY_LATE_SECONDS", "300"))
            shrinkage = float(os.getenv("RELIABILITY_SHRINKAGE_STRENGTH", "20"))
        except ValueError as exc:
            raise ConfigurationError(
                "reliability timeout and minimum samples must be numeric"
            ) from exc
        if timeout <= 0 or minimum < 1 or shrinkage < 0 or early >= late:
            raise ConfigurationError(
                "reliability timeout and minimum samples must be positive"
            )
        return cls(
            api_key, template, timeout, minimum,
            early_threshold_seconds=early,
            late_threshold_seconds=late,
            shrinkage_strength=shrinkage,
        )

    @property
    def feed_url(self) -> str:
        return self.feed_url_template.format(api_key=self.api_key)
