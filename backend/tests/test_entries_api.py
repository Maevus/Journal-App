"""GET /entries — per-day entry listing with mood fields."""
from datetime import datetime


def test_returns_entries_for_the_day_only(client, add_entry):
    add_entry("on the day", timestamp=datetime(2026, 5, 10, 12))
    add_entry("day before", timestamp=datetime(2026, 5, 9, 23, 59))
    add_entry("day after", timestamp=datetime(2026, 5, 11, 0, 1))

    entries = client.get("/entries", params={"date": "2026-05-10"}).json()["entries"]
    assert [e["content"] for e in entries] == ["on the day"]


def test_includes_mood_score_and_reasoning(client, add_entry):
    add_entry(
        "great climb",
        mood_score=0.91,
        mood_reasoning="energised and accomplished",
        timestamp=datetime(2026, 5, 10, 12),
    )
    entry = client.get("/entries", params={"date": "2026-05-10"}).json()["entries"][0]
    assert entry["mood_score"] == 0.91
    assert entry["mood_reasoning"] == "energised and accomplished"
    assert {"id", "content", "source", "timestamp", "external_id"} <= entry.keys()


def test_source_filter(client, add_entry):
    add_entry("keep", source="google_keep", timestamp=datetime(2026, 5, 10, 8))
    add_entry("goodnotes", source="goodnotes", timestamp=datetime(2026, 5, 10, 9))
    entries = client.get(
        "/entries", params={"date": "2026-05-10", "source": "goodnotes"}
    ).json()["entries"]
    assert [e["source"] for e in entries] == ["goodnotes"]


def test_empty_day_returns_empty_list(client, add_entry):
    add_entry("elsewhere", timestamp=datetime(2026, 1, 1))
    assert client.get("/entries", params={"date": "2026-05-10"}).json()["entries"] == []
