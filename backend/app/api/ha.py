"""Home Assistant actions: log today's scheduled outfit as worn, mark laundry done, and read
today's outfit with its item thumbnails for the wall tablets.

HA reaches only these routes, authenticated by a shared token, so it never holds an identity that
could edit or delete the catalog (PER-160).
"""

import base64
import binascii
import hmac
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import DbSession
from app.models.considering import owned_item
from app.models.item import ClothingItem
from app.models.outfit import Outfit, OutfitItem, OutfitSource, UserFeedback
from app.models.user import User
from app.services.image_service import ImageService
from app.services.item_service import ItemService
from app.services.learning_service import LearningService
from app.services.wear_service import AlreadyThis, DayLogged, WearService, map_wear_error, wear_http
from app.utils.garment_vocabulary import types_for_role
from app.utils.timezone import get_user_today
from app.utils.washing import never_washed

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/ha", tags=["home-assistant"])

FOOTWEAR_TYPES = frozenset(types_for_role("footwear"))
TODAY_ITEM_LIMIT = 6


def _error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error": error})


def _token_ok(request: Request) -> bool:
    return _matches_token(request.headers.get("x-ha-token"))


def _matches_token(supplied: str | None) -> bool:
    expected = settings.wardrowbe_ha_token
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


# HA's generic camera can send only basic auth, so the thumbnail route also takes the same
# shared token as the basic-auth password. It is still a header secret, never a signed link (PER-263).
def _basic_password(request: Request) -> str | None:
    scheme, _, encoded = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    return decoded.partition(":")[2] or None


async def _ha_user(db: DbSession) -> User | None:
    result = await db.execute(select(User).where(User.email == settings.ha_user_email))
    return result.scalar_one_or_none()


@router.post("/wore-today")
@wear_http(ha=True)
async def wore_today(request: Request, db: DbSession) -> JSONResponse:
    await WearService(db).begin(ha_email=settings.ha_user_email, authorized=_token_ok(request))
    user = await _ha_user(db)
    if user is None:
        raise HTTPException(404, detail="user_not_found")
    today = get_user_today(user)
    existing_id = await db.scalar(
        select(Outfit.id)
        .where(Outfit.user_id == user.id, Outfit.feedback.has(UserFeedback.worn_at == today))
        .order_by(Outfit.id)
    )
    if existing_id is not None:
        return map_wear_error(DayLogged([existing_id]), ha=True, worn_on=today)
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
        return _error(status.HTTP_404_NOT_FOUND, "no_outfit_today")
    result = await WearService(db).log_worn(user, outfits[0], today, caller="ha")
    if isinstance(result, DayLogged):
        await db.commit()
        return map_wear_error(result, ha=True, worn_on=today)
    outfit = result.outfit
    await db.commit()

    if not isinstance(result, AlreadyThis):
        try:
            await LearningService(db).process_feedback(outfit.id, user.id)
        except Exception:
            logger.exception("learning process_feedback failed for outfit %s", outfit.id)

    return JSONResponse(
        {
            "ok": True,
            "outfit_id": str(outfit.id),
            "date": today.isoformat(),
            "already_logged": isinstance(result, AlreadyThis),
        }
    )


async def _todays_outfit(db: DbSession, user: User) -> Outfit | None:
    """The outfit logged as worn today, else the newest one scheduled for today, as wore-today picks."""
    today = get_user_today(user)
    for condition in (
        Outfit.feedback.has(UserFeedback.worn_at == today),
        (Outfit.source == OutfitSource.scheduled) & (Outfit.scheduled_for == today),
    ):
        outfit = await db.scalar(
            select(Outfit)
            .where(Outfit.user_id == user.id, condition)
            .order_by(Outfit.created_at.desc())
            .limit(1)
            .options(selectinload(Outfit.items).selectinload(OutfitItem.item))
        )
        if outfit is not None:
            return outfit
    return None


def _owned_items(outfit: Outfit, user: User) -> list[ClothingItem]:
    entries = sorted(outfit.items, key=lambda entry: entry.position)
    return [entry.item for entry in entries if entry.item.user_id == user.id][:TODAY_ITEM_LIMIT]


def _item_label(item: ClothingItem) -> str:
    if item.name and item.name.strip():
        return item.name.strip()
    return " ".join(part for part in (item.primary_color, item.type) if part)


@router.get("/today")
async def today_outfit(request: Request, db: DbSession) -> JSONResponse:
    if not _token_ok(request):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthorized")
    user = await _ha_user(db)
    if user is None:
        return _error(status.HTTP_404_NOT_FOUND, "user_not_found")
    today = get_user_today(user)
    outfit = await _todays_outfit(db, user)
    items = _owned_items(outfit, user) if outfit is not None else []
    return JSONResponse(
        {
            "ok": True,
            "date": today.isoformat(),
            "outfit_id": str(outfit.id) if outfit is not None else None,
            "items": [
                {
                    "id": str(item.id),
                    "name": _item_label(item),
                    "has_photo": bool(item.thumbnail_path),
                }
                for item in items
            ],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/items/{item_id}/thumbnail")
async def today_item_thumbnail(item_id: UUID, request: Request, db: DbSession) -> Response:
    if not (_token_ok(request) or _matches_token(_basic_password(request))):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthorized")
    user = await _ha_user(db)
    if user is None:
        return _error(status.HTTP_404_NOT_FOUND, "user_not_found")
    outfit = await _todays_outfit(db, user)
    items = _owned_items(outfit, user) if outfit is not None else []
    item = next((candidate for candidate in items if candidate.id == item_id), None)
    if item is None:
        return _error(status.HTTP_404_NOT_FOUND, "not_in_todays_outfit")
    if not item.thumbnail_path:
        return _error(status.HTTP_404_NOT_FOUND, "no_photo")
    image_service = ImageService()
    image_path = image_service.get_image_path(item.thumbnail_path)
    if not image_path.resolve().is_relative_to(image_service.storage_path.resolve()):
        return _error(status.HTTP_404_NOT_FOUND, "no_photo")
    if not image_path.is_file():
        return _error(status.HTTP_404_NOT_FOUND, "no_photo")
    return FileResponse(
        path=str(image_path),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
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
            owned_item(),
            ClothingItem.user_id == user.id,
            ClothingItem.is_archived.is_(False),
            ClothingItem.needs_wash.is_(True),
            ClothingItem.type.not_in(FOOTWEAR_TYPES),
            ~never_washed(ClothingItem),
        )
        .order_by(ClothingItem.id)
        .with_for_update()
    )
    items = list(result.scalars().all())
    today = get_user_today(user)
    item_service = ItemService(db)
    for item in items:
        await item_service.log_wash(item=item, washed_at=today)
    await db.commit()
    return JSONResponse({"ok": True, "washed": len(items)})
