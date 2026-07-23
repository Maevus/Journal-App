"""GET /trends — daily mood averaging, filtering, and the empty-DB stub."""
from datetime import datetime, timedelta


def test_averages_scores_per_day(client, add_entry):
    day = datetime(2026, 5, 10, 9, 0)
    add_entry("morning", mood_score=0.4, timestamp=day)
    add_entry("evening", mood_score=0.8, timestamp=day.replace(hour=21))
    add_entry("next day", mood_score=0.2, timestamp=day + timedelta(days=1))

    r = client.get("/trends", params={"days": 0})
    trends = r.json()["trends"]
    assert r.status_code == 200
    by_date = {t["date"]: t["score"] for t in trends}
    assert by_date["2026-05-10"] == 0.6  # (0.4 + 0.8) / 2
    assert by_date["2026-05-11"] == 0.2


def test_excludes_unscored_entries(client, add_entry):
    add_entry("scored", mood_score=0.5, timestamp=datetime(2026, 5, 10))
    add_entry("not scored yet", mood_score=None, timestamp=datetime(2026, 5, 11))
    trends = client.get("/trends", params={"days": 0}).json()["trends"]
    assert [t["date"] for t in trends] == ["2026-05-10"]


def test_source_filter(client, add_entry):
    add_entry("keep one", source="google_keep", mood_score=0.9, timestamp=datetime(2026, 5, 10))
    add_entry("gn one", source="goodnotes", mood_score=0.1, timestamp=datetime(2026, 5, 10))
    trends = client.get("/trends", params={"days": 0, "source": "google_keep"}).json()["trends"]
    assert trends == [{"date": "2026-05-10", "score": 0.9}]


def test_empty_db_returns_sinusoid_stub(client, me):
    # No scored entries and no source filter → deterministic stub so the chart renders.
    trends = client.get("/trends", params={"days": 30}).json()["trends"]
    assert len(trends) == 30
    assert all(0.0 <= t["score"] <= 1.0 for t in trends)


def test_empty_db_with_source_filter_returns_no_stub(client, me):
    # A fake sinusoid for a specific source would be misleading → honest empty.
    trends = client.get("/trends", params={"days": 30, "source": "goodnotes"}).json()["trends"]
    assert trends == []


def test_custom_start_end_window_overrides_days(client, add_entry):
    """An explicit start/end date range filters to that window (end inclusive)
    and ignores the `days` lookback."""
    add_entry("before window", mood_score=0.2, timestamp=datetime(2024, 12, 31))
    add_entry("inside", mood_score=0.6, timestamp=datetime(2025, 6, 1))
    add_entry("on the end day", mood_score=0.8, timestamp=datetime(2025, 12, 31, 22, 0))
    add_entry("after window", mood_score=0.9, timestamp=datetime(2026, 1, 2))

    r = client.get("/trends", params={"start": "2025-01-01", "end": "2025-12-31", "days": 30})
    assert r.status_code == 200
    dates = {t["date"] for t in r.json()["trends"]}
    assert dates == {"2025-06-01", "2025-12-31"}  # before/after excluded; end day kept


def test_custom_window_with_no_entries_returns_empty_not_stub(client, add_entry):
    """An empty custom range shows an honest empty chart, not the sinusoid stub."""
    add_entry("far outside", mood_score=0.5, timestamp=datetime(2020, 1, 1))
    r = client.get("/trends", params={"start": "2025-01-01", "end": "2025-01-31"})
    assert r.json()["trends"] == []


def test_bad_date_is_rejected(client, me):
    assert client.get("/trends", params={"start": "not-a-date", "end": "2025-01-01"}).status_code == 422
