import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

import httpx
import numpy as np
from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.item import ClothingItem, ItemStatus
from app.services.background_removal import get_provider
from app.services.image_service import ImageService
from app.services.mask_cleanup import clean_cutout
from app.utils.prompts import load_prompt

PHOTO_FIELDS = ("image_path", "medium_path", "thumbnail_path")


async def lock_photo(db, item, missing_ok=False):
    """Lock and reload the latest photo paths before a write."""
    item_id = item.id if isinstance(item, ClothingItem) else item
    query = select(ClothingItem).where(ClothingItem.id == item_id).with_for_update()
    if isinstance(item, ClothingItem):
        found = await db.scalar(query.with_only_columns(ClothingItem.id))
        current = item if found is not None else None
        if current is not None:
            await db.refresh(
                current, attribute_names=list(ClothingItem.__mapper__.column_attrs.keys())
            )
    else:
        current = await db.scalar(
            query.options(selectinload(ClothingItem.additional_images)).execution_options(
                populate_existing=True
            )
        )
    if current is None and not missing_ok:
        raise HTTPException(404, "Item not found")
    return current


class Assessment(BaseModel):
    needs_redraw: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=500)


def stale_claim(photo) -> bool:
    """Recognize expired claims, including claims from before timestamps existed."""
    started = photo.get("started_at")
    return not started or datetime.fromisoformat(started) < datetime.now(UTC) - timedelta(
        minutes=15
    )


def retained_paths(item) -> dict[str, str]:
    """List saved photos and edit backups that item deletion must remove."""
    photo = item.photo_redraw or {}
    paths = list(photo.get("retained", []))
    for name in ("original", "redrawn"):
        paths.extend(photo.get(name, {}).values())
    return {str(index): path for index, path in enumerate(paths) if path}


def live_paths(item) -> dict[str, str]:
    """Return disposable working copies only while they are still active."""
    paths = (item.photo_redraw or {}).get("live", {})
    return paths if paths.get("image_path") == item.image_path else {}


def select_photo(item, choice: str) -> dict[str, str]:
    """Select a saved photo using disposable copies; repeat selections do nothing."""
    photo = dict(item.photo_redraw or {})
    if not photo.get(choice):
        raise HTTPException(400, "Photo unavailable")
    if photo.get("selected") == choice:
        return {}
    paths = ImageService().copy_photo(photo[choice])
    retained = list(photo.get("retained", []))
    if not live_paths(item):
        retained.extend(getattr(item, field) for field in PHOTO_FIELDS if getattr(item, field))
    if item.original_image_path:
        retained.append(item.original_image_path)
    for field, path in paths.items():
        setattr(item, field, path)
    item.original_image_path = None
    item.photo_redraw = {
        **photo,
        "retained": list(dict.fromkeys(retained)),
        "selected": choice,
        "live": paths,
    }
    return paths


def retain_backup(item) -> None:
    """Keep edit backups reachable until the item is deleted."""
    if item.photo_redraw:
        path = f"{item.image_path.rsplit('.', 1)[0]}_orig.jpg"
        if ImageService().get_image_path(path).exists():
            item.photo_redraw = {
                **item.photo_redraw,
                "retained": list(dict.fromkeys([*item.photo_redraw.get("retained", []), path])),
            }


def prepare_photo_edit(item) -> dict[str, str]:
    """Copy a protected original before an existing edit overwrites its active path."""
    if not item.photo_redraw or live_paths(item):
        return {}
    paths = ImageService().copy_photo({field: getattr(item, field) for field in PHOTO_FIELDS})
    for field, path in paths.items():
        setattr(item, field, path)
    item.photo_redraw = {**item.photo_redraw, "live": paths}
    return paths


def cutout(image: Image.Image) -> Image.Image:
    """Remove the background and require transparent and opaque pixels."""
    image = image.convert("RGB")
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    result = clean_cutout(image, get_provider().remove(image)).convert("RGBA")
    if result.getchannel("A").getextrema() != (0, 255):
        raise ValueError("Cutout has no transparent background or opaque garment")
    return result


def garment_color(image: Image.Image) -> tuple[float, float]:
    """Measure circular mean hue and mean lightness inside the opaque garment."""
    pixels = np.asarray(image.convert("RGBA"), dtype=float) / 255
    mask = pixels[..., 3] >= 200 / 255
    rgb = pixels[..., :3][mask]
    if not len(rgb):
        raise ValueError("Empty garment mask")
    maximum, minimum = rgb.max(axis=1), rgb.min(axis=1)
    lightness = (maximum + minimum) / 2
    delta = maximum - minimum
    saturation = np.divide(
        delta, 1 - np.abs(2 * lightness - 1), out=np.zeros_like(delta), where=delta > 0
    )
    hues = np.asarray(image.convert("RGB").convert("HSV"))[..., 0][mask] / 255
    hue = np.angle(np.sum(saturation * np.exp(2j * np.pi * hues))) / (2 * np.pi) % 1
    return (float(hue) if saturation.mean() > 0.08 else -1, float(lightness.mean()))


def color_check(original: Image.Image, redrawn: Image.Image) -> dict:
    """Compute both garment colors once and return the drift decision and measurements."""
    original_color, redrawn_color = garment_color(original), garment_color(redrawn)
    original_hue, original_light = original_color
    redrawn_hue, redrawn_light = redrawn_color
    difference = abs(original_hue - redrawn_hue)
    hue_ok = (
        original_hue == redrawn_hue
        if min(original_hue, redrawn_hue) < 0
        else min(difference, 1 - difference) <= 0.06
    )
    return {
        "passed": hue_ok and abs(original_light - redrawn_light) <= 0.12,
        "original": original_color,
        "redrawn": redrawn_color,
    }


class PhotoRedrawService:
    def __init__(self):
        self.calls = 0
        self.usage = {}
        self.mark_paid = None

    async def request(self, image: bytes, prompt: str, modality: str) -> dict:
        """Make one Gemini call without automatically retrying a possible charge."""
        settings = get_settings()
        inline = {"mimeType": "image/png", "data": base64.b64encode(image).decode()}
        body = {
            "contents": [{"parts": [{"text": prompt}, {"inlineData": inline}]}],
            "generationConfig": {"responseModalities": [modality]},
        }
        self.calls += 1
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_image_model}:generateContent",
                headers={"x-goog-api-key": settings.ai_api_key},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
            self.usage[modality] = payload.get("usageMetadata", {})
            return payload

    async def generate(self, source: Image.Image) -> tuple[Image.Image | None, dict]:
        """Assess redraw eligibility, then cut out and color-check one generated image."""
        buffer = BytesIO()
        source.save(buffer, "PNG")
        image = buffer.getvalue()
        assessment = await self.request(image, load_prompt("photo_redraw_assessment"), "TEXT")
        text = "".join(
            part.get("text", "") for part in assessment["candidates"][0]["content"]["parts"]
        ).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        gate = Assessment.model_validate(json.loads(text), strict=True)
        metadata = gate.model_dump()
        if not gate.needs_redraw or gate.confidence < 0.85:
            return None, {**metadata, "status": "skipped"}
        mask = await asyncio.to_thread(cutout, source)
        if self.mark_paid is not None:
            await self.mark_paid()
        payload = await self.request(image, load_prompt("photo_redraw"), "IMAGE")
        parts = payload["candidates"][0]["content"]["parts"]
        inline = next(part["inlineData"] for part in parts if "inlineData" in part)
        decoded = ImageService()._open_bounded(
            base64.b64decode(inline["data"], validate=True), ".png"
        )
        result = await asyncio.to_thread(cutout, decoded)
        check = await asyncio.to_thread(color_check, mask, result)
        return result, {
            **metadata,
            "color_check": check,
            "status": "generated" if check["passed"] else "color_drift",
            "model": get_settings().ai_image_model,
        }

    async def redraw(self, db, item, retry=False) -> None:
        """Persist one claim; failed image-generation attempts need explicit paid retry."""
        item_id = item.id
        settings = get_settings()
        if not settings.ai_image_model or not settings.ai_api_key:
            raise HTTPException(503, "Photo redraw is disabled")
        item = await lock_photo(db, item)
        previous = item.photo_redraw or {}
        status = previous.get("status")
        stale = status == "generating" and stale_claim(previous)
        if item.status != ItemStatus.ready or (previous and status != "failed" and not stale):
            raise HTTPException(409, "Photo redraw already requested or item busy")
        if previous.get("paid", previous.get("provider_calls", 0) > 0) and not retry:
            raise HTTPException(409, "Confirm a paid retry")
        claim_id = uuid4().hex
        metadata = {
            **previous,
            "status": "generating",
            "started_at": datetime.now(UTC).isoformat(),
            "claim_id": claim_id,
            "paid": False,
        }
        item.photo_redraw = dict(metadata)
        # Persist the claim before a provider request, including after a stale claim is reclaimed.
        await db.commit()
        service, pending_store, new_live = ImageService(), None, {}

        async def mark_paid():
            current = await lock_photo(db, item_id)
            if (
                current.photo_redraw.get("claim_id") != claim_id
                or current.photo_redraw.get("status") != "generating"
            ):
                raise HTTPException(409, "Photo claim changed")
            current.photo_redraw = {**current.photo_redraw, "paid": True}
            await db.commit()
            metadata["paid"] = True

        self.mark_paid = mark_paid
        try:
            active_paths = {field: getattr(item, field) for field in PHOTO_FIELDS}
            original = previous.get("original") or service.copy_photo(active_paths)
            retained = [
                *previous.get("retained", []),
                *previous.get("redrawn", {}).values(),
                *filter(None, active_paths.values()),
            ]
            metadata.update(
                original=original,
                selected=previous.get("selected", "original"),
                retained=list(dict.fromkeys(retained)),
            )
            item.photo_redraw = dict(metadata)
            await db.commit()
            source = service._open_bounded(
                service.get_image_path(original["image_path"]).read_bytes(), ".png"
            )
            source_path = item.image_path
            source_hash = hashlib.sha256(service.get_image_path(source_path).read_bytes()).digest()
            result, details = await self.generate(source)
            current = await lock_photo(db, item_id, missing_ok=True)
            if current is None:
                service.delete_images(original)
                return
            if (
                current.photo_redraw.get("claim_id") != claim_id
                or current.photo_redraw.get("status") != "generating"
            ):
                raise HTTPException(409, "Photo claim changed")
            metadata.update(
                details,
                retained=current.photo_redraw.get("retained", []),
                provider_calls=self.calls,
                usage=self.usage,
            )
            old_live = live_paths(current)
            if result is not None:
                pending_store = asyncio.create_task(
                    asyncio.to_thread(service.store_cutout, result, item.user_id)
                )
                metadata["redrawn"] = await asyncio.shield(pending_store)
                if (
                    current.image_path != source_path
                    or hashlib.sha256(service.get_image_path(source_path).read_bytes()).digest()
                    != source_hash
                ):
                    metadata.update(status="source_changed", selected="custom")
            current.photo_redraw = dict(metadata)
            if metadata["status"] == "generated":
                current.photo_redraw = {**metadata, "selected": "custom"}
                new_live = select_photo(current, "redrawn")
            await db.commit()
            if new_live:
                service.delete_images(old_live)
        except BaseException as error:
            if pending_store is not None and isinstance(error, asyncio.CancelledError):
                try:
                    stored = await pending_store
                except BaseException:
                    stored = {}
                service.delete_images(stored)
                metadata.pop("redrawn", None)
            service.delete_images(new_live)
            await db.rollback()
            current = await lock_photo(db, item_id, missing_ok=True)
            if current is not None and current.photo_redraw.get("claim_id") == claim_id:
                current.photo_redraw = {
                    **metadata,
                    "status": "failed",
                    "error": type(error).__name__,
                    "retained": current.photo_redraw.get("retained", []),
                    "live": current.photo_redraw.get("live", {}),
                    "selected": current.photo_redraw.get("selected", "original"),
                    "provider_calls": self.calls,
                    "usage": self.usage,
                }
                await db.commit()
            elif current is None:
                for name in ("original", "redrawn"):
                    service.delete_images(metadata.get(name, {}))
            raise
