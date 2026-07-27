import pytest

from src.reliability.aggregation import summarize_delays


def test_percentiles_and_five_minute_on_time_threshold():
    result = summarize_delays([0, 100, 200, 300, 1000])
    assert result["p50_delay_seconds"] == 200
    assert result["p90_delay_seconds"] == pytest.approx(720)
    assert result["on_time_probability"] == 0.8


def test_mixed_early_and_late_delays_do_not_cancel():
    result = summarize_delays([-600, -120, 0, 240, 600])
    assert result["mean_delay_seconds"] == 24
    assert result["mean_absolute_delay_seconds"] == 312
    assert result["early_probability"] == 0.4
    assert result["on_time_probability"] == 0.4
    assert result["late_probability"] == 0.2
    assert (
        result["early_probability"]
        + result["on_time_probability"]
        + result["late_probability"]
    ) == pytest.approx(1.0)


def test_classification_boundaries_are_complete():
    result = summarize_delays([-61, -60, 300, 301])
    assert result["early_probability"] == 0.25
    assert result["on_time_probability"] == 0.5
    assert result["late_probability"] == 0.25
