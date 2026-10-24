from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus, UserFeedback
from app.models.user import User
from app.services.item_service import ItemService
from app.utils.timezone import get_user_today


class ItemOwnershipError(Exception):
    pass


@dataclass
class Created:
    outfit: Outfit


@dataclass
class AlreadyThis:
    outfit: Outfit


@dataclass
class DayLogged:
    existing_ids: list[UUID]


def require_wear(result: Created | AlreadyThis | DayLogged) -> Outfit:
    """Unwrap Created/AlreadyThis or raise 409 for DayLogged; the caller commits."""
    if isinstance(result, DayLogged):
        raise HTTPException(
            409,
            detail={
                "error_code": "DAY_LOGGED",
                "message": "A wear is already logged for this day",
                "existing_ids": [str(outfit_id) for outfit_id in result.existing_ids],
            },
        )
    return result.outfit


class WearService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_day(self, user_id: UUID, worn_on: date) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{user_id}:{worn_on.isoformat()}"},
        )

    async def _load(self, user_id: UUID, outfit_id: UUID) -> Outfit:
        result = await self.db.execute(
            select(Outfit)
            .where(Outfit.id == outfit_id, Outfit.user_id == user_id)
            .options(
                selectinload(Outfit.items).selectinload(OutfitItem.item),
                selectinload(Outfit.feedback),
            )
            .execution_options(populate_existing=True)
        )
        outfit = result.scalar_one_or_none()
        if outfit is None:
            raise LookupError("Outfit not found")
        return outfit

    async def _validate(
        self, user: User, outfit: Outfit, item_ids: list[UUID], *, availability: bool = True
    ) -> None:
        if outfit.status in (OutfitStatus.rejected, OutfitStatus.expired):
            raise HTTPException(422, detail="Outfit cannot be worn")
        if not item_ids or len(item_ids) != len(set(item_ids)):
            raise HTTPException(422, detail="Choose unique items")
        if not availability:
            return
        result = await self.db.execute(
            select(ClothingItem.id).where(
                ClothingItem.id.in_(item_ids),
                ClothingItem.user_id == user.id,
                ClothingItem.is_archived.is_(False),
                ClothingItem.status == ItemStatus.ready,
            )
        )
        if set(result.scalars()) != set(item_ids):
            raise ItemOwnershipError("Items are unavailable")

    async def _root(self, user_id: UUID, outfit: Outfit) -> Outfit:
        seen = {outfit.id}
        while parent_id := outfit.cloned_from_outfit_id or outfit.replaces_outfit_id:
            # A saved template starts its own wear history, even when copied from a suggestion.
            if outfit.source == OutfitSource.manual and outfit.scheduled_for is None:
                return outfit
            if parent_id in seen:
                raise HTTPException(422, detail="Invalid outfit lineage")
            seen.add(parent_id)
            outfit = await self._load(user_id, parent_id)
        return outfit

    async def log_worn(
        self,
        user: User,
        source_outfit: Outfit,
        worn_on: date,
        item_ids: list[UUID] | None = None,
        *,
        caller: str,
    ) -> Created | AlreadyThis | DayLogged:
        """Return Created, unchanged AlreadyThis, or DayLogged; the caller commits."""
        if worn_on > get_user_today(user):
            raise HTTPException(422, detail="Wear date cannot be in the future")
        await self.lock_day(user.id, worn_on)
        source = await self._load(user.id, source_outfit.id)
        effective_ids = (
            item_ids if item_ids is not None else [item.item_id for item in source.items]
        )
        await self._validate(user, source, effective_ids, availability=False)
        was_worn = source.feedback is not None and source.feedback.worn_at is not None
        is_child = source.scheduled_for is not None and source.cloned_from_outfit_id is not None
        root = await self._root(user.id, source) if was_worn or is_child else source
        source_ids = {item.item_id for item in source.items}
        result = await self.db.execute(
            select(Outfit)
            .where(
                Outfit.user_id == user.id,
                Outfit.feedback.has(UserFeedback.worn_at == worn_on),
            )
            .options(selectinload(Outfit.items), selectinload(Outfit.feedback))
            .execution_options(populate_existing=True)
            .order_by(Outfit.id)
        )
        logged = list(result.scalars())
        for existing in logged:
            if (await self._root(user.id, existing)).id == root.id:
                if {item.item_id for item in existing.items} == set(effective_ids):
                    return AlreadyThis(existing)
        if logged:
            return DayLogged([existing.id for existing in logged])
        await self._validate(user, source, effective_ids)

        inspiration = "inspiration" in (source.ai_raw_response or {})
        was_worn = source.feedback is not None and source.feedback.worn_at is not None
        template = source.source == OutfitSource.manual and source.scheduled_for is None
        if (
            not inspiration
            and not was_worn
            and not template
            and source.scheduled_for == worn_on
            and source_ids == set(effective_ids)
        ):
            wear = source
        else:
            template_root = root.source == OutfitSource.manual and root.scheduled_for is None
            wear = Outfit(
                user_id=user.id,
                occasion=source.occasion,
                scheduled_for=worn_on,
                source=OutfitSource.manual,
                status=OutfitStatus.pending,
                cloned_from_outfit_id=root.id if template_root and not inspiration else None,
                replaces_outfit_id=(
                    source.id
                    if caller == "wore_instead"
                    else None
                    if template_root and not inspiration
                    else root.id
                ),
                name=source.name,
                source_item_id=source.source_item_id,
            )
            self.db.add(wear)
            await self.db.flush()
            layers = {item.item_id: item.layer_type for item in source.items}
            for position, item_id in enumerate(effective_ids):
                self.db.add(
                    OutfitItem(
                        outfit_id=wear.id,
                        item_id=item_id,
                        position=position,
                        layer_type=layers.get(item_id),
                    )
                )
            await self.db.flush()
            wear = await self._load(user.id, wear.id)
        wear.status = OutfitStatus.accepted
        wear.responded_at = datetime.now(UTC)
        if wear.feedback is None:
            wear.feedback = UserFeedback(outfit_id=wear.id)
            self.db.add(wear.feedback)
        wear.feedback.accepted = True
        wear.feedback.actually_worn = True
        wear.feedback.worn_at = worn_on
        if source_ids != set(effective_ids):
            wear.feedback.worn_with_modifications = True
            wear.feedback.wore_instead_items = [
                str(item_id) for item_id in effective_ids if item_id not in source_ids
            ]
        await ItemService(self.db).record_wears(
            user.id, effective_ids, worn_on, occasion=wear.occasion, outfit_id=wear.id
        )
        result = await self.db.execute(
            select(Outfit)
            .where(
                Outfit.user_id == user.id,
                Outfit.id != wear.id,
                or_(Outfit.id == source.id, Outfit.scheduled_for == worn_on),
                Outfit.source.in_(
                    [OutfitSource.scheduled, OutfitSource.on_demand, OutfitSource.external]
                ),
                Outfit.status.in_([OutfitStatus.pending, OutfitStatus.sent, OutfitStatus.viewed]),
                ~Outfit.feedback.has(UserFeedback.worn_at.is_not(None)),
            )
            .options(selectinload(Outfit.feedback))
        )
        for suggestion in result.scalars():
            suggestion.status = OutfitStatus.skipped
        await self.db.flush()
        return Created(wear)
