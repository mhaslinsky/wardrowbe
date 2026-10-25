import asyncio
import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import ImageOps
from sqlalchemy import select, text, update
from sqlalchemy.orm import selectinload

from app.models.outfit import Outfit, OutfitItem
from app.models.preview import OutfitPreview, PreviewPhoto, delete_preview_files
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

logger = logging.getLogger(__name__)


def urls(paths):
    return {
        name.replace("_path", "_url"): sign_image_url(path, expiry_seconds=600).replace(
            "/api/v1/images/", "/api/v1/users/me/preview-photo/files/"
        )
        for name, path in paths.items()
    }


def snapshot(outfit):
    service, records, images = ImageService(), [], []
    for link in sorted(outfit.items, key=lambda link: str(link.item_id)):
        item = link.item
        path = item.medium_path or item.image_path
        image = jpeg(path, 800) if path and service.get_image_path(path).is_file() else None
        images.append(image)
        records.append(
            {
                "id": str(link.item_id),
                "image_path": item.image_path,
                "medium_path": item.medium_path,
                "type": item.type,
                "colors": item.colors,
                "primary_color": item.primary_color,
                "layer": link.layer_type,
                "content_hash": hashlib.sha256(image).hexdigest() if image else None,
            }
        )
    key = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    return key, images, records


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
        delete_preview_files(paths)
        raise


def jpeg(path, size):
    service = ImageService()
    image = service._open_bounded(service.get_image_path(path).read_bytes(), Path(path).suffix)
    return service.encode_variant(ImageOps.exif_transpose(image), ImageVariant("", "", size, 85))


class PreviewService:
    def __init__(self, db):
        self.db = db

    async def lock_user(self, user_id, key_share=False):
        found = await self.db.scalar(
            select(User.id).where(User.id == user_id).with_for_update(key_share=key_share)
        )
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
        pending = asyncio.create_task(asyncio.to_thread(store_photo, user_id, data, filename))
        paths = {}
        try:
            paths = await asyncio.shield(pending)
            self.db.add(PreviewPhoto(user_id=user_id, paths=paths))
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            try:
                paths = await pending
            except BaseException:
                logger.warning("Reference storage failed")
            delete_preview_files(paths)
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

    def response(self, record, key):
        return {
            **urls(record.paths),
            "created_at": record.created_at,
            "stale": record.cache_key != key,
        }

    async def saved(self, outfit_id, user_id):
        outfit = await self.outfit(outfit_id, user_id)
        record = await self.db.get(OutfitPreview, outfit_id, populate_existing=True)
        if record is None or not record.paths:
            raise HTTPException(404, "NO_PREVIEW")
        key, _, _ = await asyncio.to_thread(snapshot, outfit)
        return self.response(record, key)

    async def generate(self, outfit_id, user_id, regenerate):
        paths, pending_store, claim_id = {}, None, None
        try:
            await self.outfit(outfit_id, user_id)
            acquired = await self.db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": int.from_bytes(hashlib.sha256(outfit_id.bytes).digest()[:8], signed=True)},
            )
            if not acquired:
                raise HTTPException(409, "PREVIEW_IN_PROGRESS")
            await self.lock_user(user_id, key_share=True)
            await self.db.execute(select(Outfit.id).where(Outfit.id == outfit_id).with_for_update())
            outfit = await self.outfit(outfit_id, user_id)
            record = await self.db.get(OutfitPreview, outfit_id, populate_existing=True)
            if (
                record
                and record.claim_id
                and record.started_at
                and record.started_at > datetime.now(UTC) - timedelta(minutes=15)
            ):
                raise HTTPException(409, "PREVIEW_IN_PROGRESS")
            try:
                reference = await self.reference(user_id)
            except HTTPException as error:
                raise HTTPException(409, "NO_PREVIEW_PHOTO") from error
            key, images, records = await asyncio.to_thread(snapshot, outfit)
            if record and record.paths and record.cache_key == key and not regenerate:
                result = self.response(record, key)
                await self.db.commit()
                return result
            if not photo_redraw_enabled():
                raise HTTPException(503, "Preview is disabled")
            if (
                not images
                or any(image is None for image in images)
                or any(not link.item.image_path for link in outfit.items)
            ):
                raise HTTPException(422, "Outfit photos required")
            await rate_limit_by_user(user_id, "outfit_preview", max_requests=5, window_seconds=60)
            images.insert(0, await asyncio.to_thread(jpeg, reference.paths["image_path"], 1600))
            if record is None:
                record = OutfitPreview(outfit_id=outfit_id, user_id=user_id)
                self.db.add(record)
            claim_id = uuid4().hex
            record.claim_id, record.started_at = claim_id, datetime.now(UTC)
            await self.db.commit()
            garments = [
                {field: record[field] for field in ("type", "colors", "primary_color", "layer")}
                for record in records
            ]
            data = json.dumps(garments).replace("<", "\\u003c").replace(">", "\\u003e")
            slots = (
                json.dumps(sorted({garment["type"] for garment in garments}))
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
            )
            prompt = (
                load_prompt("outfit_preview")
                + "\n<present_slots>\n"
                + slots
                + "\n</present_slots>\n<garment_data>\n"
                + data
                + "\n</garment_data>"
            )
            provider = PhotoRedrawService()
            payload = await provider.request(images, prompt, "IMAGE", mime_type="image/jpeg")
            inline = next(
                part["inlineData"]["data"]
                for part in response_parts(payload)
                if isinstance(part.get("inlineData"), dict) and part["inlineData"].get("data")
            )
            pending_store = asyncio.create_task(
                asyncio.to_thread(
                    store_photo, user_id, base64.b64decode(inline, validate=True), "preview.png"
                )
            )
            paths = await asyncio.shield(pending_store)
            await self.lock_user(user_id)
            await self.db.execute(select(Outfit.id).where(Outfit.id == outfit_id).with_for_update())
            outfit = await self.outfit(outfit_id, user_id)
            record = await self.db.get(OutfitPreview, outfit_id, populate_existing=True)
            if record is None or record.claim_id != claim_id:
                raise HTTPException(409, "Preview changed")
            current_key, _, _ = await asyncio.to_thread(snapshot, outfit)
            old_paths = record.paths
            record.paths, record.cache_key = paths, key
            record.created_at, record.usage = datetime.now(UTC), provider.usage
            record.claim_id, record.started_at = None, None
            result = self.response(record, current_key)
            await self.db.commit()
        except BaseException as error:
            if pending_store is not None:
                try:
                    paths = await pending_store
                except BaseException:
                    logger.warning("Preview storage failed")
            await self.db.rollback()
            delete_preview_files(paths)
            if claim_id is not None:
                await self.db.execute(
                    update(OutfitPreview)
                    .where(OutfitPreview.outfit_id == outfit_id, OutfitPreview.claim_id == claim_id)
                    .values(claim_id=None, started_at=None)
                )
                await self.db.commit()
            if isinstance(error, HTTPException) or not isinstance(error, Exception):
                raise
            logger.warning(
                "Preview failed: %s status=%s",
                type(error).__name__,
                getattr(getattr(error, "response", None), "status_code", None),
            )
            raise HTTPException(502, "PREVIEW_FAILED") from error
        delete_preview_files(old_paths)
        return result
