import pytest

from src.reliability.aggregation import summarize_delays


def test_percentiles_and_five_minute_on_time_threshold():
    result = summarize_delays([0, 100, 200, 300, 1000])
    assert result["p50_delay_seconds"] == 200
    assert result["p90_delay_seconds"] == pytest.approx(720)
    assert result["on_time_probability"] == 0.8
