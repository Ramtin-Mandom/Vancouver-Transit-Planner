"""HTTP boundary for GTFS-Realtime Trip Updates."""

from __future__ import annotations

import requests

from .config import ReliabilityConfig


class RealtimeDownloadError(RuntimeError):
    """Raised when the realtime feed cannot be downloaded or validated."""


class RealtimeClient:
    def __init__(self, config: ReliabilityConfig, session=None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def download(self) -> bytes:
        try:
            response = self.session.get(
                self.config.feed_url,
                timeout=self.config.request_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = str(exc).replace(self.config.api_key, "[redacted]")
            raise RealtimeDownloadError(
                f"GTFS-Realtime download failed: {detail}"
            ) from exc
        content = response.content
        if not content:
            raise RealtimeDownloadError("GTFS-Realtime response was empty")
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type:
            raise RealtimeDownloadError("GTFS-Realtime endpoint returned HTML")
        return content
