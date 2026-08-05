"""Hybrid retrieval: semantic (pgvector) + BM25 keyword, fused 70/30, with an
optional timestamp window so time-scoped questions ("energy in May") only
retrieve from the relevant date range.

Shared by `/search` and `/chat` so the fusion lives in one place. Returns
entries paired with their combined score, highest first.
"""
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sqlmodel import Session

from clock import utcnow_naive
from models.journal import JournalEntry, user_entries
from services.embeddings import search_similar
from services.llm_client import structured_output

logger = logging.getLogger(__name__)

# When a date window is active, pull a wider semantic pool so in-window entries
# aren't left BM25-only just because they miss the global top-`limit` hits.
# TODO: still under-covers windows >100 entries. Fix: add `timestamp` to
# entry_embeddings and push the window into the ANN query's WHERE, so
# search_similar itself returns only in-window hits.
_WINDOWED_SEMANTIC_POOL = 100

# Strips punctuation ("grumpy." and "grumpy" must be one token). `\w` is
# Unicode-aware, so accented words stay whole; intra-word apostrophes are kept
# in both the straight and curly forms.
_TOKEN_RE = re.compile(r"\w+(?:['’]\w+)*")


def _tokenize(text: str) -> list[str]:
    """Lowercase and split into BM25 terms. Corpus and query must both go
    through this or they stop matching."""
    return _TOKEN_RE.findall(text.lower())

# Task-fixed, passed explicitly — bypasses per-user resolution
# (llm_client.resolve_model), since a small fast model suffices regardless of tier.
_DATE_PARSE_MODEL = "claude-haiku-4-5"
_CONDENSE_MODEL = "claude-haiku-4-5"


def hybrid_search(
    session: Session,
    query: str,
    limit: int = 10,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    *,
    user_id: int,
) -> list[tuple[JournalEntry, float]]:
    """Return [(entry, score)] of `user_id`'s entries ranked by
    0.7·semantic + 0.3·BM25, top `limit`.

    `user_id` is required and keyword-only (ADR 0003): a forgotten argument
    fails loudly instead of searching everyone. The SQL candidate set is the
    isolation boundary — other users' semantic hits are dropped at fusion;
    the pgvector filter is defense in depth, not the source of truth.

    `start`/`end` (naive UTC, half-open) restrict the candidate set before
    fusion, so a time-scoped question isn't crowded out by other dates.
    """
    stmt = user_entries(user_id)
    if start is not None:
        stmt = stmt.where(JournalEntry.timestamp >= start)
    if end is not None:
        stmt = stmt.where(JournalEntry.timestamp < end)
    entries = session.exec(stmt).all()
    if not entries:
        return []

    # --- Semantic search (pgvector) ---
    # Already user-filtered; the fusion below also keeps only ids in
    # `entries`, discarding out-of-window hits.
    windowed = start is not None or end is not None
    n_semantic = max(limit, _WINDOWED_SEMANTIC_POOL) if windowed else limit
    semantic_scores: dict[int, float] = dict(
        search_similar(query, n_semantic, user_id=user_id)
    )

    # --- Keyword search (BM25), normalised to 0-1 ---
    tokenised_corpus = [_tokenize(e.content) for e in entries]
    bm25 = BM25Okapi(tokenised_corpus)
    bm25_raw = bm25.get_scores(_tokenize(query))
    bm25_max = max(bm25_raw) if len(bm25_raw) else 0.0
    # BM25 IDF goes negative for terms in >half the docs. Clamp to 0 so a
    # negative score can't sign-flip into a boost for an irrelevant entry.
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
    question: str, *, today=None, user_id: Optional[int]
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Resolve a question's time scope to a half-open `[start, end)` window.

    Returns (None, None) when unscoped or parsing fails, so callers retrieve
    the whole journal. The LLM's end bound is inclusive; converted here to
    an exclusive next-day boundary.
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
            user_id=user_id,  # date parsing spends money on the asking user's behalf
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


class StandaloneQuery(BaseModel):
    """A follow-up question rewritten to stand on its own for retrieval."""

    standalone_query: str


_CONDENSE_SYSTEM = (
    "You rewrite a follow-up question in a conversation about a personal journal into "
    "a single self-contained search query. Resolve pronouns and elliptical references "
    "('and June?', 'what about then?') using the earlier turns, so the query makes sense "
    "on its own without the conversation. Preserve any time period the user means. Return "
    "only the rewritten query — do not answer the question."
)


async def condense_query(history: list[dict], question: str, *, user_id: Optional[int]) -> str:
    """Rewrite a follow-up `question` into a standalone retrieval query.

    `history` is prior `{role, content}` messages, oldest first, excluding
    the current question. No history means no LLM call — the question
    already stands alone. Any failure degrades to the raw question.
    """
    if not history:
        return question

    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    prompt = f"Conversation so far:\n{transcript}\n\nFollow-up question: {question}"
    try:
        result = await structured_output(
            prompt=prompt,
            output_schema=StandaloneQuery,
            system=_CONDENSE_SYSTEM,
            model=_CONDENSE_MODEL,
            max_tokens=256,
            user_id=user_id,
        )
    except Exception as exc:  # never let condensation break a chat answer
        logger.warning("query condense failed (%s); using raw question", type(exc).__name__)
        return question

    rewritten = result.standalone_query.strip()
    return rewritten or question


@dataclass
class MoodSummary:
    """Whole-journal mood aggregates for the Ask agent's context.

    RAG retrieves only a handful of entries per turn, so questions like
    "what's my average mood?" need this aggregate instead — scoped to one
    user, computed the same parameterized-ORM way /trends does."""

    count: int
    overall_avg: float
    first_date: str  # ISO date of the earliest scored entry
    last_date: str  # ISO date of the latest scored entry
    by_year: list[dict]  # [{"year", "avg", "count"}], oldest → newest


def mood_summary(
    session: Session,
    *,
    user_id: int,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Optional[MoodSummary]:
    """`user_id`'s mood aggregates over their scored entries, or None if none
    are scored in scope.

    `start`/`end` (naive UTC, half-open) scope the aggregate to a time
    window, so "average in 2024?" summarises that period, not all time."""
    stmt = user_entries(user_id).where(JournalEntry.mood_score.isnot(None))  # type: ignore[union-attr]
    if start is not None:
        stmt = stmt.where(JournalEntry.timestamp >= start)
    if end is not None:
        stmt = stmt.where(JournalEntry.timestamp < end)
    rows = session.exec(stmt).all()
    if not rows:
        return None

    scores = [r.mood_score for r in rows]
    per_year: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        per_year[r.timestamp.year].append(r.mood_score)
    by_year = [
        {"year": y, "avg": round(sum(v) / len(v), 3), "count": len(v)}
        for y, v in sorted(per_year.items())
    ]
    dates = sorted(r.timestamp for r in rows)
    return MoodSummary(
        count=len(rows),
        overall_avg=round(sum(scores) / len(scores), 3),
        first_date=dates[0].date().isoformat(),
        last_date=dates[-1].date().isoformat(),
        by_year=by_year,
    )


def render_mood_summary(summary: MoodSummary) -> str:
    """A compact prompt block from a MoodSummary — the mood-analytics context
    the Ask agent uses for average/trend questions."""
    years = "; ".join(f"{y['year']}: {y['avg']:.2f} (n={y['count']})" for y in summary.by_year)
    return (
        "Mood analytics — a computed 0.0-1.0 sentiment score per entry "
        "(0 = very negative, 1 = very positive), separate from any rating the "
        "person wrote in their own words:\n"
        f"- Overall: average {summary.overall_avg:.2f} across {summary.count} "
        f"scored entries, {summary.first_date} to {summary.last_date}.\n"
        f"- By year: {years}."
    )
