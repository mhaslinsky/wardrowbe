import hashlib
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit
from app.models.selfie_confirm import SelfieConfirm
from app.models.user import User
from app.services.studio_service import StudioService, load_full_outfit
from app.services.wear_service import ItemOwnershipError, WearService
from app.utils.timezone import get_user_today


class SelfieConfirmService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def confirm(
        self, user: User, request_id: UUID, item_ids: list[UUID]
    ) -> tuple[Outfit, bool]:
        await WearService(self.db).begin(user)
        if not item_ids or len(item_ids) != len(set(item_ids)):
            raise HTTPException(422, detail="Choose unique items")
        payload_hash = hashlib.sha256(
            ",".join(sorted(str(item_id) for item_id in item_ids)).encode()
        ).hexdigest()
        previous = await self.db.scalar(
            select(SelfieConfirm).where(
                SelfieConfirm.user_id == user.id, SelfieConfirm.request_id == request_id
            )
        )
        if previous is not None:
            if previous.payload_hash != payload_hash:
                raise HTTPException(409, detail={"error_code": "REQUEST_REUSED"})
            deleted_result = HTTPException(
                410,
                detail={
                    "error_code": "CONFIRM_RESULT_DELETED",
                    "worn_on": previous.worn_on.isoformat(),
                },
            )
            if previous.outfit_id is None:
                raise deleted_result
            try:
                return await load_full_outfit(self.db, previous.outfit_id), False
            except NoResultFound:
                # Deletion can commit after the request lookup.
                raise deleted_result from None

        available = await self.db.scalars(
            select(ClothingItem.id).where(
                ClothingItem.id.in_(item_ids),
                ClothingItem.user_id == user.id,
                ClothingItem.status == ItemStatus.ready,
                ClothingItem.is_archived.is_(False),
            )
        )
        if set(available) != set(item_ids):
            raise ItemOwnershipError("Items are unavailable")
        worn_on = get_user_today(user)
        outfit = await StudioService(self.db).create_from_scratch(
            user=user,
            item_ids=item_ids,
            occasion="casual",
            name=None,
            scheduled_for=worn_on,
            mark_worn=True,
            source_item_id=None,
        )
        self.db.add(
            SelfieConfirm(
                user_id=user.id,
                request_id=request_id,
                payload_hash=payload_hash,
                worn_on=worn_on,
                outfit_id=outfit.id,
            )
        )
        await self.db.flush()
        return await load_full_outfit(self.db, outfit.id), True
