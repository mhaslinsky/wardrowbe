import asyncio
import logging
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image
from pillow_heif import register_heif_opener
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from app.api.outfits import outfit_to_response
from app.config import get_settings
from app.database import DbSession
from app.models.item import ClothingItem, ItemStatus
from app.models.user import User
from app.schemas.item import ItemResponse
from app.schemas.outfit import OutfitResponse
from app.services.ai_service import AIDisabledError, get_ai_service
from app.services.image_service import ALLOWED_MIME_TYPES
from app.services.learning_service import LearningService
from app.services.selfie_confirm_service import SelfieConfirmService
from app.services.selfie_service import candidates_for, match_selfie
from app.services.suggestion_cache import clear_suggestions
from app.services.wear_service import wear_http
from app.utils.auth import get_current_user
from app.utils.rate_limit import rate_limit_by_user
from app.utils.uploads import UploadTooLargeError, read_upload_within_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/selfie", tags=["Selfie"])


class SelfieResponse(BaseModel):
    matches: list[ItemResponse]
    considered: int


@router.post("", response_model=SelfieResponse)
async def recognise_outfit(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
    image: UploadFile = File(...),
) -> SelfieResponse:
    """Suggest wardrobe items without logging or retaining the photo."""
    settings = get_settings()
    # The photo goes to the vision model: nothing is sent when vision is switched off.
    if not settings.effective_ai_vision_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The AI is turned off"
        )
    # Each request is a full vision-model call.
    await rate_limit_by_user(current_user.id, "selfie", max_requests=10, window_seconds=60)
    try:
        content = await read_upload_within_limit(image, settings.max_upload_size_mb)
    except UploadTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(error)
        ) from None
    await asyncio.to_thread(
        _validate_photo, content, image.content_type, settings.max_image_megapixels
    )
    try:
        ai = get_ai_service()
    except AIDisabledError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The AI is turned off"
        ) from None

    result = await db.execute(
        select(ClothingItem)
        .where(
            and_(
                ClothingItem.user_id == current_user.id,
                ClothingItem.status == ItemStatus.ready,
                ClothingItem.is_archived.is_(False),
            )
        )
        .options(selectinload(ClothingItem.additional_images))
        .order_by(ClothingItem.last_worn_at.desc().nulls_last(), ClothingItem.created_at.desc())
    )
    items = candidates_for(
        [item for item in result.scalars().all() if item.type and item.type != "unknown"]
    )
    # Hand the connection back before waiting on the model, which can take a while.
    await db.commit()

    with tempfile.TemporaryDirectory() as folder:
        photo = Path(folder) / "selfie"
        photo.write_bytes(content)
        try:
            matches = await match_selfie(ai, photo, items)
        except Exception:
            logger.warning("Selfie recognition failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The AI could not read the photo. Try again in a moment.",
            ) from None

    return SelfieResponse(
        matches=[ItemResponse.model_validate(item) for item in matches], considered=len(items)
    )


def _pixels(content: bytes) -> int:
    try:
        with Image.open(BytesIO(content)) as photo:
            width, height = photo.size
    except Image.DecompressionBombError:
        raise HTTPException(status_code=413, detail="Photo is too large") from None
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file") from None
    return width * height


def _validate_photo(content: bytes, content_type: str | None, max_megapixels: float) -> None:
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported image format")
    register_heif_opener()
    # Reject oversized headers before verify or load can decode pixels.
    if _pixels(content) > max_megapixels * 1_000_000:
        raise HTTPException(status_code=413, detail="Photo is too large")
    try:
        with Image.open(BytesIO(content)) as photo:
            if photo.format not in {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}:
                raise ValueError("Unsupported image format")
            photo.verify()
        with Image.open(BytesIO(content)) as photo:
            photo.load()
    except Image.DecompressionBombError:
        raise HTTPException(status_code=413, detail="Photo is too large") from None
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file") from None


class SelfieConfirmRequest(BaseModel):
    request_id: UUID
    item_ids: list[UUID]


@router.post("/confirm", response_model=OutfitResponse)
@wear_http()
async def confirm_outfit(
    request: SelfieConfirmRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
) -> OutfitResponse:
    try:
        outfit, created = await SelfieConfirmService(db).confirm(
            current_user, request.request_id, request.item_ids
        )
        response = outfit_to_response(outfit)
        outfit_id, user_id, occasion = outfit.id, current_user.id, outfit.occasion
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    if created:
        try:
            await LearningService(db).process_feedback(outfit_id, user_id)
        except Exception:
            await db.rollback()
            logger.exception("Selfie learning failed for outfit %s", outfit_id)
        try:
            await clear_suggestions(user_id, occasion)
        except Exception:
            logger.exception("Selfie suggestion cache clear failed for user %s", user_id)
    return response
