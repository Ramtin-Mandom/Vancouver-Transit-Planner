import pytest
import requests

from src.reliability.client import RealtimeClient, RealtimeDownloadError
from src.reliability.config import ReliabilityConfig


class Response:
    content = b"protobuf"
    headers = {"content-type": "application/x-protobuf"}

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return Response()


def test_download_uses_timeout_and_returns_bytes():
    session = Session()
    client = RealtimeClient(ReliabilityConfig("key"), session)
    assert client.download() == b"protobuf"
    assert session.calls[0][1] == 20


def test_download_error_is_sanitized():
    client = RealtimeClient(
        ReliabilityConfig("key"),
        Session(requests.ConnectionError("offline")),
    )
    with pytest.raises(RealtimeDownloadError, match="download failed"):
        client.download()
