#!/usr/bin/env python3
import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from sqlalchemy import or_, select

from app.database import async_session_maker, engine
from app.models.item import ClothingItem, ItemImage
from app.services.image_service import VARIANTS, ImageService
from app.services.photo_redraw_service import (
    PHOTO_FIELDS,
    live_paths,
    lock_photo,
    retained_paths,
)


def replace_paths(value, replacements):
    if isinstance(value, dict):
        return {key: replace_paths(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_paths(child, replacements) for child in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def plan_variants(item, service):
    planned = {}
    missing_history = 0
    photo = item.photo_redraw or {}
    required = {getattr(item, field) for field in (*PHOTO_FIELDS, "original_image_path")}
    for name in ("original", "redrawn"):
        required.update(photo.get(name, {}).values())
    required.update(live_paths(item).values())
    for source in retained_paths(item).values():
        if not service.get_image_path(source).exists():
            if source in required:
                raise FileNotFoundError(f"Required photo missing: {source}")
            missing_history += 1
            continue
        path = Path(source)
        variant = next((entry for entry in VARIANTS[1:] if path.stem.endswith(entry.suffix)), None)
        if path.suffix.lower() != ".png" or variant is None or source in planned:
            continue
        with Image.open(service.get_image_path(source)) as image:
            payload = service.encode_cutout_variant(image, variant)
        limit = 120_000 if variant.path_field == "medium_path" else 40_000
        if len(payload) >= limit:
            raise ValueError(f"Compressed variant exceeds {limit} bytes: {source}")
        target = str(path.with_suffix(".webp"))
        if (
            service.get_image_path(target).exists()
            and service.get_image_path(target).read_bytes() != payload
        ):
            raise ValueError(f"Target already contains a different preview: {target}")
        planned[source] = (target, payload)
    return planned, missing_history


async def compress_item(db, item_id, service, dry_run=True):
    item = await lock_photo(db, item_id)
    if (item.photo_redraw or {}).get("status") == "generating":
        await db.rollback()
        return {
            "variants": 0,
            "before_bytes": 0,
            "after_bytes": 0,
            "skipped": 1,
            "missing_history": 0,
        }
    planned, missing_history = plan_variants(item, service)
    summary = {
        "variants": len(planned),
        "before_bytes": sum(service.get_image_path(path).stat().st_size for path in planned),
        "after_bytes": sum(len(payload) for _, payload in planned.values()),
        "skipped": 0,
        "missing_history": missing_history,
    }
    if dry_run or not planned:
        await db.rollback()
        return summary
    try:
        images = list(
            (
                await db.scalars(
                    select(ItemImage)
                    .where(or_(*(getattr(ItemImage, field).in_(planned) for field in PHOTO_FIELDS)))
                    .with_for_update()
                )
            ).all()
        )
        for target, payload in planned.values():
            if not service.get_image_path(target).exists():
                service.get_image_path(target).write_bytes(payload)
        replacements = {source: target for source, (target, _) in planned.items()}
        for field in (*PHOTO_FIELDS, "original_image_path"):
            setattr(item, field, replacements.get(getattr(item, field), getattr(item, field)))
        item.photo_redraw = replace_paths(copy.deepcopy(item.photo_redraw), replacements)
        for image in images:
            for field in PHOTO_FIELDS:
                setattr(
                    image, field, replacements.get(getattr(image, field), getattr(image, field))
                )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return summary


async def compress_items(db, item_ids, service, dry_run):
    totals = {
        "items": 0,
        "variants": 0,
        "before_bytes": 0,
        "after_bytes": 0,
        "skipped": 0,
        "missing_history": 0,
        "failed": 0,
    }
    for item_id in item_ids:
        try:
            result = await compress_item(db, item_id, service, dry_run)
        except Exception as error:
            await db.rollback()
            totals["failed"] += 1
            print(json.dumps({"item_id": str(item_id), "error": str(error)}))
            continue
        totals["items"] += bool(result["variants"])
        for field in result:
            totals[field] += result[field]
    png_previews = [
        path
        for path in service.storage_path.rglob("*.png")
        if path.is_file() and path.name.endswith(("_medium.png", "_thumb.png"))
    ]
    totals["retained_png_files"] = len(png_previews)
    totals["retained_png_bytes"] = sum(path.stat().st_size for path in png_previews)
    return totals


async def main(dry_run):
    service = ImageService()
    try:
        async with async_session_maker() as db:
            item_ids = list((await db.scalars(select(ClothingItem.id))).all())
            await db.rollback()
            totals = await compress_items(db, item_ids, service, dry_run)
        print(json.dumps({"dry_run": dry_run, **totals}, sort_keys=True))
        return totals["failed"]
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    sys.exit(bool(asyncio.run(main(dry_run=not parser.parse_args().execute))))
