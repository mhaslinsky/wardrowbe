"""Home Assistant actions: log today's scheduled outfit as worn, and mark laundry done.

HA reaches only these routes, authenticated by a shared token, so it never holds an identity that
could edit or delete the catalog (PER-160).
"""

import hmac
import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import DbSession
from app.models.item import ClothingItem
from app.models.outfit import Outfit, OutfitSource, UserFeedback
from app.models.user import User
from app.services.item_service import ItemService
from app.services.learning_service import LearningService
from app.services.wear_service import AlreadyThis, DayLogged, WearService
from app.utils.garment_vocabulary import types_for_role
from app.utils.timezone import get_user_today

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/ha", tags=["home-assistant"])

FOOTWEAR_TYPES = frozenset(types_for_role("footwear"))


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
    result = await db.execute(
        select(Outfit)
        .where(
            Outfit.user_id == user.id,
            Outfit.source == OutfitSource.scheduled,
            Outfit.scheduled_for == today,
        )
        .order_by(Outfit.created_at.desc())
        .options(selectinload(Outfit.items), selectinload(Outfit.feedback))
    )
    outfits = list(result.scalars().all())
    if not outfits:
        logged = await db.execute(
            select(Outfit.id)
            .where(Outfit.user_id == user.id, Outfit.feedback.has(UserFeedback.worn_at == today))
            .order_by(Outfit.id)
        )
        existing_id = logged.scalars().first()
        if existing_id is None:
            return _error(status.HTTP_404_NOT_FOUND, "no_outfit_today")
        return JSONResponse(
            {
                "ok": True,
                "outfit_id": str(existing_id),
                "date": today.isoformat(),
                "already_logged": True,
            }
        )
    result = await WearService(db).log_worn(user, outfits[0], today, caller="ha")
    if isinstance(result, (AlreadyThis, DayLogged)):
        outfit_id = result.existing_ids[0] if isinstance(result, DayLogged) else result.outfit.id
        await db.commit()
        return JSONResponse(
            {
                "ok": True,
                "outfit_id": str(outfit_id),
                "date": today.isoformat(),
                "already_logged": True,
            }
        )
    outfit = result.outfit
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

    # A concurrent call waits on these row locks, then rechecks needs_wash and washes nothing.
    result = await db.execute(
        select(ClothingItem)
        .where(
            ClothingItem.user_id == user.id,
            ClothingItem.is_archived.is_(False),
            ClothingItem.needs_wash.is_(True),
            ClothingItem.type.not_in(FOOTWEAR_TYPES),
        )
        .with_for_update()
    )
    items = list(result.scalars().all())
    today = get_user_today(user)
    item_service = ItemService(db)
    for item in items:
        await item_service.log_wash(item=item, washed_at=today)
    await db.commit()
    return JSONResponse({"ok": True, "washed": len(items)})
