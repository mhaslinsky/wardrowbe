from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.considering import owned_item
from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import (
    FamilyOutfitRating,
    Outfit,
    OutfitItem,
    OutfitSource,
    OutfitStatus,
)
from app.models.user import User
from app.services.learning_service import LearningService
from app.services.wear_service import (
    AlreadyThis,
    ItemOwnershipError,
    OutfitNotTemplateError,
    WearService,
    require_wear,
)
from app.services.wear_service import FutureWearDateError as FutureWearDateError
from app.utils.clothing import canonical_item_order
from app.utils.timezone import get_user_today


class OutfitWornImmutableError(Exception):
    pass


async def load_full_outfit(db: AsyncSession, outfit_id: UUID) -> Outfit:
    result = await db.execute(
        select(Outfit)
        .where(Outfit.id == outfit_id)
        .options(
            selectinload(Outfit.items).selectinload(OutfitItem.item),
            selectinload(Outfit.feedback),
            selectinload(Outfit.source_item),
            selectinload(Outfit.family_ratings).selectinload(FamilyOutfitRating.user),
        )
    )
    return result.scalar_one()


async def validate_item_ownership(
    db: AsyncSession, user_id: UUID, item_ids: list[UUID]
) -> list[ClothingItem]:
    if not item_ids:
        raise ValueError("items required")

    result = await db.execute(
        select(ClothingItem).where(
            and_(
                ClothingItem.id.in_(item_ids),
                ClothingItem.user_id == user_id,
                ClothingItem.status == ItemStatus.ready,
            )
        )
    )
    items = list(result.scalars().all())
    unique_requested = set(item_ids)
    if len(items) != len(unique_requested):
        raise ItemOwnershipError("one or more items do not belong to the caller")
    return items


class StudioService:
    CLONE_SOFT_IDEMPOTENCY_SECONDS = 5

    def __init__(self, db: AsyncSession):
        self.db = db
        self.learning = LearningService(db)

    def _order_items_canonically(self, items: list[ClothingItem]) -> list[ClothingItem]:
        type_map = {item.id: (item.type or "") for item in items}
        ordered_ids = canonical_item_order([i.id for i in items], type_map)
        by_id = {item.id: item for item in items}
        return [by_id[iid] for iid in ordered_ids]

    async def _reload_outfit(self, outfit_id: UUID) -> Outfit:
        result = await self.db.execute(
            select(Outfit)
            .where(Outfit.id == outfit_id)
            .options(
                selectinload(Outfit.items).selectinload(OutfitItem.item),
                selectinload(Outfit.feedback),
            )
        )
        return result.scalar_one()

    async def get_full_outfit(self, outfit_id: UUID) -> Outfit:
        return await load_full_outfit(self.db, outfit_id)

    async def create_from_scratch(
        self,
        user: User,
        item_ids: list[UUID],
        occasion: str,
        name: str | None,
        scheduled_for: date | None,
        mark_worn: bool,
        source_item_id: UUID | None,
        season: str | None = None,
        formality: str | None = None,
        palette: list[str] | None = None,
        notes: str | None = None,
    ) -> Outfit:
        if mark_worn:
            await WearService(self.db).begin(user, scheduled_for)
            # Reject shelf items before creating the outfit or its acceptance feedback.
            available = set(
                await self.db.scalars(
                    select(ClothingItem.id).where(
                        ClothingItem.id.in_(item_ids),
                        ClothingItem.user_id == user.id,
                        owned_item(),
                    )
                )
            )
            if available != set(item_ids):
                raise ItemOwnershipError("Items are unavailable")
            scheduled_for = scheduled_for or get_user_today(user)
        items = await validate_item_ownership(self.db, user.id, item_ids)
        ordered = self._order_items_canonically(items)

        outfit = Outfit(
            user_id=user.id,
            occasion=occasion,
            scheduled_for=scheduled_for,
            source=OutfitSource.manual,
            status=OutfitStatus.pending,
            responded_at=None,
            name=name,
            source_item_id=source_item_id,
            season=season,
            formality=formality,
            palette=palette,
            notes=notes,
        )
        self.db.add(outfit)
        await self.db.flush()

        for pos, item in enumerate(ordered):
            self.db.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=pos))

        feedback = self.learning.create_synthetic_feedback(
            outfit_id=outfit.id, accepted=True, worn_at=None
        )
        self.db.add(feedback)

        await self.db.flush()
        if mark_worn:
            source = await self._reload_outfit(outfit.id)
            result = await WearService(self.db).log_worn(
                user, source, scheduled_for, item_ids, caller="studio"
            )
            require_wear(result)

        await self.db.flush()
        return await self._reload_outfit(outfit.id)

    async def create_wore_instead(
        self,
        user: User,
        original_outfit_id: UUID,
        item_ids: list[UUID],
        rating: int | None,
        comment: str | None,
        scheduled_for: date | None,
    ) -> Outfit:
        await WearService(self.db).begin(user, scheduled_for)
        original = await WearService(self.db)._load(user.id, original_outfit_id)
        target_date = WearService.date_for(user, original, scheduled_for)
        result = await WearService(self.db).log_worn(
            user,
            original,
            target_date,
            item_ids,
            caller="wore_instead",
        )
        replacement = require_wear(result)
        if rating is not None:
            replacement.feedback.rating = rating
        if comment is not None:
            replacement.feedback.comment = comment
        await self.db.flush()
        return await self._reload_outfit(replacement.id)

    async def clone_to_lookbook(
        self,
        user: User,
        source_outfit_id: UUID,
        name: str,
    ) -> Outfit:
        result = await self.db.execute(
            select(Outfit)
            .where(and_(Outfit.id == source_outfit_id, Outfit.user_id == user.id))
            .options(selectinload(Outfit.items).selectinload(OutfitItem.item))
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise LookupError("source outfit not found")

        recent_cutoff = datetime.now(UTC) - timedelta(seconds=self.CLONE_SOFT_IDEMPOTENCY_SECONDS)
        recent_result = await self.db.execute(
            select(Outfit)
            .where(
                and_(
                    Outfit.user_id == user.id,
                    Outfit.cloned_from_outfit_id == source.id,
                    Outfit.scheduled_for.is_(None),
                    Outfit.created_at >= recent_cutoff,
                )
            )
            .options(
                selectinload(Outfit.items).selectinload(OutfitItem.item),
                selectinload(Outfit.feedback),
            )
        )
        existing = recent_result.scalar_one_or_none()
        if existing is not None:
            return existing

        clone = Outfit(
            user_id=user.id,
            occasion=source.occasion,
            scheduled_for=None,
            source=OutfitSource.manual,
            status=OutfitStatus.pending,
            cloned_from_outfit_id=source.id,
            source_item_id=source.source_item_id,
            name=name,
        )
        self.db.add(clone)
        await self.db.flush()

        for oi in sorted(source.items, key=lambda x: x.position):
            self.db.add(
                OutfitItem(
                    outfit_id=clone.id,
                    item_id=oi.item_id,
                    position=oi.position,
                    layer_type=oi.layer_type,
                )
            )

        feedback = self.learning.create_synthetic_feedback(
            outfit_id=clone.id, accepted=True, worn_at=None
        )
        self.db.add(feedback)

        await self.db.flush()
        return await self._reload_outfit(clone.id)

    async def wear_today(self, user: User, template_id: UUID, scheduled_for: date | None) -> Outfit:
        wear, _already_logged = await self.wear_on(user, template_id, scheduled_for)
        return wear

    async def wear_on(
        self, user: User, template_id: UUID, scheduled_for: date | None
    ) -> tuple[Outfit, bool]:
        """Return the wear and whether it was already logged for this template and date."""
        await WearService(self.db).begin(user, scheduled_for)
        target_date = scheduled_for if scheduled_for is not None else get_user_today(user)
        template = await WearService(self.db)._load(user.id, template_id)
        if template.scheduled_for is not None:
            raise OutfitNotTemplateError("wear_today requires a lookbook template")
        result = await WearService(self.db).log_worn(
            user, template, target_date, caller="wear_today"
        )
        return require_wear(result), isinstance(result, AlreadyThis)

    async def patch_outfit(
        self,
        user: User,
        outfit_id: UUID,
        name: str | None,
        items: list[UUID] | None,
    ) -> Outfit:
        result = await self.db.execute(
            select(Outfit)
            .where(and_(Outfit.id == outfit_id, Outfit.user_id == user.id))
            .options(
                selectinload(Outfit.items).selectinload(OutfitItem.item),
                selectinload(Outfit.feedback),
            )
        )
        outfit = result.scalar_one_or_none()
        if outfit is None:
            raise LookupError("outfit not found")

        if name is not None:
            outfit.name = name

        if items is not None:
            if outfit.feedback is not None and outfit.feedback.worn_at is not None:
                raise OutfitWornImmutableError("cannot modify items on a worn outfit")

            new_items = await validate_item_ownership(self.db, user.id, items)
            ordered = self._order_items_canonically(new_items)

            old_item_ids = [oi.item_id for oi in outfit.items]
            new_item_ids = [i.id for i in ordered]

            old_pairs = {tuple(sorted([a, b])) for a, b in combinations(old_item_ids, 2)}
            new_pairs = {tuple(sorted([a, b])) for a, b in combinations(new_item_ids, 2)}
            added = new_pairs - old_pairs
            removed = old_pairs - new_pairs

            self.db.expire(outfit, attribute_names=["items"])
            await self.db.execute(sa_delete(OutfitItem).where(OutfitItem.outfit_id == outfit.id))
            await self.db.flush()

            for pos, item in enumerate(ordered):
                self.db.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=pos))
            await self.db.flush()

            refreshed = await self._reload_outfit(outfit.id)
            if added or removed:
                await self.learning.process_item_diff(refreshed, removed_pairs=removed)
            outfit = refreshed

        await self.db.flush()
        return await self._reload_outfit(outfit.id)
