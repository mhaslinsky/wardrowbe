import logging
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import DbSession
from app.models.item import ClothingItem, ItemStatus
from app.models.user import User
from app.schemas.item import ItemResponse
from app.services.ai_service import AIDisabledError, get_ai_service
from app.services.image_service import ImageService
from app.services.selfie_service import candidates_for, match_selfie
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
    if not ImageService().validate_image(content, image.content_type or "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image file. Supported formats: JPEG, PNG, WebP, HEIC",
        )
    # Header only: a small file can still decode to a huge bitmap, so the pixel ceiling the
    # uploads use is checked before the photo is ever decoded.
    if _pixels(content) > settings.max_image_megapixels * 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"The photo is over {settings.max_image_megapixels:g} megapixels",
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
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file") from None
    return width * height
