import asyncio
import json
import re

from fastapi import HTTPException
from sqlalchemy import select

from app.models.preview import OutfitPreview, PreviewFeedback
from app.services.ai_telemetry_service import CallContext
from app.services.photo_redraw_service import PhotoRedrawService, response_parts
from app.services.preview_service import PreviewService, jpeg
from app.utils.prompts import load_prompt
from app.utils.rate_limit import rate_limit_by_user


def parse_comparison(payload):
    try:
        reply = "".join(part.get("text", "") for part in response_parts(payload))
        if len(reply) > 2048:
            raise ValueError("Oversized comparison")
        lines = json.loads(reply)
        if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
            raise ValueError("Invalid comparison count")
        cleaned = []
        for line in lines:
            if not isinstance(line, str) or not 1 <= len(line) <= 120:
                raise ValueError("Invalid comparison line")
            line = re.sub(r"<[^>]*>", "", line)
            line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
            line = re.sub(r"[*_`#~]", "", line)
            line = " ".join(line.split()).strip("-• ")
            if not line:
                raise ValueError("Empty comparison line")
            cleaned.append(line)
        return "\n".join(cleaned)
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        raise HTTPException(502, "COMPARISON_FAILED") from error


async def compare_preview(db, outfit_id, user_id):
    service = PreviewService(db)
    outfit = await service.outfit(outfit_id, user_id)
    preview = await db.get(OutfitPreview, outfit_id, populate_existing=True)
    if not preview or not preview.paths or not outfit.selfie_paths:
        raise HTTPException(409, "COMPARISON_IMAGES_REQUIRED")
    preview_paths, photo_paths = dict(preview.paths), dict(outfit.selfie_paths)
    created_at = preview.created_at
    await rate_limit_by_user(user_id, "preview_photo_compare", max_requests=5, window_seconds=60)
    try:
        images = await asyncio.to_thread(
            lambda: [
                jpeg(paths.get("medium_path") or paths["image_path"], 1200)
                for paths in (preview_paths, photo_paths)
            ]
        )
        provider = PhotoRedrawService(
            telemetry=CallContext(user_id, "preview_photo_compare", "outfit", outfit_id)
        )
        payload = await provider.request(
            images, load_prompt("preview_photo_compare"), "TEXT", mime_type="image/jpeg"
        )
        note = parse_comparison(payload)
        call_id = provider.telemetry.last_call_id
        if call_id is None:
            raise HTTPException(502, "COMPARISON_FAILED")
        await service.lock_user(user_id)
        outfit = await service.outfit(outfit_id, user_id)
        current = await db.get(OutfitPreview, outfit_id, populate_existing=True)
        if (
            not current
            or current.created_at != created_at
            or current.paths != preview_paths
            or outfit.selfie_paths != photo_paths
        ):
            raise HTTPException(409, "PREVIEW_CHANGED")
        db.add(
            PreviewFeedback(
                user_id=user_id,
                outfit_id=outfit_id,
                kind="comparison",
                note=note,
                ai_call_id=call_id,
                preview_created_at=created_at,
            )
        )
        await db.commit()
        return {"note": note, "call_id": str(call_id)}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as error:
        await db.rollback()
        raise HTTPException(502, "COMPARISON_FAILED") from error


async def validate_comparison(db, outfit_id, user_id, preview, call_id):
    if call_id is None:
        return
    comparison = await db.scalar(
        select(PreviewFeedback.id).where(
            PreviewFeedback.user_id == user_id,
            PreviewFeedback.outfit_id == outfit_id,
            PreviewFeedback.kind == "comparison",
            PreviewFeedback.ai_call_id == call_id,
            PreviewFeedback.preview_created_at == (preview.created_at if preview else None),
        )
    )
    if comparison is None:
        raise HTTPException(409, "COMPARISON_CHANGED")
