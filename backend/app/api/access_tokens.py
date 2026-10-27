from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database import DbSession
from app.services.access_token_service import AccessTokenService
from app.utils.auth import SessionUser

router = APIRouter(prefix="/users/me/access-tokens", tags=["access tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class TokenInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class TokenCreated(TokenInfo):
    token: str


@router.post("", response_model=TokenCreated, status_code=201)
async def create_token(data: TokenCreate, user: SessionUser, db: DbSession, response: Response):
    response.headers["Cache-Control"] = "no-store"
    token, secret = await AccessTokenService(db).create(user.id, data.name)
    return {**TokenInfo.model_validate(token).model_dump(), "token": secret}


@router.get("", response_model=list[TokenInfo])
async def list_tokens(user: SessionUser, db: DbSession):
    return await AccessTokenService(db).list(user.id)


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: UUID, user: SessionUser, db: DbSession):
    if not await AccessTokenService(db).revoke(user.id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)
