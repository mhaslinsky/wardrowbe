import asyncio
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from app.config import get_settings
from app.models.outfit import Outfit
from app.services.image_service import ALLOWED_EXTENSIONS
from app.services.preview_service import PreviewService, delete_preview_files, store_photo, urls
from app.utils.uploads import UploadTooLargeError, read_upload_within_limit


def photo_fields(outfit, preview_paths=None):
    photo = urls(outfit.selfie_paths) if outfit.selfie_paths else None
    choice = outfit.card_image_choice or "automatic"
    selected = (
        urls(preview_paths)
        if choice == "preview" and preview_paths
        else photo
        if choice != "preview"
        else None
    )
    return {
        "selfie_photo": photo,
        "card_image_choice": choice,
        "card_image_url": (selected.get("medium_url") or selected.get("image_url"))
        if selected
        else None,
    }


async def read_photo(image: UploadFile):
    from app.api.selfie import _validate_photo

    try:
        data = await read_upload_within_limit(image, 20)
    except UploadTooLargeError:
        raise HTTPException(413, "Photo is too large") from None
    if Path(image.filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Unsupported image format")
    await asyncio.to_thread(
        _validate_photo, data, image.content_type, get_settings().max_image_megapixels
    )
    return data


class OutfitPhotoService:
    def __init__(self, db):
        self.db = db

    async def owned(self, outfit_id, user_id):
        await PreviewService(self.db).lock_user(user_id)
        outfit = await self.db.scalar(
            select(Outfit)
            .where(Outfit.id == outfit_id, Outfit.user_id == user_id)
            .with_for_update()
        )
        if outfit is None:
            raise HTTPException(404, "Outfit not found")
        return outfit

    def clear(self, outfit):
        if outfit.selfie_paths:
            self.db.sync_session.info.setdefault("preview_cleanup", {}).update(
                {path: path for path in outfit.selfie_paths.values()}
            )
        outfit.selfie_paths = None

    async def save(self, outfit, data, filename):
        pending = asyncio.create_task(
            asyncio.to_thread(store_photo, outfit.user_id, data, filename)
        )
        paths = {}
        try:
            paths = await asyncio.shield(pending)
            self.clear(outfit)
            outfit.selfie_paths = paths
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            try:
                paths = await pending
            finally:
                delete_preview_files(paths)
            raise
