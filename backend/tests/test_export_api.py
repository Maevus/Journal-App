"""Tests for GET /export (CSV + JSON download of journal entries)."""
import csv
import io
import json


def test_export_csv_basic(client, add_entry):
    add_entry("first entry", source="text", mood_score=3, mood_reasoning="ok")
    add_entry("second entry", source="text", mood_score=7, mood_reasoning="good")

    resp = client.get("/export", params={"format": "csv"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert (
        resp.headers["content-disposition"]
        == 'attachment; filename="journal_export.csv"'
    )

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert reader.fieldnames == [
        "id",
        "timestamp",
        "source",
        "content",
        "mood_score",
        "mood_reasoning",
        "external_id",
    ]
    assert len(rows) == 2  # header + 2 data rows -> 2 parsed dicts
    assert rows[0]["content"] == "first entry"
    assert rows[1]["content"] == "second entry"


def test_export_csv_quotes_comma_and_newline(client, add_entry):
    tricky = 'has a comma, and a\nnewline, plus "quotes"'
    add_entry(tricky, source="text")

    resp = client.get("/export", params={"format": "csv"})
    assert resp.status_code == 200

    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    # The csv module must have round-tripped the special chars intact.
    assert rows[0]["content"] == tricky


def test_export_json_basic(client, add_entry):
    add_entry("entry one", source="text", mood_score=4, mood_reasoning="neutral")

    resp = client.get("/export", params={"format": "json"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert (
        resp.headers["content-disposition"]
        == 'attachment; filename="journal_export.json"'
    )

    data = json.loads(resp.text)
    assert isinstance(data, list)
    assert len(data) == 1
    entry = data[0]
    for field in (
        "id",
        "timestamp",
        "source",
        "content",
        "mood_score",
        "mood_reasoning",
        "external_id",
    ):
        assert field in entry
    assert entry["content"] == "entry one"
    assert entry["mood_score"] == 4
    assert entry["mood_reasoning"] == "neutral"


def test_export_source_filter(client, add_entry):
    add_entry("keep note", source="google_keep")
    add_entry("text note", source="text")

    resp = client.get("/export", params={"format": "json", "source": "google_keep"})
    assert resp.status_code == 200

    data = json.loads(resp.text)
    assert len(data) == 1
    assert data[0]["content"] == "keep note"
    assert data[0]["source"] == "google_keep"


def test_export_unknown_format_400(client, add_entry):
    add_entry("anything")

    resp = client.get("/export", params={"format": "xml"})
    assert resp.status_code == 400


def test_export_empty_csv(client):
    resp = client.get("/export", params={"format": "csv"})
    assert resp.status_code == 200

    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert rows == []  # header only, no data rows


def test_export_empty_json(client):
    resp = client.get("/export", params={"format": "json"})
    assert resp.status_code == 200
    assert json.loads(resp.text) == []
