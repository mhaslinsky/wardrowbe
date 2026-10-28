import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, field_validator

from app.database import DbSession
from app.models.user import User
from app.schemas.user import DisplayName, Latitude, Longitude, PlaceName
from app.services.user_service import UserService
from app.utils.auth import get_current_user
from app.utils.locale import SUPPORTED_LOCALES, is_supported_locale
from app.utils.numbers import is_finite_number
from app.utils.timezone import is_valid_timezone

router = APIRouter(prefix="/users/me", tags=["Users"])


class OnboardingCompleteResponse(BaseModel):
    onboarding_completed: bool


class UserProfileResponse(BaseModel):
    id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    timezone: str
    locale: str
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = None
    family_id: str | None = None
    role: str
    onboarding_completed: bool
    body_measurements: dict | None = None


class UserProfileUpdate(BaseModel):
    # Reject unknown keys instead of the Pydantic default of silently
    # dropping them - a client sending a misnamed field (e.g. "timeZone")
    # would otherwise get a 200 with nothing actually updated, indistinguishable
    # from a real success.
    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    timezone: str | None = None
    locale: str | None = None
    location_lat: Latitude | None = None
    location_lon: Longitude | None = None
    location_name: PlaceName | None = None
    body_measurements: dict | None = None

    # The column is NOT NULL, so an explicit null must be refused here instead of failing the flush.
    @field_validator("display_name")
    @classmethod
    def _display_name_is_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("display_name cannot be null")
        return value


@router.get("", response_model=UserProfileResponse)
async def get_profile(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileResponse:
    return _user_response(current_user)


@router.patch("", response_model=UserProfileResponse)
async def update_profile(
    data: UserProfileUpdate,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileResponse:
    update_data = data.model_dump(exclude_unset=True)

    # update_data is applied with a blanket setattr below, so an unsupported locale
    # must be rejected here to prevent it reaching the column.
    if "locale" in update_data and not is_supported_locale(update_data["locale"]):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"locale must be one of: {', '.join(SUPPORTED_LOCALES)}",
        )

    # The settings page resends the stored timezone with every location save, so a
    # zone stored before validation existed must not block that save.
    new_timezone = update_data.get("timezone", current_user.timezone)
    if new_timezone != current_user.timezone and not is_valid_timezone(new_timezone):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="timezone must be an IANA timezone name such as Europe/London",
        )

    if "body_measurements" in update_data and update_data["body_measurements"] is not None:
        numeric_keys = {"chest", "waist", "hips", "inseam", "height", "weight"}
        for key, value in update_data["body_measurements"].items():
            if key not in numeric_keys or value is None:
                continue
            # Numeric fields are interpolated into the AI prompt, so anything other than a
            # real positive number (strings, bools, NaN/inf) is rejected, not just <= 0.
            if not is_finite_number(value) or value <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{key} must be a positive number",
                )

    for field, value in update_data.items():
        setattr(current_user, field, value)

    await db.flush()
    await db.refresh(current_user)
    await db.commit()

    return _user_response(current_user)


def _user_response(user: User) -> UserProfileResponse:
    return UserProfileResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        timezone=user.timezone,
        locale=user.locale,
        location_lat=float(user.location_lat) if user.location_lat is not None else None,
        location_lon=float(user.location_lon) if user.location_lon is not None else None,
        location_name=user.location_name,
        family_id=str(user.family_id) if user.family_id else None,
        role=user.role,
        onboarding_completed=user.onboarding_completed,
        body_measurements=user.body_measurements,
    )


@router.post("/onboarding/complete", response_model=OnboardingCompleteResponse)
async def complete_onboarding(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingCompleteResponse:
    user_service = UserService(db)
    await user_service.complete_onboarding(current_user)
    await db.commit()

    return OnboardingCompleteResponse(onboarding_completed=True)


@router.get("/preview-photo")
async def get_preview_photo(
    db: DbSession, current_user: Annotated[User, Depends(get_current_user)]
):
    from app.services.preview_service import PreviewService, reference_urls

    return reference_urls(
        (await PreviewService(db).reference(current_user.id, require_front=False)).paths
    )


@router.put("/preview-photo")
async def put_preview_photo(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
    image: UploadFile,
    angle: Literal["front", "quarter", "side"] = "front",
):
    from app.services.image_service import ALLOWED_EXTENSIONS, ImageService
    from app.services.preview_service import PreviewService

    data = await image.read(20 * 1024 * 1024 + 1)
    if Path(
        image.filename or ""
    ).suffix.lower() not in ALLOWED_EXTENSIONS or not ImageService().validate_image(
        data, image.content_type
    ):
        raise HTTPException(400, "Invalid image")
    try:
        return await PreviewService(db).replace_reference(
            current_user.id, data, image.filename, angle
        )
    except (ValueError, OSError) as error:
        await db.rollback()
        raise HTTPException(400, "Invalid image") from error


@router.delete("/preview-photo", status_code=204)
async def delete_preview_photo(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
    angle: Literal["front", "quarter", "side"] = "front",
):
    from app.services.preview_service import PreviewService

    await PreviewService(db).clear(current_user.id, angle)
    await db.commit()


@router.get("/preview-photo/files/{user_id}/previews/{filename}")
async def get_preview_file(
    user_id: UUID,
    filename: str,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
):
    from sqlalchemy import select

    from app.models.outfit import Outfit
    from app.models.preview import OutfitPreview, PreviewPhoto, preview_file_paths
    from app.services.image_service import ImageService

    path = f"{user_id}/previews/{filename}"
    if current_user.id != user_id or not re.fullmatch(r"[a-f0-9]{32}(?:_medium)?\.jpg", filename):
        raise HTTPException(401, "Access denied")
    records = [
        await db.get(PreviewPhoto, user_id),
        *(await db.scalars(select(OutfitPreview).where(OutfitPreview.user_id == user_id))).all(),
    ]
    selfie_paths = await db.scalars(
        select(Outfit.selfie_paths).where(
            Outfit.user_id == user_id, Outfit.selfie_paths.is_not(None)
        )
    )
    if not any(
        record is not None and path in preview_file_paths(record.paths) for record in records
    ) and not any(
        paths is not None and path in preview_file_paths(paths) for paths in selfie_paths
    ):
        raise HTTPException(404, "Image not found")
    target = ImageService().get_image_path(path)
    if not target.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(
        target, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )
