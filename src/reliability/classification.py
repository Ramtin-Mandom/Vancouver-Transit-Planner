"""Shared delay-classification thresholds."""

EARLY_THRESHOLD_SECONDS = -60
LATE_THRESHOLD_SECONDS = 300
FINAL_ARRIVAL_LATE_THRESHOLD_SECONDS = 600


def classify_delay(delay_seconds: float) -> str:
    if delay_seconds < EARLY_THRESHOLD_SECONDS:
        return "early"
    if delay_seconds > LATE_THRESHOLD_SECONDS:
        return "late"
    return "on-time"
