from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from models.journal import get_session
from models.user import User
from services.auth import get_current_user
from services.rate_limit import TIER_SEARCH, rate_limit
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


# A local ONNX encode per query: CPU rather than money, so the ceiling is
# higher than the LLM tiers — but it is still work an anonymous caller can
# make this process do, one core at a time.
@router.post("", dependencies=[Depends(rate_limit(TIER_SEARCH))])
async def search(
    request: SearchRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    hits = hybrid_search(session, request.query, request.limit, user_id=user.id)
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
