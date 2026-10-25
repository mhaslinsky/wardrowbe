#!/usr/bin/env python3
import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from sqlalchemy import select

from app.database import async_session_maker, engine
from app.models.item import ClothingItem
from app.services.image_service import VARIANTS, ImageService
from app.services.photo_redraw_service import PHOTO_FIELDS, lock_photo, retained_paths


def replace_paths(value, replacements):
    if isinstance(value, dict):
        return {key: replace_paths(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_paths(child, replacements) for child in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def plan_variants(item, service):
    planned = {}
    for source in retained_paths(item).values():
        path = Path(source)
        variant = next((entry for entry in VARIANTS[1:] if path.stem.endswith(entry.suffix)), None)
        if path.suffix.lower() != ".png" or variant is None or source in planned:
            continue
        with Image.open(service.get_image_path(source)) as image:
            payload = service.encode_cutout_variant(image, variant)
        limit = 120_000 if variant.path_field == "medium_path" else 40_000
        if len(payload) >= limit:
            raise ValueError(f"Compressed variant exceeds {limit} bytes: {source}")
        target = str(path.with_name(f"{path.stem}_{uuid4().hex}.webp"))
        planned[source] = (target, payload)
    return planned


async def compress_item(db, item_id, service, dry_run=True):
    item = await lock_photo(db, item_id)
    planned = plan_variants(item, service)
    summary = {
        "variants": len(planned),
        "before_bytes": sum(service.get_image_path(path).stat().st_size for path in planned),
        "after_bytes": sum(len(payload) for _, payload in planned.values()),
    }
    if dry_run or not planned:
        await db.rollback()
        return summary
    created = {}
    try:
        for target, payload in planned.values():
            created[target] = target
            service.get_image_path(target).write_bytes(payload)
        replacements = {source: target for source, (target, _) in planned.items()}
        for field in (*PHOTO_FIELDS, "original_image_path"):
            setattr(item, field, replacements.get(getattr(item, field), getattr(item, field)))
        item.photo_redraw = replace_paths(copy.deepcopy(item.photo_redraw), replacements)
        await db.commit()
    except BaseException:
        await db.rollback()
        current = await lock_photo(db, item_id, missing_ok=True)
        owned = set(retained_paths(current).values()) if current is not None else set()
        service.delete_images({path: path for path in created if path not in owned})
        await db.rollback()
        raise
    service.delete_images({source: source for source in planned})
    return summary


async def main(dry_run):
    totals = {"items": 0, "variants": 0, "before_bytes": 0, "after_bytes": 0}
    service = ImageService()
    try:
        async with async_session_maker() as db:
            item_ids = list((await db.scalars(select(ClothingItem.id))).all())
            await db.rollback()
            for item_id in item_ids:
                result = await compress_item(db, item_id, service, dry_run)
                totals["items"] += bool(result["variants"])
                for field in result:
                    totals[field] += result[field]
        print(json.dumps({"dry_run": dry_run, **totals}, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    asyncio.run(main(dry_run=not parser.parse_args().execute))
