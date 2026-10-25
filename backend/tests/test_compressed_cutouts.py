import importlib.util
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import inspect

from app.models.item import ClothingItem, ItemImage, ItemStatus
from app.services import photo_redraw_service as photos
from app.services.image_service import VARIANTS, ImageService
from app.workers.background_removal import remove_item_background_job
from app.workers.rotation import rotate_item_image_job
from tests.test_image_undo_replace import BLUE, _mock_provider


def load_script():
    spec = importlib.util.spec_from_file_location(
        "compress_cutout_variants",
        Path(__file__).parents[1] / "scripts/compress_cutout_variants.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paths_of(item):
    return {field: getattr(item, field) for field in photos.PHOTO_FIELDS}


async def persisted_cutout(db, user, metadata=False):
    service = ImageService()
    directory = service._get_user_path(user.id)
    stem = str(directory.relative_to(service.storage_path) / str(uuid4()))
    paths = {variant.path_field: f"{stem}{variant.suffix}.png" for variant in VARIANTS}
    for variant in VARIANTS:
        image = Image.new("RGBA", (800, 400), (20, 30, 40, 128))
        image.thumbnail(variant.box)
        image.save(service.get_image_path(paths[variant.path_field]))
    item = ClothingItem(user_id=user.id, type="shirt", status=ItemStatus.ready, **paths)
    if metadata:
        item.photo_redraw = {
            "original": dict(paths),
            "redrawn": dict(paths),
            "files": list(paths.values()),
        }
    db.add(item)
    await db.commit()
    return service, item


def snapshot(service, user):
    user_id = inspect(user).identity[0]
    return {
        str(path.relative_to(service.storage_path)): path.read_bytes()
        for path in (service.storage_path / str(user_id)).iterdir()
    }


@pytest.mark.parametrize("color", [(12, 12, 18), (245, 245, 240)])
def test_cutout_webp_alpha_and_copy(tmp_path, color):
    service = ImageService(str(tmp_path))
    user_id = uuid4()
    service._get_user_path(user_id)
    image = Image.new("RGBA", (1000, 1200))
    ImageDraw.Draw(image).rounded_rectangle((150, 50, 850, 1150), 70, fill=(*color, 255))
    paths = service.store_cutout(image, user_id)
    assert Image.open(service.get_image_path(paths["image_path"])).format == "PNG"
    copied = service.copy_photo(paths)
    for variant, limit in zip(VARIANTS[1:], (120_000, 40_000), strict=True):
        path = service.get_image_path(paths[variant.path_field])
        assert path.suffix == ".webp" and path.stat().st_size < limit
        with Image.open(path) as preview:
            assert preview.format == "WEBP" and preview.getpixel((0, 0))[3] == 0
            assert preview.getpixel((preview.width // 2, preview.height // 2))[3] == 255
        assert Path(copied[variant.path_field]).suffix == ".webp"
        assert service.get_image_path(copied[variant.path_field]).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("dry_run", [True, False])
async def test_backfill_paths_and_ownership(db_session, test_user, dry_run):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id, original = item.id, item.image_path
    before = snapshot(service, test_user)
    result = await load_script().compress_item(db_session, item_id, service, dry_run)
    await db_session.refresh(item)
    assert result["variants"] == 2 and result["after_bytes"] < result["before_bytes"]
    assert service.get_image_path(original).read_bytes() == before[original]
    if dry_run:
        assert snapshot(service, test_user) == before
        assert item.medium_path.endswith(".png")
    else:
        assert item.medium_path == str(
            Path(original).with_name(Path(original).stem + "_medium.webp")
        )
        for name in ("original", "redrawn"):
            assert item.photo_redraw[name] == paths_of(item)
        assert set(photos.retained_paths(item).values()) == set(snapshot(service, test_user))
        assert (await load_script().compress_item(db_session, item_id, service))["variants"] == 0


async def test_backfill_commit_failure_removes_unowned_writes(db_session, test_user, monkeypatch):
    service, item = await persisted_cutout(db_session, test_user)
    before = snapshot(service, test_user)
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        await load_script().compress_item(db_session, item.id, service, False)
    assert snapshot(service, test_user) == before


@pytest.mark.parametrize("payload", [None, b"not an image"])
async def test_missing_or_malformed_active_source_fails_without_writes(
    db_session, test_user, payload
):
    service, item = await persisted_cutout(db_session, test_user)
    path = service.get_image_path(item.medium_path)
    path.unlink() if payload is None else path.write_bytes(payload)
    before = snapshot(service, test_user)
    with pytest.raises(OSError):
        await load_script().compress_item(db_session, item.id, service, False)
    assert snapshot(service, test_user) == before


@pytest.mark.parametrize("started_at", [None, "2000-01-01T00:00:00+00:00"])
async def test_backfill_skips_generating_even_when_stale(db_session, test_user, started_at):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item.photo_redraw = {**item.photo_redraw, "status": "generating", "started_at": started_at}
    await db_session.commit()
    before = snapshot(service, test_user)
    result = await load_script().compress_item(db_session, item.id, service, False)
    await db_session.refresh(item)
    assert result["skipped"] == 1 and result["variants"] == 0
    assert item.photo_redraw["status"] == "generating"
    assert snapshot(service, test_user) == before


async def test_backfill_updates_gallery_after_primary_swap(
    db_session, test_user, client, auth_headers
):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id, source = item.id, item.medium_path
    payload = BytesIO()
    Image.new("RGB", (600, 400), "white").save(payload, "JPEG")
    paths = await service.process_and_store(test_user.id, payload.getvalue(), "other.jpg")
    gallery = ItemImage(item_id=item_id, **{field: paths[field] for field in photos.PHOTO_FIELDS})
    db_session.add(gallery)
    await db_session.commit()
    response = await client.post(
        f"/api/v1/items/{item_id}/images/{gallery.id}/set-primary", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    await load_script().compress_item(db_session, item_id, service, False)
    await db_session.refresh(gallery)
    assert gallery.medium_path == str(Path(source).with_suffix(".webp"))
    assert Image.open(service.get_image_path(gallery.medium_path)).format == "WEBP"
    assert (
        service.get_image_path(gallery.image_path).exists()
        and not service.get_image_path(source).exists()
    )


@pytest.mark.parametrize("backfilled", [False, True])
@pytest.mark.parametrize(
    "operation",
    ["rotate", "rotate_worker", "remove-background", "remove_worker", "restore-original"],
)
async def test_edits_preserve_stored_paths_and_update_content(
    db_session, test_user, client, auth_headers, backfilled, operation
):
    service, item = await persisted_cutout(db_session, test_user)
    item_id = item.id
    if backfilled:
        await load_script().compress_item(db_session, item_id, service, False)
        await db_session.refresh(item)
    paths = paths_of(item)
    if operation == "restore-original":
        item.original_image_path = item.image_path.replace(".png", "_orig.jpg")
        Image.new("RGB", (300, 700), "green").save(service.get_image_path(item.original_image_path))
        await db_session.commit()
    before = service.get_image_path(item.medium_path).read_bytes()
    with patch("app.services.background_removal.get_provider", return_value=_mock_provider()):
        if operation.endswith("_worker"):
            module, job, argument = (
                ("rotation", rotate_item_image_job, "cw")
                if operation == "rotate_worker"
                else ("background_removal", remove_item_background_job, "#ffffff")
            )
            with (
                patch(f"app.workers.{module}.get_db_session", return_value=db_session),
                patch.object(db_session, "close", new_callable=AsyncMock),
            ):
                result = await job({}, str(item_id), argument)
            assert result["status"] == "success", result
        else:
            response = await client.post(
                f"/api/v1/items/{item_id}/{operation}", headers=auth_headers, json={}
            )
            assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert paths_of(item) == paths
    assert service.get_image_path(item.medium_path).read_bytes() != before
    with Image.open(service.get_image_path(item.medium_path)) as preview:
        assert preview.format == ("WEBP" if backfilled else "PNG")
        if operation.startswith("rotate"):
            assert preview.size == (400, 800)
        elif operation == "restore-original":
            assert preview.size == (300, 700)
        else:
            assert all(
                abs(actual - expected) < 10
                for actual, expected in zip(
                    preview.convert("RGB").getpixel((200, 200)), BLUE, strict=True
                )
            )


@pytest.mark.parametrize("worker", [False, True])
async def test_failed_rotate_keeps_every_committed_path(
    db_session, test_user, client, auth_headers, worker
):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id = item.id
    rotate = ImageService.rotate_image

    def fail_after_write(self, *args, **kwargs):
        rotate(self, *args, **kwargs)
        raise ValueError("rotate failed")

    with patch.object(ImageService, "rotate_image", fail_after_write):
        if worker:
            with (
                patch("app.workers.rotation.get_db_session", return_value=db_session),
                patch.object(db_session, "close", new_callable=AsyncMock),
            ):
                result = await rotate_item_image_job({}, str(item_id), "cw")
            assert result["status"] == "error"
        else:
            response = await client.post(f"/api/v1/items/{item_id}/rotate", headers=auth_headers)
            assert response.status_code == 400, response.text
    await db_session.refresh(item)
    owned = set(photos.retained_paths(item).values())
    assert all(service.get_image_path(path).exists() for path in owned)
    assert set(snapshot(service, test_user)) == owned


async def test_switch_history_missing_files_are_counted(
    db_session, test_user, client, auth_headers
):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id = item.id
    for choice in ("original", "redrawn", "original"):
        response = await client.post(
            f"/api/v1/items/{item_id}/photo/{choice}", headers=auth_headers
        )
        assert response.status_code == 200, response.text
    await db_session.refresh(item)
    missing = {
        path for path in item.photo_redraw["files"] if not service.get_image_path(path).exists()
    }
    assert missing
    result = await load_script().compress_item(db_session, item_id, service, False)
    await db_session.refresh(item)
    assert result["missing_history"] == len(missing)
    for name in ("original", "redrawn", "live"):
        assert all(
            service.get_image_path(path).exists() for path in item.photo_redraw[name].values()
        )


async def test_missing_required_photo_fails_one_item_and_run_continues(
    db_session, test_user, capsys
):
    service, broken = await persisted_cutout(db_session, test_user, metadata=True)
    _, valid = await persisted_cutout(db_session, test_user)
    saved = service.copy_photo(paths_of(broken))
    broken.photo_redraw = {**broken.photo_redraw, "original": saved}
    await db_session.commit()
    broken_id, valid_id = broken.id, valid.id
    service.get_image_path(saved["medium_path"]).unlink()
    totals = await load_script().compress_items(db_session, [broken_id, valid_id], service, False)
    assert totals["failed"] == 1 and totals["items"] == 1 and totals["variants"] == 2
    assert str(broken_id) in capsys.readouterr().out
    await db_session.refresh(valid)
    assert Image.open(service.get_image_path(valid.medium_path)).format == "WEBP"


async def test_delete_rechecks_owner_under_each_lock(
    db_session, session_maker, test_user, monkeypatch
):
    service, item = await persisted_cutout(db_session, test_user)
    item_id, thumbnail, medium, full = (
        item.id,
        item.thumbnail_path,
        item.medium_path,
        item.image_path,
    )
    original_lock, original_rollback, original_unlink = (
        photos.lock_photo,
        db_session.rollback,
        Path.unlink,
    )
    state = {"locks": 0, "held": False}

    async def lock(db, item_id, **kwargs):
        state["locks"] += 1
        if state["locks"] == 3:
            async with session_maker() as other:
                other.add(ItemImage(item_id=item_id, image_path=full, thumbnail_path=thumbnail))
                await other.commit()
        result = await original_lock(db, item_id, **kwargs)
        state["held"] = True
        return result

    async def rollback():
        await original_rollback()
        state["held"] = False

    def unlink(path, *args, **kwargs):
        assert state["held"]
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(photos, "lock_photo", lock)
    script = load_script()
    monkeypatch.setattr(script, "lock_photo", lock)
    monkeypatch.setattr(db_session, "rollback", rollback)
    monkeypatch.setattr(Path, "unlink", unlink)
    await script.compress_item(db_session, item_id, service, False)
    assert state["locks"] == 3
    assert (
        service.get_image_path(thumbnail).exists() and not service.get_image_path(medium).exists()
    )


def test_in_place_edit_does_not_create_missing_preview_paths(tmp_path):
    service = ImageService(str(tmp_path))
    Image.new("RGBA", (80, 40)).save(tmp_path / "original.png")
    result = service.rotate_image(
        "original.png", variant_paths={"medium_path": None, "thumbnail_path": None}
    )
    assert result == {"image_path": "original.png"}
    assert {path.name for path in tmp_path.iterdir()} == {"original.png"}
    assert Image.open(tmp_path / "original.png").size == (40, 80)
