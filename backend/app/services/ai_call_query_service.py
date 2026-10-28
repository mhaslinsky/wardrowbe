from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, and_, cast, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_call import AICall
from app.models.considering import ItemPurchase
from app.models.item import ClothingItem
from app.models.outfit import FamilyOutfitRating, Outfit, UserFeedback
from app.models.preview import PreviewFeedback
from app.schemas.ai_call import AICallPage, AICallRead, CallFeedback


def linked_call_id(metadata: dict | None) -> UUID | None:
    """Read a correlation ID without requiring its recorded row to exist."""
    try:
        return UUID(str((metadata or {}).get("_ai_call_id")))
    except (ValueError, TypeError, AttributeError):
        return None


class AICallQueryService:
    """Read calls with current feedback, scoped to the call owner's records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: UUID, call_id: UUID) -> AICallRead | None:
        """Return an owner's call or None, including for a dropped recording."""
        call = await self.db.scalar(
            select(AICall).where(AICall.id == call_id, AICall.user_id == user_id)
        )
        return (await self._enrich([call], user_id))[0] if call else None

    async def page(
        self, user_id: UUID | None, limit: int = 100, cursor: str | None = None
    ) -> AICallPage:
        """Page in creation order; None selects the server-only all-user export."""
        query = select(AICall)
        if user_id is not None:
            query = query.where(AICall.user_id == user_id)
        if cursor:
            timestamp, identifier = cursor.split(",", 1)
            created_at = datetime.fromisoformat(timestamp)
            if created_at.utcoffset() is None:
                raise ValueError("Cursor requires timezone")
            query = query.where(
                tuple_(AICall.created_at, AICall.id) > (created_at, UUID(identifier))
            )
        rows = list(
            await self.db.scalars(query.order_by(AICall.created_at, AICall.id).limit(limit + 1))
        )
        next_cursor = (
            f"{rows[limit - 1].created_at.isoformat()},{rows[limit - 1].id}"
            if len(rows) > limit
            else None
        )
        return AICallPage(items=await self._enrich(rows[:limit], user_id), next_cursor=next_cursor)

    async def _enrich(self, calls: list[AICall], author_id: UUID | None) -> list[AICallRead]:
        results = {call.id: AICallRead.model_validate(call) for call in calls}
        if not results:
            return []
        links = (
            select(AICall.id.label("call_id"), Outfit.id.label("outfit_id"))
            .join(
                Outfit,
                and_(
                    Outfit.user_id == AICall.user_id,
                    Outfit.ai_raw_response["_ai_call_id"].astext == cast(AICall.id, Text),
                ),
            )
            .where(AICall.id.in_(results))
            .subquery()
        )
        for model, kind, fields in (
            (
                UserFeedback,
                "outfit",
                (
                    "accepted",
                    "rating",
                    "comfort_rating",
                    "style_rating",
                    "comment",
                    "comment_updated_at",
                    "worn_at",
                    "worn_with_modifications",
                    "modification_notes",
                    "actually_worn",
                ),
            ),
            (FamilyOutfitRating, "family_rating", ("rating", "comment", "updated_at")),
        ):
            query = select(links.c.call_id, model).join(model, model.outfit_id == links.c.outfit_id)
            if model is FamilyOutfitRating and author_id is not None:
                query = query.where(FamilyOutfitRating.user_id == author_id)
            for call_id, feedback in await self.db.execute(query):
                results[call_id].feedback.append(
                    CallFeedback(
                        id=feedback.id,
                        kind=kind,
                        user_id=feedback.user_id
                        if model is FamilyOutfitRating
                        else results[call_id].user_id,
                        outfit_id=feedback.outfit_id,
                        created_at=feedback.created_at,
                        data={field: getattr(feedback, field) for field in fields},
                    )
                )
        query = (
            select(PreviewFeedback)
            .join(
                AICall,
                and_(
                    PreviewFeedback.ai_call_id == AICall.id,
                    PreviewFeedback.user_id == AICall.user_id,
                ),
            )
            .where(AICall.id.in_(results))
        )
        for feedback in await self.db.scalars(query):
            results[feedback.ai_call_id].feedback.append(
                CallFeedback(
                    id=feedback.id,
                    kind="preview_" + feedback.kind,
                    user_id=feedback.user_id,
                    outfit_id=feedback.outfit_id,
                    created_at=feedback.created_at,
                    data={"note": feedback.note, "preview_created_at": feedback.preview_created_at},
                )
            )
        query = (
            select(AICall.id, ItemPurchase)
            .select_from(AICall)
            .join(
                ClothingItem,
                and_(
                    ClothingItem.user_id == AICall.user_id,
                    or_(
                        and_(AICall.resource_type == "item", AICall.resource_id == ClothingItem.id),
                        and_(
                            AICall.resource_type == "outfit",
                            select(Outfit.id)
                            .where(
                                Outfit.id == AICall.resource_id,
                                Outfit.user_id == AICall.user_id,
                                Outfit.source_item_id == ClothingItem.id,
                            )
                            .exists(),
                        ),
                    ),
                ),
            )
            .join(ItemPurchase, ItemPurchase.item_id == ClothingItem.id)
            .where(AICall.id.in_(results))
        )
        for call_id, purchase in await self.db.execute(query):
            results[call_id].feedback.append(
                CallFeedback(
                    id=purchase.item_id,
                    kind="purchase",
                    user_id=results[call_id].user_id,
                    created_at=purchase.created_at,
                    data={
                        "state": purchase.state,
                        "updated_at": purchase.updated_at,
                        "passed_reason": purchase.passed_reason,
                    },
                )
            )
        return list(results.values())
