from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import current_user, get_db, get_redis
from app.auth.rate_limits import rate_limit
from app.config import settings
from app.retrieval.schemas import SearchRequest, SearchResponse
from app.retrieval.service import retrieve

router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(
        redis, f"retrieval:{actor['_id']}", settings.retrieval_rate_limit, 60
    )
    return await retrieve(db, actor, payload.query, request.state.request_id)
