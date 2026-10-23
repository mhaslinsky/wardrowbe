"""Home Assistant actions: log today's scheduled outfit as worn, and mark laundry done.

HA reaches only these routes, authenticated by a shared token, so it never holds an identity that
could edit or delete the catalog (PER-160).
"""

import hmac
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import DbSession
from app.models.item import ClothingItem
from app.models.outfit import Outfit, OutfitSource, OutfitStatus, UserFeedback
from app.models.user import User
from app.services.item_service import ItemService
from app.services.learning_service import LearningService
from app.utils.timezone import get_user_today

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/ha", tags=["home-assistant"])

FOOTWEAR_TYPES = frozenset({"shoes", "sneakers", "boots", "sandals"})


def _error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error": error})


def _token_ok(request: Request) -> bool:
    expected = settings.wardrowbe_ha_token
    supplied = request.headers.get("x-ha-token")
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


async def _ha_user(db: DbSession) -> User | None:
    result = await db.execute(select(User).where(User.email == settings.ha_user_email))
    return result.scalar_one_or_none()


@router.post("/wore-today")
async def wore_today(request: Request, db: DbSession) -> JSONResponse:
    if not _token_ok(request):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthorized")
    user = await _ha_user(db)
    if user is None:
        return _error(status.HTTP_404_NOT_FOUND, "user_not_found")

    today = get_user_today(user)
    # Row locks ensure two concurrent calls log only one wear.
    result = await db.execute(
        select(Outfit)
        .where(
            Outfit.user_id == user.id,
            Outfit.source == OutfitSource.scheduled,
            Outfit.scheduled_for == today,
        )
        .order_by(Outfit.created_at.desc())
        .options(selectinload(Outfit.items), selectinload(Outfit.feedback))
        .with_for_update(of=Outfit)
    )
    outfits = list(result.scalars().all())
    if not outfits:
        return _error(status.HTTP_404_NOT_FOUND, "no_outfit_today")

    # A day gets one wear even when it has several scheduled outfits.
    worn = next((o for o in outfits if o.feedback and o.feedback.worn_at), None)
    if worn is not None:
        return JSONResponse(
            {
                "ok": True,
                "outfit_id": str(worn.id),
                "date": today.isoformat(),
                "already_logged": True,
            }
        )

    outfit = outfits[0]
    feedback = outfit.feedback
    if feedback is None:
        feedback = UserFeedback(outfit_id=outfit.id)
        outfit.feedback = feedback
        db.add(feedback)
    feedback.accepted = True
    feedback.actually_worn = True
    feedback.worn_at = today
    outfit.status = OutfitStatus.accepted
    outfit.responded_at = datetime.now(UTC)
    await ItemService(db).record_wears(
        user.id,
        [outfit_item.item_id for outfit_item in outfit.items],
        today,
        occasion=outfit.occasion,
        outfit_id=outfit.id,
    )
    await db.commit()

    try:
        await LearningService(db).process_feedback(outfit.id, user.id)
    except Exception:
        logger.exception("learning process_feedback failed for outfit %s", outfit.id)

    return JSONResponse(
        {
            "ok": True,
            "outfit_id": str(outfit.id),
            "date": today.isoformat(),
            "already_logged": False,
        }
    )


@router.post("/laundry-done")
async def laundry_done(request: Request, db: DbSession) -> JSONResponse:
    if not _token_ok(request):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthorized")
    user = await _ha_user(db)
    if user is None:
        return _error(status.HTTP_404_NOT_FOUND, "user_not_found")

    result = await db.execute(
        select(ClothingItem).where(
            ClothingItem.user_id == user.id,
            ClothingItem.is_archived.is_(False),
            ClothingItem.needs_wash.is_(True),
            ClothingItem.type.not_in(FOOTWEAR_TYPES),
        )
    )
    items = list(result.scalars().all())
    today = get_user_today(user)
    item_service = ItemService(db)
    for item in items:
        await item_service.log_wash(item=item, washed_at=today)
    await db.commit()
    return JSONResponse({"ok": True, "washed": len(items)})
