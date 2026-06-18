"""Tests for handwritten date-header parsing (used for GoodNotes/Keep dating)."""
from datetime import datetime

from services.ingestion import parse_inline_date


def test_full_date_with_year():
    assert parse_inline_date("20 Jan 2026\nbody") == datetime(2026, 1, 20)


def test_weekday_prefix_and_ordinal():
    assert parse_inline_date("Wed 5th April 2025\nbody") == datetime(2025, 4, 5)


def test_date_colon_prefix():
    assert parse_inline_date("DATE: 15th May 2025") == datetime(2025, 5, 15)


def test_two_digit_year():
    assert parse_inline_date("5 May 24\nbody") == datetime(2024, 5, 5)


def test_no_year_picks_year_nearest_fallback():
    # Candidates are fallback.year ± 1; April 5 is closest in 2025.
    assert parse_inline_date("5th April\nbody", fallback=datetime(2025, 4, 10)) == datetime(2025, 4, 5)


def test_no_header_returns_none():
    assert parse_inline_date("Just some thoughts today.") is None


def test_mid_sentence_date_not_matched():
    # Anchored to the start of the first line, so this must not match.
    assert parse_inline_date("I went out on 5 May and it rained.") is None


def test_invalid_calendar_date_returns_none():
    assert parse_inline_date("31 Feb 2025\nbody") is None


def test_implausible_year_rejected():
    assert parse_inline_date("20 Jan 2099\nbody", fallback=datetime(2026, 1, 1)) is None
