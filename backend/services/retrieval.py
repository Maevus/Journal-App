"""Hybrid retrieval: semantic (Chroma) + BM25 keyword, fused 70/30, with an
optional timestamp window so time-scoped questions ("energy in May") only
retrieve from the relevant date range.

Shared by `/search` and `/chat` so the fusion lives in one place. Returns
entries paired with their combined score, highest first.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sqlmodel import Session, select

from clock import utcnow_naive
from models.journal import JournalEntry
from services.embeddings import search_similar
from services.llm_client import structured_output

logger = logging.getLogger(__name__)

# When a date window is active, pull a wider semantic pool: the global top-`limit`
# semantic hits may all fall outside the window, which would leave in-window
# entries with no semantic score and rank them on BM25 alone. Out-of-pool ids are
# dropped anyway by the fusion, so this is harmless when the window is large.
# TODO: this fixed cap still under-covers windows holding >100 entries (in-window
# entries beyond the global semantic top-100 fall back to BM25-only). The deep fix
# is storing `timestamp` as Chroma metadata and filtering the ANN query itself,
# which needs `embed_and_store` to persist metadata + a one-off re-embed.
_WINDOWED_SEMANTIC_POOL = 100

# A small, fast model is plenty for date extraction — no need to spend the default
# (opus) on it. Bare alias to match the house style (llm_client.DEFAULT_MODEL etc.).
_DATE_PARSE_MODEL = "claude-haiku-4-5"


def hybrid_search(
    session: Session,
    query: str,
    limit: int = 10,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[tuple[JournalEntry, float]]:
    """Return [(entry, score)] ranked by 0.7·semantic + 0.3·BM25, top `limit`.

    `start`/`end` (naive UTC, half-open `[start, end)`) restrict the candidate
    set to that window before fusion, so a time-scoped question can't be crowded
    out by more-similar entries from other dates. Either bound may be omitted.
    """
    stmt = select(JournalEntry)
    if start is not None:
        stmt = stmt.where(JournalEntry.timestamp >= start)
    if end is not None:
        stmt = stmt.where(JournalEntry.timestamp < end)
    entries = session.exec(stmt).all()
    if not entries:
        return []

    # --- Semantic search (Chroma) ---
    # search_similar ranks over the whole store; the fusion below keeps only ids
    # that are in `entries`, so out-of-window semantic hits are discarded.
    windowed = start is not None or end is not None
    n_semantic = max(limit, _WINDOWED_SEMANTIC_POOL) if windowed else limit
    semantic_scores: dict[int, float] = dict(search_similar(query, n_semantic))

    # --- Keyword search (BM25), normalised to 0-1 ---
    tokenised_corpus = [e.content.lower().split() for e in entries]
    bm25 = BM25Okapi(tokenised_corpus)
    bm25_raw = bm25.get_scores(query.lower().split())
    bm25_max = max(bm25_raw) if len(bm25_raw) else 0.0
    # BM25 IDF goes negative for terms in >half the docs, so raw scores can be
    # negative. Clamp: a non-positive max or a negative score contributes 0, which
    # avoids a negative/sign-flipped boost inflating an irrelevant entry's rank.
    bm25_scores: dict[int, float] = {
        entries[i].id: (score / bm25_max if bm25_max > 0 and score > 0 else 0.0)
        for i, score in enumerate(bm25_raw)
    }

    # --- Hybrid fusion (70 % semantic + 30 % BM25) ---
    entry_map = {e.id: e for e in entries}
    combined: dict[int, float] = {
        eid: semantic_scores.get(eid, 0.0) * 0.7 + bm25_scores.get(eid, 0.0) * 0.3
        for eid in entry_map
    }

    top_ids = sorted(combined, key=lambda x: combined[x], reverse=True)[:limit]
    return [(entry_map[eid], round(combined[eid], 4)) for eid in top_ids]


class DateRange(BaseModel):
    """An optional calendar window parsed from a natural-language question."""

    is_time_scoped: bool  # False when the question doesn't refer to a time period
    start_date: Optional[str] = None  # inclusive, ISO YYYY-MM-DD
    end_date: Optional[str] = None  # inclusive, ISO YYYY-MM-DD


_DATE_PARSE_SYSTEM = (
    "You extract an explicit calendar date range from a question about a personal "
    "journal. Set is_time_scoped=true ONLY when the question refers to a specific "
    "time period (a month, season, year, or a relative window like 'last week' / "
    "'the past 3 months'). Resolve relative references against the given current "
    "date. Do NOT treat ambiguous words like the modal verb 'may' as the month "
    "unless the phrasing clearly means the calendar month. When in doubt, set "
    "is_time_scoped=false. Return start_date and end_date as ISO dates "
    "(YYYY-MM-DD), both inclusive."
)


async def parse_date_range(
    question: str, *, today=None
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Resolve a question's time scope to a half-open `[start, end)` window.

    Returns (None, None) when the question isn't time-scoped or parsing fails —
    callers then retrieve over the whole journal, exactly as before. The LLM end
    bound is inclusive; this converts it to an exclusive next-day boundary so a
    whole-day/month range is retrieved completely.
    """
    today = today or utcnow_naive().date()
    prompt = f"Current date: {today.isoformat()}\n\nQuestion: {question}"
    try:
        result = await structured_output(
            prompt=prompt,
            output_schema=DateRange,
            system=_DATE_PARSE_SYSTEM,
            model=_DATE_PARSE_MODEL,
            max_tokens=128,
        )
    except Exception as exc:  # never let date parsing break a chat answer
        logger.warning("date-range parse failed (%s); retrieving unscoped", type(exc).__name__)
        return (None, None)

    if not result.is_time_scoped or not result.start_date or not result.end_date:
        return (None, None)
    try:
        # date (not datetime) so a stray time component can't shift a day boundary.
        start_day = date.fromisoformat(result.start_date)
        end_day = date.fromisoformat(result.end_date)
    except ValueError:
        logger.warning("date-range parse returned non-date values; retrieving unscoped")
        return (None, None)

    if start_day > end_day:  # inverted window would match nothing — fall back to unscoped
        logger.warning("date-range parse returned start after end; retrieving unscoped")
        return (None, None)

    start = datetime(start_day.year, start_day.month, start_day.day)
    end_exclusive = datetime(end_day.year, end_day.month, end_day.day) + timedelta(days=1)
    return (start, end_exclusive)
