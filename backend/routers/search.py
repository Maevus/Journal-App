from fastapi import APIRouter, Depends
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sqlmodel import Session, select

from models.journal import JournalEntry, get_session
from services.embeddings import search_similar

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


class SearchResult(BaseModel):
    id: int
    content: str
    source: str
    timestamp: str
    similarity_score: float


@router.post("")
async def search(
    request: SearchRequest,
    session: Session = Depends(get_session),
):
    entries = session.exec(select(JournalEntry)).all()

    if not entries:
        return {"results": []}

    # --- Semantic search (Chroma) ---
    semantic_hits = search_similar(request.query, request.limit)
    semantic_scores: dict[int, float] = {eid: score for eid, score in semantic_hits}

    # --- Keyword search (BM25) ---
    tokenised_corpus = [e.content.lower().split() for e in entries]
    bm25 = BM25Okapi(tokenised_corpus)
    bm25_raw = bm25.get_scores(request.query.lower().split())
    bm25_max = max(bm25_raw) if any(bm25_raw) else 1.0
    bm25_scores: dict[int, float] = {
        entries[i].id: (score / bm25_max if bm25_max else 0.0)
        for i, score in enumerate(bm25_raw)
    }

    # --- Hybrid fusion (70 % semantic + 30 % BM25) ---
    all_ids = {e.id for e in entries}
    combined: dict[int, float] = {
        eid: semantic_scores.get(eid, 0.0) * 0.7 + bm25_scores.get(eid, 0.0) * 0.3
        for eid in all_ids
    }

    top_ids = sorted(combined, key=lambda x: combined[x], reverse=True)[: request.limit]
    entry_map = {e.id: e for e in entries}

    results = [
        SearchResult(
            id=eid,
            content=entry_map[eid].content,
            source=entry_map[eid].source,
            timestamp=entry_map[eid].timestamp.isoformat(),
            similarity_score=round(combined[eid], 4),
        )
        for eid in top_ids
        if eid in entry_map
    ]

    return {"results": results}
