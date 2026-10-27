from io import BytesIO
from uuid import UUID

import httpx
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.config import get_settings
from app.models.considering import ItemPurchase, PurchaseState
from app.models.item import ClothingItem
from app.schemas.considering import ConsideringResponse
from app.schemas.item import ItemResponse
from app.services.item_service import ItemService
from app.services.public_download import download_public


async def download_image(url: str) -> UploadFile:
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    try:
        content, content_type, _url = await download_public(
            url,
            content_types=set(extensions),
            limit=get_settings().max_upload_size_mb * 1024 * 1024,
            deadline=20,
            timeout=10,
            size_error="Image exceeds upload limit",
        )
        return UploadFile(
            BytesIO(content),
            filename="product" + extensions[content_type],
            size=len(content),
            headers=Headers({"content-type": content_type}),
        )
    except (
        ValueError,
        KeyError,
        OSError,
        httpx.HTTPError,
        httpx.InvalidURL,
        TimeoutError,
    ) as error:
        raise HTTPException(422, "Image URL could not be downloaded") from error


class ConsideringService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: UUID, item_id: UUID, *, lock: bool = False) -> ItemPurchase:
        query = (
            select(ItemPurchase)
            .join(ClothingItem, ClothingItem.id == ItemPurchase.item_id)
            .where(
                ClothingItem.user_id == user_id,
                ItemPurchase.item_id == item_id,
            )
        )
        if lock:
            query = query.with_for_update(of=ItemPurchase).execution_options(populate_existing=True)
        purchase = await self.db.scalar(query)
        if purchase is None:
            raise HTTPException(404, "Shelf item not found")
        return purchase

    async def response(self, user_id: UUID, purchase: ItemPurchase) -> ConsideringResponse:
        item = await ItemService(self.db).get_by_id(purchase.item_id, user_id)
        return ConsideringResponse(
            source_url=purchase.source_url,
            price=purchase.price,
            currency=purchase.currency,
            planned_size=purchase.planned_size,
            state=purchase.state,
            passed_reason=purchase.passed_reason,
            item=ItemResponse.model_validate(item),
        )

    async def transition(
        self, user_id: UUID, item_id: UUID, state: PurchaseState, reason: str | None = None
    ) -> ConsideringResponse:
        purchase = await self.get(user_id, item_id, lock=True)
        if (purchase.state, state) not in {
            ("considering", "bought"),
            ("considering", "passed"),
            ("passed", "considering"),
        }:
            raise HTTPException(409, "Invalid purchase transition")
        purchase.state = state
        purchase.passed_reason = reason if state == "passed" else None
        await self.db.flush()
        return await self.response(user_id, purchase)
