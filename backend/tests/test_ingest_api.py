"""POST /ingest — the text payload path: dedup + background mood scoring.

`score_mood_background` is stubbed so ingest doesn't make real LLM calls; we
assert it's scheduled once per newly-ingested entry.
"""
import routers.ingest as ingest_router


async def _noop(*args, **kwargs):
    pass


def test_ingests_text_entries_and_schedules_scoring(client, login, monkeypatch):
    scored = []

    async def fake_score(entry_id, content):
        scored.append(entry_id)

    monkeypatch.setattr(ingest_router, "score_mood_background", fake_score)

    login()
    r = client.post("/ingest", json={"entries": ["first entry", "second entry"], "source": "text"})
    assert r.status_code == 200
    assert r.json() == {"ingested": 2, "updated": 0, "skipped": 0}
    assert len(scored) == 2  # background scoring scheduled per new entry


def test_duplicate_ingest_is_skipped(client, login, monkeypatch):
    monkeypatch.setattr(ingest_router, "score_mood_background", _noop)
    login()
    client.post("/ingest", json={"entries": ["dup"], "source": "text"})
    r = client.post("/ingest", json={"entries": ["dup"], "source": "text"})
    assert r.json() == {"ingested": 0, "updated": 0, "skipped": 1}


def test_requires_body_when_no_source(client, login):
    # No source query param and no body → 422 (nothing to ingest).
    login()
    assert client.post("/ingest").status_code == 422
