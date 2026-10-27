from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.items import create_uploaded_item
from app.database import DbSession
from app.models.considering import ItemPurchase
from app.models.item import ClothingItem
from app.models.user import User
from app.schemas.considering import (
    ConsideringCreate,
    ConsideringEdit,
    ConsideringResponse,
    PassRequest,
)
from app.services.considering_service import ConsideringService, download_image
from app.utils.auth import get_current_user
from app.utils.rate_limit import rate_limit_by_user

router = APIRouter(prefix="/considering", tags=["Considering"])
ShelfUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[ConsideringResponse])
async def list_considering(
    db: DbSession, current_user: ShelfUser, state: Literal["considering", "passed"] = "considering"
):
    purchases = (
        await db.scalars(
            select(ItemPurchase)
            .join(ClothingItem, ClothingItem.id == ItemPurchase.item_id)
            .where(
                ClothingItem.user_id == current_user.id,
                ItemPurchase.state == state,
            )
            .order_by(ItemPurchase.created_at.desc())
        )
    ).all()
    service = ConsideringService(db)
    return [await service.response(current_user.id, purchase) for purchase in purchases]


@router.post("", response_model=ConsideringResponse, status_code=201)
async def create_considering(data: ConsideringCreate, db: DbSession, current_user: ShelfUser):
    await rate_limit_by_user(str(current_user.id), "considering", max_requests=5, window_seconds=60)
    uploads = []
    try:
        for url in data.image_urls:
            uploads.append(await download_image(str(url)))
        purchase = ItemPurchase(
            **data.model_dump(
                include={"source_url", "price", "currency", "planned_size"}, mode="json"
            )
        )
        await create_uploaded_item(
            db,
            current_user,
            uploads[0],
            type=data.type,
            name=data.name,
            brand=data.brand,
            purchase=purchase,
            additional_uploads=uploads[1:],
        )
        return await ConsideringService(db).response(current_user.id, purchase)
    finally:
        for upload in uploads:
            await upload.close()


@router.patch("/{item_id}", response_model=ConsideringResponse)
async def edit_considering(
    item_id: UUID, data: ConsideringEdit, db: DbSession, current_user: ShelfUser
):
    service = ConsideringService(db)
    purchase = await service.get(current_user.id, item_id, lock=True)
    if purchase.state == "bought":
        raise HTTPException(409, "Item already bought")
    fields = data.model_dump(exclude_unset=True, mode="json")
    if "name" in fields:
        item = await db.get(ClothingItem, item_id)
        item.name = fields.pop("name")
    for field, value in fields.items():
        setattr(purchase, field, value)
    await db.flush()
    return await service.response(current_user.id, purchase)


@router.post("/{item_id}/bought", response_model=ConsideringResponse)
async def buy_considering(item_id: UUID, db: DbSession, current_user: ShelfUser):
    return await ConsideringService(db).transition(current_user.id, item_id, "bought")


@router.post("/{item_id}/passed", response_model=ConsideringResponse)
async def pass_considering(
    item_id: UUID, data: PassRequest, db: DbSession, current_user: ShelfUser
):
    return await ConsideringService(db).transition(current_user.id, item_id, "passed", data.reason)


@router.post("/{item_id}/restore", response_model=ConsideringResponse)
async def restore_considering(item_id: UUID, db: DbSession, current_user: ShelfUser):
    return await ConsideringService(db).transition(current_user.id, item_id, "considering")
