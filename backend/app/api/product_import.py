from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl, UrlConstraints

from app.models.user import User
from app.services.product_import import fetch_product
from app.utils.auth import get_current_user
from app.utils.rate_limit import rate_limit_by_user

router = APIRouter()


class ProductLink(BaseModel):
    url: Annotated[HttpUrl, UrlConstraints(max_length=2000)]


@router.post("/extract")
async def extract_link(data: ProductLink, current_user: Annotated[User, Depends(get_current_user)]):
    await rate_limit_by_user(
        str(current_user.id), "product_extract", max_requests=10, window_seconds=60
    )
    return await fetch_product(str(data.url))
