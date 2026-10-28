import asyncio
import base64
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import ImageOps
from sqlalchemy import select, text, update
from sqlalchemy.orm import selectinload, undefer

from app.models.outfit import Outfit, OutfitItem
from app.models.preference import UserPreference
from app.models.preview import OutfitPreview, PreviewFeedback, PreviewPhoto, delete_preview_files
from app.models.user import User
from app.services.ai_call_query_service import linked_call_id
from app.services.ai_telemetry_service import CallContext
from app.services.image_service import ImageService, ImageVariant
from app.services.photo_redraw_service import (
    PhotoRedrawService,
    photo_redraw_enabled,
    response_parts,
)
from app.services.preview_face import face_reference, reference_hash
from app.utils.garment_vocabulary import ITEM_ROLE
from app.utils.photo_urls import image_url
from app.utils.prompts import load_prompt
from app.utils.rate_limit import rate_limit_by_user

logger = logging.getLogger(__name__)
ANGLES = ("front", "quarter", "side")
FACE_REFERENCE_TIMEOUT = 3.0
DRAWING_MEASUREMENT_FIELDS = frozenset(
    ("height", "inseam", "shirt_size", "pants_size", "shoe_size")
)


def reference_urls(
    paths: dict[str, dict[str, str]],
) -> dict[str, str | dict[str, dict[str, str]]]:
    """Return photo URLs for every angle and the legacy front fields."""
    return {
        **urls(paths.get("front", {})),
        "photos": {angle: urls(paths[angle]) for angle in ANGLES if angle in paths},
    }


def reference_key(
    key: str,
    paths: dict[str, dict[str, str]],
    measurements: dict[str, object] | None = None,
    face_hash: str | None = None,
) -> str:
    """Hash outfit, reference, measurement and face inputs for cache validation."""
    return hashlib.sha256(
        json.dumps([key, paths, measurements, face_hash], sort_keys=True).encode()
    ).hexdigest()


def urls(paths):
    return {
        name.replace("_path", "_url"): image_url(path).replace(
            "/photos/images/", "/photos/users/me/preview-photo/files/"
        )
        for name, path in paths.items()
    }


def garment_size(notes: str | None) -> str | None:
    if not notes:
        return None
    match = re.search(r"(?:^|[|\n])[ \t]*Size:[ \t]*([^|\n]*)", notes, re.IGNORECASE)
    return match.group(1).strip()[:100] or None if match else None


def legacy_requests_tucking(style_notes: str | None) -> bool:
    for clause in re.split(r"[.!?;\n]|\bbut\b", (style_notes or "").lower()):
        if re.search(r"\btuck(?:ed|ing)?\b", clause) and not re.search(
            r"\b(?:not|never|no|avoid|without|untucked)\b|don['’]t", clause
        ):
            return True
    return False


def mentions_tuck(text: str | None) -> bool:
    return re.search(r"\b(?:un)?tuck", text or "", re.IGNORECASE) is not None


def resolve_tucking(note, style_notes, setting):
    for source in (note, style_notes):
        if mentions_tuck(source):
            return legacy_requests_tucking(source)
    return setting == "tucked"


def preferences_hash(preferences):
    content = {"lines": preferences["lines"], "tuck": preferences["tuck"]}
    if content == {"lines": [], "tuck": "outfit"}:
        return None
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def with_preferences(key, preferences_key):
    return hashlib.sha256((key + preferences_key).encode()).hexdigest() if preferences_key else key


def snapshot(outfit):
    service, records, images = ImageService(), [], []
    for link in sorted(outfit.items, key=lambda link: str(link.item_id)):
        item = link.item
        path = item.medium_path or item.image_path
        image = jpeg(path, 800) if path and service.get_image_path(path).is_file() else None
        images.append(image)
        size = garment_size(item.notes)
        records.append(
            {
                "id": str(link.item_id),
                "image_path": item.image_path,
                "medium_path": item.medium_path,
                "type": item.type,
                "colors": item.colors,
                "primary_color": item.primary_color,
                "layer": link.layer_type,
                "fit": (item.tags or {}).get("fit"),
                **({"size": size} if size else {}),
                "content_hash": hashlib.sha256(image).hexdigest() if image else None,
            }
        )
    key = hashlib.sha256(
        json.dumps([records, outfit.style_notes or None], sort_keys=True).encode()
    ).hexdigest()
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

    async def reference(self, user_id, require_front=True):
        record = await self.db.scalar(
            select(PreviewPhoto)
            .where(PreviewPhoto.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if record is None or (require_front and "front" not in record.paths):
            raise HTTPException(404, "NO_PREVIEW_PHOTO")
        return record

    async def clear(self, user_id, angle="front"):
        await self.lock_user(user_id)
        if angle == "front":
            records = list(
                (
                    await self.db.scalars(
                        select(OutfitPreview).where(OutfitPreview.user_id == user_id)
                    )
                ).all()
            )
            for record in records:
                await self.db.delete(record)
        reference = await self.db.get(PreviewPhoto, user_id, populate_existing=True)
        if reference is not None and angle in reference.paths:
            removed = reference.paths[angle]
            reference.paths = {key: value for key, value in reference.paths.items() if key != angle}
            self.db.sync_session.info.setdefault("preview_cleanup", {}).update(
                {path: path for path in removed.values()}
            )
            if not reference.paths:
                await self.db.delete(reference)
        await self.db.flush()

    async def replace_reference(self, user_id, data, filename, angle="front"):
        await self.clear(user_id, angle)
        pending = asyncio.create_task(asyncio.to_thread(store_photo, user_id, data, filename))
        paths = {}
        try:
            paths = await asyncio.shield(pending)
            reference = await self.db.get(PreviewPhoto, user_id, populate_existing=True)
            if reference is None:
                reference = PreviewPhoto(user_id=user_id, paths={angle: paths})
                self.db.add(reference)
            else:
                reference.paths = {**reference.paths, angle: paths}
            result = reference_urls(reference.paths)
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            try:
                paths = await pending
            except BaseException:
                logger.warning("Reference storage failed")
            delete_preview_files(paths)
            raise
        return result

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

    async def preview_preferences(self, user_id):
        preferences = await self.db.scalar(
            select(UserPreference.preview_preferences).where(UserPreference.user_id == user_id)
        )
        return preferences if preferences is not None else {"lines": [], "tuck": "outfit"}

    async def response(self, record, key, clothes_key, person_key, preferences_key=None):
        if record.clothes_key is None or record.person_key is None:
            reasons = ["unknown"] if record.cache_key != key else []
        else:
            reasons = [
                reason
                for reason, saved, current in (
                    ("clothes", record.clothes_key, clothes_key),
                    ("person", record.person_key, person_key),
                )
                if saved != current
            ]
        if record.preferences_key != preferences_key:
            reasons.append("preferences")
        feedback = await self.db.scalar(
            select(PreviewFeedback).where(
                PreviewFeedback.user_id == record.user_id,
                PreviewFeedback.outfit_id == record.outfit_id,
                PreviewFeedback.kind == "comment",
                PreviewFeedback.preview_created_at == record.created_at,
            )
        )
        return {
            **urls(record.paths),
            "created_at": record.created_at,
            "stale": bool(reasons),
            "stale_reasons": reasons,
            "comment": feedback.note if feedback else "",
        }

    async def person_inputs(
        self, user_id: UUID, paths: dict[str, dict[str, str]], *, include_face: bool = True
    ) -> tuple[dict[str, object] | None, bytes | None, str | None]:
        """Read stored profile measurements and attempt a face crop within the deadline."""
        user = await self.db.get(User, user_id, populate_existing=True)
        measurements = {
            field: value
            for field, value in (user.body_measurements or {}).items()
            if field in DRAWING_MEASUREMENT_FIELDS and value is not None and value != ""
        }
        face_hash = await asyncio.to_thread(reference_hash, paths)
        if not include_face:
            return measurements or None, None, face_hash
        try:
            crop, _ = await asyncio.wait_for(
                asyncio.to_thread(face_reference, paths), timeout=FACE_REFERENCE_TIMEOUT
            )
        except TimeoutError:
            logger.warning("Preview face crop timed out")
            crop = None
        return measurements or None, crop, face_hash

    async def saved(self, outfit_id, user_id):
        outfit = await self.outfit(outfit_id, user_id)
        record = await self.db.get(
            OutfitPreview,
            outfit_id,
            populate_existing=True,
            options=[
                undefer(OutfitPreview.clothes_key),
                undefer(OutfitPreview.person_key),
                undefer(OutfitPreview.preferences_key),
            ],
        )
        if record is None or not record.paths:
            raise HTTPException(404, "NO_PREVIEW")
        key, _, _ = await asyncio.to_thread(snapshot, outfit)
        reference = await self.db.get(PreviewPhoto, user_id, populate_existing=True)
        paths = reference.paths if reference else {}
        measurements, _, face_hash = await self.person_inputs(user_id, paths, include_face=False)
        person_key = reference_key("", paths, measurements, face_hash)
        combined_key = reference_key(key, paths, measurements, face_hash)
        preferences_key = preferences_hash(await self.preview_preferences(user_id))
        return await self.response(record, combined_key, key, person_key, preferences_key)

    async def comment(self, outfit_id, user_id, note, preview_created_at):
        await self.outfit(outfit_id, user_id)
        await self.lock_user(user_id)
        await self.db.execute(select(Outfit.id).where(Outfit.id == outfit_id).with_for_update())
        record = await self.db.get(
            OutfitPreview,
            outfit_id,
            populate_existing=True,
            options=[
                undefer(OutfitPreview.clothes_key),
                undefer(OutfitPreview.person_key),
                undefer(OutfitPreview.preferences_key),
            ],
        )
        if record is None or not record.paths:
            raise HTTPException(404, "NO_PREVIEW")
        if record.created_at != preview_created_at:
            raise HTTPException(409, "PREVIEW_CHANGED")
        feedback = await self.db.scalar(
            select(PreviewFeedback).where(
                PreviewFeedback.user_id == user_id,
                PreviewFeedback.outfit_id == outfit_id,
                PreviewFeedback.kind == "comment",
                PreviewFeedback.preview_created_at == record.created_at,
            )
        )
        note = note.strip()
        if not note:
            if feedback is not None:
                await self.db.delete(feedback)
            await self.db.commit()
            return await self.saved(outfit_id, user_id)
        if feedback is None:
            feedback = PreviewFeedback(
                user_id=user_id,
                outfit_id=outfit_id,
                kind="comment",
                preview_created_at=record.created_at,
                paths=dict(record.paths),
                ai_call_id=linked_call_id(record.usage),
            )
            self.db.add(feedback)
        feedback.note = note
        await self.db.commit()
        return await self.saved(outfit_id, user_id)

    async def delete(self, outfit_id, user_id):
        await self.outfit(outfit_id, user_id)
        await self.lock_user(user_id)
        await self.db.execute(select(Outfit.id).where(Outfit.id == outfit_id).with_for_update())
        record = await self.db.get(
            OutfitPreview,
            outfit_id,
            populate_existing=True,
            options=[
                undefer(OutfitPreview.clothes_key),
                undefer(OutfitPreview.person_key),
                undefer(OutfitPreview.preferences_key),
            ],
        )
        if (
            record
            and record.claim_id
            and record.started_at
            and record.started_at > datetime.now(UTC) - timedelta(minutes=15)
        ):
            raise HTTPException(409, "PREVIEW_IN_PROGRESS")
        if record is None or not record.paths:
            raise HTTPException(404, "NO_PREVIEW")
        await self.db.delete(record)
        await self.db.commit()

    async def generate(self, outfit_id, user_id, regenerate, note=None, comparison_call_id=None):
        note = note.strip() if note else None
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
            record = await self.db.get(
                OutfitPreview,
                outfit_id,
                populate_existing=True,
                options=[
                    undefer(OutfitPreview.clothes_key),
                    undefer(OutfitPreview.person_key),
                    undefer(OutfitPreview.preferences_key),
                ],
            )
            if comparison_call_id is not None:
                from app.services.preview_compare_service import validate_comparison

                if not regenerate or not note:
                    raise HTTPException(422, "COMPARISON_NOTE_REQUIRED")
                await validate_comparison(self.db, outfit_id, user_id, record, comparison_call_id)
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
            measurements, face, face_hash = await self.person_inputs(user_id, reference.paths)
            preferences = await self.preview_preferences(user_id)
            preferences_key = preferences_hash(preferences)
            clothes_key = key
            person_key = reference_key("", reference.paths, measurements, face_hash)
            key = with_preferences(
                reference_key(key, reference.paths, measurements, face_hash), preferences_key
            )
            if record and record.paths and record.cache_key == key and not regenerate:
                result = await self.response(record, key, clothes_key, person_key, preferences_key)
                await self.db.commit()
                return result
            if not photo_redraw_enabled():
                raise HTTPException(503, "Preview is disabled")
            tucked = resolve_tucking(note, outfit.style_notes, preferences["tuck"])
            hide_belt = (
                any(ITEM_ROLE.get(record["type"]) == "base_top" for record in records)
                and not tucked
            )
            if hide_belt:
                visible = [
                    (image, record)
                    for image, record in zip(images, records, strict=True)
                    if record["type"] != "belt"
                ]
                images = [image for image, _ in visible]
                records = [record for _, record in visible]
            if (
                not images
                or any(image is None for image in images)
                or any(not record["image_path"] for record in records)
            ):
                raise HTTPException(422, "Outfit photos required")
            await rate_limit_by_user(user_id, "outfit_preview", max_requests=5, window_seconds=60)
            reference_images = [
                await asyncio.to_thread(jpeg, reference.paths[angle]["image_path"], 1600)
                for angle in ANGLES
                if angle in reference.paths
            ]
            if face is not None:
                reference_images.insert(1, face)
            images = reference_images + images
            if record is None:
                record = OutfitPreview(outfit_id=outfit_id, user_id=user_id)
                self.db.add(record)
            from app.models.considering import ItemPurchase

            purchase = (
                await self.db.get(ItemPurchase, outfit.source_item_id)
                if outfit.source_item_id
                else None
            )
            purchase_state = purchase.state if purchase is not None else None
            claim_id = uuid4().hex
            record.claim_id, record.started_at = claim_id, datetime.now(UTC)
            await self.db.commit()
            garments = [
                {
                    field: record[field]
                    for field in ("type", "colors", "primary_color", "layer", "fit", "size")
                    if field in record
                }
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
                .replace("{reference_count}", str(len(reference_images)))
                .replace(
                    "{face_note}",
                    "\n" + load_prompt("outfit_preview_face") if face is not None else "",
                )
                + "\n<present_slots>\n"
                + slots
                + "\n</present_slots>\n<garment_data>\n"
                + data
                + "\n</garment_data>"
            )
            if any(
                ITEM_ROLE.get((garment["type"] or "").strip().lower()) == "underwear"
                for garment in garments
            ):
                prompt += "\n" + load_prompt("outfit_preview_underwear")
            if preferences["lines"]:
                prompt += (
                    "\n<preview_preferences>\n"
                    + json.dumps(preferences["lines"])
                    .replace("<", "\\u003c")
                    .replace(">", "\\u003e")
                    + "\n</preview_preferences>"
                )
                prompt = prompt.replace(
                    "outfit styling notes say so",
                    "outfit styling notes or standing preferences say so",
                )
                prompt = prompt.replace(
                    "<style_notes> explicitly call for cropped pants",
                    "<style_notes> or standing preferences explicitly call for cropped pants",
                )
            if preferences_key is not None or tucked != legacy_requests_tucking(outfit.style_notes):
                start = prompt.index("Draw tops untucked by default.")
                end = prompt.index("Socks are worn", start)
                instruction = (
                    "Tuck the top into the pants."
                    if tucked
                    else "Keep tops untucked with the hem outside the pants, covering the waistband."
                )
                prompt = (
                    prompt[:start]
                    + instruction
                    + " This tuck decision overrides all text blocks.\n"
                    + prompt[end:]
                )
                prompt = prompt.replace(
                    "Use it for tucking and pant length;", "Use it for pant length;"
                )
                if preferences["lines"]:
                    prompt += "\nStanding preferences cannot override identity or garment facts."
            if outfit.style_notes:
                prompt += (
                    "\n<style_notes>\n"
                    + json.dumps(outfit.style_notes).replace("<", "\\u003c").replace(">", "\\u003e")
                    + "\n</style_notes>"
                )
            if measurements:
                prompt += (
                    "\n<body_measurements>\n"
                    + json.dumps(measurements).replace("<", "\\u003c").replace(">", "\\u003e")
                    + "\n</body_measurements>"
                )
            if note:
                prompt += (
                    "\n<change_request>\n"
                    + json.dumps(note).replace("<", "\\u003c").replace(">", "\\u003e")
                    + "\n</change_request>"
                )
            provider = PhotoRedrawService(
                telemetry=CallContext(
                    user_id,
                    "considering_preview"
                    if purchase_state in {"considering", "passed"}
                    else "preview_redraw"
                    if regenerate
                    else "preview",
                    "outfit",
                    outfit_id,
                )
            )
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
            record = await self.db.get(
                OutfitPreview,
                outfit_id,
                populate_existing=True,
                options=[
                    undefer(OutfitPreview.clothes_key),
                    undefer(OutfitPreview.person_key),
                    undefer(OutfitPreview.preferences_key),
                ],
            )
            if record is None or record.claim_id != claim_id:
                raise HTTPException(409, "Preview changed")
            current_key, _, _ = await asyncio.to_thread(snapshot, outfit)
            current_reference = await self.db.get(PreviewPhoto, user_id, populate_existing=True)
            current_paths = current_reference.paths if current_reference else {}
            current_measurements, _, current_face_hash = await self.person_inputs(
                user_id, current_paths, include_face=False
            )
            current_person_key = reference_key(
                "", current_paths, current_measurements, current_face_hash
            )
            current_combined_key = reference_key(
                current_key, current_paths, current_measurements, current_face_hash
            )
            retained_paths = await self.db.scalars(
                select(PreviewFeedback.paths).where(
                    PreviewFeedback.user_id == user_id, PreviewFeedback.kind == "comment"
                )
            )
            retained = {path for saved_paths in retained_paths for path in saved_paths.values()}
            old_paths = {name: path for name, path in record.paths.items() if path not in retained}
            previous_call_id = linked_call_id(record.usage)
            previous_created_at = record.created_at
            record.paths, record.cache_key = paths, key
            record.clothes_key, record.person_key = clothes_key, person_key
            record.preferences_key = preferences_key
            record.created_at, record.usage = (
                datetime.now(UTC),
                {
                    **provider.usage,
                    "_ai_call_id": str(provider.telemetry.last_call_id)
                    if provider.telemetry.last_call_id
                    else None,
                },
            )
            record.claim_id, record.started_at = None, None
            if regenerate and note:
                self.db.add(
                    PreviewFeedback(
                        user_id=user_id,
                        outfit_id=outfit_id,
                        kind="fix",
                        note=note,
                        ai_call_id=previous_call_id,
                        comparison_call_id=comparison_call_id,
                        preview_created_at=previous_created_at,
                    )
                )
            result = await self.response(
                record,
                current_combined_key,
                current_key,
                current_person_key,
                preferences_hash(await self.preview_preferences(user_id)),
            )
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
