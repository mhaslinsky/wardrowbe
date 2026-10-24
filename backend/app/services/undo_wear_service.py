from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem, ItemHistory, WashHistory
from app.models.learning import OutfitPerformance
from app.models.outfit import Outfit, OutfitStatus
from app.models.user import User
from app.services.item_service import DEFAULT_WASH_INTERVALS
from app.services.wear_service import WearService


def undo_conflict(code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(409, detail={"error_code": code, "message": message, **details})


class UndoWearService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def undo(self, user: User, outfit_id: UUID, washed_item_ids: list[UUID] | None) -> Outfit:
        wear = WearService(self.db)
        await wear.begin(user)
        outfit = await wear._load(user.id, outfit_id)
        feedback = outfit.feedback
        if feedback is None or feedback.worn_at is None:
            raise undo_conflict("NOT_WORN", "Outfit is not worn")
        undone_date = feedback.worn_at
        await wear.lock_day(user.id, undone_date)
        item_ids = [link.item_id for link in outfit.items]
        items = list(
            (
                await self.db.scalars(
                    select(ClothingItem)
                    .where(ClothingItem.id.in_(item_ids), ClothingItem.user_id == user.id)
                    .order_by(ClothingItem.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        histories = list(
            (
                await self.db.scalars(select(ItemHistory).where(ItemHistory.item_id.in_(item_ids)))
            ).all()
        )
        target_rows = [history for history in histories if history.outfit_id == outfit.id]
        target_by_item = {history.item_id: history for history in target_rows}
        if (
            not items
            or len(items) != len(item_ids)
            or len(target_rows) != len(items)
            or len(target_by_item) != len(items)
            or any(item.wear_count <= 0 for item in items)
            or any(history.worn_at != undone_date for history in target_rows)
        ):
            raise undo_conflict("UNDO_INCONSISTENT", "Wear history is inconsistent")
        washes = list(
            (
                await self.db.scalars(
                    select(WashHistory)
                    .where(WashHistory.item_id.in_(item_ids))
                    .order_by(WashHistory.created_at.desc(), WashHistory.id)
                )
            ).all()
        )
        later_washes = {
            item.id: [
                wash
                for wash in washes
                if wash.item_id == item.id and wash.created_at > target_by_item[item.id].created_at
            ]
            for item in items
        }
        washed_ids = {item_id for item_id, rows in later_washes.items() if rows}
        if washed_item_ids is None and washed_ids:
            raise undo_conflict(
                "WASH_SINCE_WEAR",
                "Confirm which items were washed",
                items=[
                    {
                        "id": str(item.id),
                        "name": item.name,
                        "washed_at": later_washes[item.id][0].washed_at.isoformat(),
                    }
                    for item in items
                    if item.id in washed_ids
                ],
            )
        confirmed = set(washed_item_ids or [])
        if not confirmed.issubset(washed_ids):
            raise HTTPException(422, detail="Item has no wash since this wear")
        for item in items:
            remaining = [
                history
                for history in histories
                if history.item_id == item.id and history.outfit_id != outfit.id
            ]
            item.wear_count -= 1
            if item.last_worn_at == undone_date:
                item.last_worn_at = max((history.worn_at for history in remaining), default=None)
            if item.id not in confirmed:
                for wash in later_washes[item.id]:
                    await self.db.delete(wash)
                kept = [
                    wash
                    for wash in washes
                    if wash.item_id == item.id and wash not in later_washes[item.id]
                ]
                latest = kept[0] if kept else None
                item.last_washed_at = latest.washed_at if latest else None
                item.wears_since_wash = sum(
                    1
                    for history in remaining
                    if latest is None
                    or (
                        history.created_at > latest.created_at
                        and history.worn_at >= latest.washed_at
                    )
                )
                interval = (
                    item.wash_interval
                    if item.wash_interval is not None
                    else DEFAULT_WASH_INTERVALS.get(item.type, 3)
                )
                item.needs_wash = item.wears_since_wash >= interval
        for history in target_rows:
            await self.db.delete(history)
        outfit.status = OutfitStatus.skipped
        for field in (
            "accepted",
            "worn_at",
            "actually_worn",
            "wore_instead_items",
            "modification_notes",
            "rating",
            "comfort_rating",
            "style_rating",
        ):
            setattr(feedback, field, None)
        feedback.worn_with_modifications = False
        await self.db.execute(
            delete(OutfitPerformance).where(OutfitPerformance.outfit_id == outfit.id)
        )
        await self.db.flush()
        return outfit
