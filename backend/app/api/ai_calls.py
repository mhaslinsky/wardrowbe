from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from app.database import DbSession
from app.schemas.ai_call import AICallPage, AICallRead
from app.services.ai_call_query_service import AICallQueryService
from app.utils.auth import CurrentUser

router = APIRouter(prefix="/ai-calls", tags=["AI calls"])


@router.get("", response_model=AICallPage)
async def list_calls(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
) -> AICallPage:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await AICallQueryService(db).page(user.id, limit, cursor)
    except ValueError as error:
        raise HTTPException(422, "Invalid cursor") from error


@router.get("/{call_id}", response_model=AICallRead)
async def read_call(call_id: UUID, user: CurrentUser, db: DbSession, response: Response) -> AICallRead:
    response.headers["Cache-Control"] = "no-store"
    call = await AICallQueryService(db).get(user.id, call_id)
    if call is None:
        raise HTTPException(404, "AI call not found")
    return call
