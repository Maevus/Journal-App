from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from models.journal import get_session
from services.retrieval import hybrid_search

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
    external_id: str | None = None


@router.post("")
async def search(
    request: SearchRequest,
    session: Session = Depends(get_session),
):
    hits = hybrid_search(session, request.query, request.limit)
    results = [
        SearchResult(
            id=entry.id,
            content=entry.content,
            source=entry.source,
            timestamp=entry.timestamp.isoformat(),
            similarity_score=score,
            external_id=entry.external_id,
        )
        for entry, score in hits
    ]
    return {"results": results}
