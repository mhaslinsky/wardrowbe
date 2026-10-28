from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy import select

from app.database import DbSession
from app.models.outfit import Outfit
from app.models.user import User
from app.services.ai_service import AIDisabledError
from app.services.inspiration_service import InspirationService
from app.services.weather_service import WeatherServiceError
from app.utils.auth import get_current_user
from app.utils.rate_limit import rate_limit_by_user

router = APIRouter(prefix="/inspiration", tags=["Inspiration"])
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", status_code=201)
async def create_inspiration(db: DbSession, user: CurrentUser, image: UploadFile = File(...)):
    await rate_limit_by_user(str(user.id), "inspiration", max_requests=5, window_seconds=60)
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Use JPEG, PNG or WebP")
    image_bytes = await image.read(10 * 1024 * 1024 + 1)
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image exceeds 10 MB")
    try:
        return await InspirationService(db).create(user, image_bytes)
    except AIDisabledError as error:
        raise HTTPException(503, "Image analysis is disabled") from error
    except (ValidationError, RuntimeError, WeatherServiceError) as error:
        raise HTTPException(502, "Image analysis failed") from error
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise HTTPException(422, "Use a valid image and set your location") from error


@router.get("")
async def list_inspiration(db: DbSession, user: CurrentUser):
    outfits = await db.scalars(
        select(Outfit)
        .where(Outfit.user_id == user.id, Outfit.ai_raw_response.has_key("inspiration"))
        .order_by(Outfit.created_at.desc())
        .limit(50)
    )
    service = InspirationService(db)
    return [service.response(outfit) for outfit in outfits]


@router.get("/{outfit_id}/image")
async def inspiration_image(
    outfit_id: UUID,
    db: DbSession,
    user: CurrentUser,
):
    outfit = await db.get(Outfit, outfit_id)
    if outfit is None or not (outfit.ai_raw_response or {}).get("inspiration"):
        raise HTTPException(404, "Look not found")
    if user.id != outfit.user_id:
        raise HTTPException(401, "Access denied")
    path = InspirationService(db).image_path(outfit.user_id, outfit.id)
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )


@router.delete("/{outfit_id}", status_code=204)
async def delete_inspiration(outfit_id: UUID, db: DbSession, user: CurrentUser):
    service = InspirationService(db)
    outfit = await db.scalar(
        select(Outfit)
        .where(
            Outfit.id == outfit_id,
            Outfit.user_id == user.id,
            Outfit.ai_raw_response.has_key("inspiration"),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if outfit is None:
        raise HTTPException(404, "Look not found")
    await db.delete(outfit)
    await db.commit()
    service.cleanup_image(outfit)
