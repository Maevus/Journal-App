"""Time helpers.

Naive-UTC datetimes throughout (DB timestamps carry no tzinfo); use
`utcnow_naive()` instead of the deprecated `datetime.utcnow()` — same value,
comparable with stored timestamps.
"""
from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime (the project's storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
