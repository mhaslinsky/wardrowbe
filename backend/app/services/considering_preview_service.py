from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.considering import owned_item
from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit
from app.models.user import User
from app.services.considering_service import ConsideringService
from app.services.studio_service import StudioService


class ConsideringPreviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def validate(
        self, user_id: UUID, anchor_id: UUID, companion_ids: list[UUID]
    ) -> list[UUID]:
        purchase = await ConsideringService(self.db).get(user_id, anchor_id, lock=True)
        if purchase.state != "considering":
            raise HTTPException(409, "Item is not considering")
        if (
            len(companion_ids) > 19
            or len(set(companion_ids)) != len(companion_ids)
            or anchor_id in companion_ids
        ):
            raise HTTPException(422, "Choose unique owned companions")
        requested = [anchor_id, *companion_ids]
        available = set(
            await self.db.scalars(
                select(ClothingItem.id).where(
                    ClothingItem.id.in_(requested),
                    ClothingItem.user_id == user_id,
                    ClothingItem.status == ItemStatus.ready,
                    ClothingItem.is_archived.is_(False),
                    (ClothingItem.id == anchor_id) | owned_item(),
                )
            )
        )
        if available != set(requested):
            raise HTTPException(422, "Items are unavailable")
        return requested

    async def create(self, user: User, anchor_id: UUID, companion_ids: list[UUID]) -> Outfit:
        item_ids = await self.validate(user.id, anchor_id, companion_ids)
        service = StudioService(self.db)
        outfit = await service.create_from_scratch(
            user=user,
            item_ids=item_ids,
            occasion="casual",
            name=None,
            scheduled_for=None,
            mark_worn=False,
            source_item_id=anchor_id,
        )
        return await service.get_full_outfit(outfit.id)
