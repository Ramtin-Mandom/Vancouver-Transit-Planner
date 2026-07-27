import pytest

from src.data_ingestion.config import ConfigurationError
from src.reliability.config import ReliabilityConfig


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("TRANSLINK_API_KEY", raising=False)
    monkeypatch.setattr("src.reliability.config.load_dotenv", lambda *args: None)
    with pytest.raises(ConfigurationError, match="TRANSLINK_API_KEY"):
        ReliabilityConfig.from_environment()


def test_api_key_is_substituted_but_not_exposed_in_template(monkeypatch):
    monkeypatch.setenv("TRANSLINK_API_KEY", "secret")
    monkeypatch.setattr("src.reliability.config.load_dotenv", lambda *args: None)
    config = ReliabilityConfig.from_environment()
    assert config.feed_url.endswith("apikey=secret")
    assert config.api_key not in config.feed_url_template
