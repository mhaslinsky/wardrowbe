import base64
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import ImageOps
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.outfit import Outfit, OutfitItem
from app.models.preview import OutfitPreview, PreviewPhoto
from app.models.user import User
from app.services.image_service import ImageService, ImageVariant
from app.services.photo_redraw_service import (
    PhotoRedrawService,
    photo_redraw_enabled,
    response_parts,
)
from app.utils.prompts import load_prompt
from app.utils.rate_limit import rate_limit_by_user
from app.utils.signed_urls import sign_image_url


def urls(paths):
    return {
        name.replace("_path", "_url"): sign_image_url(path).replace(
            "/api/v1/images/", "/api/v1/users/me/preview-photo/files/"
        )
        for name, path in paths.items()
    }


def cache_key(outfit):
    records = sorted(
        (str(link.item_id), link.item.image_path, link.item.medium_path, link.layer_type)
        for link in outfit.items
    )
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def store_photo(user_id, data, filename):
    service = ImageService()
    image = ImageOps.exif_transpose(service._open_bounded(data, Path(filename).suffix.lower()))
    folder = service._get_user_path(user_id) / "previews"
    folder.mkdir(exist_ok=True)
    stem = uuid4().hex
    paths = {}
    try:
        for field, suffix, size, quality in (
            ("image_path", "", 2400, 92),
            ("medium_path", "_medium", 800, 85),
        ):
            path = f"{user_id}/previews/{stem}{suffix}.jpg"
            paths[field] = path
            service.get_image_path(path).write_bytes(
                service.encode_variant(image, ImageVariant(field, suffix, size, quality))
            )
        return paths
    except BaseException:
        service.delete_images(paths)
        raise


def png(path):
    service = ImageService()
    image = service._open_bounded(service.get_image_path(path).read_bytes(), Path(path).suffix)
    buffer = BytesIO()
    ImageOps.exif_transpose(image).convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


class PreviewService:
    def __init__(self, db):
        self.db = db

    async def lock_user(self, user_id):
        found = await self.db.scalar(select(User.id).where(User.id == user_id).with_for_update())
        if found is None:
            raise HTTPException(404, "User not found")

    async def reference(self, user_id):
        record = await self.db.scalar(
            select(PreviewPhoto)
            .where(PreviewPhoto.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise HTTPException(404, "NO_PREVIEW_PHOTO")
        return record

    async def clear(self, user_id):
        await self.lock_user(user_id)
        records = list(
            (
                await self.db.scalars(select(OutfitPreview).where(OutfitPreview.user_id == user_id))
            ).all()
        )
        reference = await self.db.get(PreviewPhoto, user_id)
        if reference is not None:
            records.append(reference)
        for record in records:
            await self.db.delete(record)
        await self.db.flush()

    async def replace_reference(self, user_id, data, filename):
        await self.clear(user_id)
        paths = store_photo(user_id, data, filename)
        try:
            self.db.add(PreviewPhoto(user_id=user_id, paths=paths))
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            ImageService().delete_images(paths)
            raise
        return urls(paths)

    async def outfit(self, outfit_id, user_id):
        outfit = await self.db.scalar(
            select(Outfit)
            .where(Outfit.id == outfit_id, Outfit.user_id == user_id)
            .options(selectinload(Outfit.items).selectinload(OutfitItem.item))
            .execution_options(populate_existing=True)
        )
        if outfit is None:
            raise HTTPException(404, "Outfit not found")
        return outfit

    def response(self, record, outfit):
        return {
            **urls(record.paths),
            "created_at": record.created_at,
            "stale": record.cache_key != cache_key(outfit),
        }

    async def saved(self, outfit_id, user_id):
        outfit = await self.outfit(outfit_id, user_id)
        record = await self.db.get(OutfitPreview, outfit_id, populate_existing=True)
        if record is None:
            raise HTTPException(404, "NO_PREVIEW")
        return self.response(record, outfit)

    async def generate(self, outfit_id, user_id, regenerate):
        await self.outfit(outfit_id, user_id)
        acquired = await self.db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": int.from_bytes(hashlib.sha256(outfit_id.bytes).digest()[:8], signed=True)},
        )
        if not acquired:
            raise HTTPException(409, "PREVIEW_IN_PROGRESS")
        paths = {}
        try:
            await self.lock_user(user_id)
            await self.db.execute(select(Outfit.id).where(Outfit.id == outfit_id).with_for_update())
            outfit = await self.outfit(outfit_id, user_id)
            try:
                reference = await self.reference(user_id)
            except HTTPException as error:
                raise HTTPException(409, "NO_PREVIEW_PHOTO") from error
            record = await self.db.get(OutfitPreview, outfit_id, populate_existing=True)
            key = cache_key(outfit)
            if record is not None and record.cache_key == key and not regenerate:
                result = self.response(record, outfit)
                await self.db.commit()
                return result
            if not photo_redraw_enabled():
                raise HTTPException(503, "Preview is disabled")
            await rate_limit_by_user(user_id, "outfit_preview", max_requests=5, window_seconds=60)
            links = sorted(outfit.items, key=lambda link: str(link.item_id))
            if not links or any(not link.item.image_path for link in links):
                raise HTTPException(422, "Outfit photos required")
            images = [png(reference.paths["image_path"])]
            records = []
            for link in links:
                images.append(png(link.item.medium_path or link.item.image_path))
                records.append(
                    {
                        "type": link.item.type,
                        "colors": link.item.colors,
                        "primary_color": link.item.primary_color,
                        "layer": link.layer_type,
                    }
                )
            provider = PhotoRedrawService()
            payload = await provider.request(
                images, load_prompt("outfit_preview") + "\n" + json.dumps(records), "IMAGE"
            )
            inline = next(
                part["inlineData"]["data"]
                for part in response_parts(payload)
                if isinstance(part.get("inlineData"), dict) and part["inlineData"].get("data")
            )
            paths = store_photo(user_id, base64.b64decode(inline, validate=True), "preview.png")
            old_paths = record.paths if record is not None else {}
            if record is None:
                record = OutfitPreview(outfit_id=outfit_id, user_id=user_id)
                self.db.add(record)
            record.paths, record.cache_key = paths, key
            record.created_at, record.usage = datetime.now(UTC), provider.usage
            result = self.response(record, outfit)
            await self.db.commit()
        except BaseException as error:
            await self.db.rollback()
            ImageService().delete_images(paths)
            if isinstance(error, (HTTPException, KeyboardInterrupt, SystemExit)):
                raise
            if not isinstance(error, Exception):
                raise
            raise HTTPException(502, "PREVIEW_FAILED") from error
        ImageService().delete_images(old_paths)
        return result
