"""Time helpers.

The app stores naive-UTC datetimes throughout (DB timestamps carry no tzinfo),
so use `utcnow_naive()` instead of the deprecated `datetime.utcnow()`. It returns
the same value `datetime.utcnow()` did — current UTC with tzinfo stripped — so it
stays comparable with stored timestamps.
"""
from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime (the project's storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
