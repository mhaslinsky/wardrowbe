import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from app.models.item import ClothingItem, ItemStatus
from app.services.image_service import VARIANTS, ImageService
from app.services.photo_redraw_service import retained_paths


def load_script():
    spec = importlib.util.spec_from_file_location(
        "compress_cutout_variants",
        Path(__file__).parents[1] / "scripts" / "compress_cutout_variants.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        assert path.suffix == ".webp"
        assert path.stat().st_size < limit
        with Image.open(path) as preview:
            assert preview.format == "WEBP"
            assert preview.getpixel((0, 0))[3] == 0
            assert preview.getpixel((preview.width // 2, preview.height // 2))[3] == 255
        assert Path(copied[variant.path_field]).suffix == ".webp"
        assert service.get_image_path(copied[variant.path_field]).read_bytes() == path.read_bytes()


def legacy_item(service, stem="photo"):
    paths = {}
    for variant in VARIANTS:
        path = f"{stem}{variant.suffix}.png"
        Image.new("RGBA", (800, 400), (20, 30, 40, 128)).save(service.get_image_path(path))
        paths[variant.path_field] = path
    item = SimpleNamespace(
        **paths,
        original_image_path=None,
        photo_redraw={
            "live": dict(paths),
            "original": dict(paths),
            "redrawn": dict(paths),
            "files": list(paths.values()),
            "retained": list(paths.values()),
        },
    )
    return item


def fake_photo_db(item, committed=None):
    async def execute(query):
        rows = [committed or item] if query.column_descriptions[0]["entity"] is ClothingItem else []
        return SimpleNamespace(all=lambda: rows)

    return SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        execute=AsyncMock(side_effect=execute),
    )


@pytest.mark.parametrize("dry_run", [True, False])
async def test_backfill_paths_and_ownership(tmp_path, monkeypatch, dry_run):
    script = load_script()
    service = ImageService(str(tmp_path))
    item = legacy_item(service)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(script, "lock_photo", AsyncMock(return_value=item))

    async def commit():
        assert service.get_image_path("photo_medium.png").exists()
        assert all(service.get_image_path(path).exists() for path in retained_paths(item).values())

    db = fake_photo_db(item)
    db.commit.side_effect = commit
    result = await script.compress_item(db, uuid4(), service, dry_run)
    assert result["variants"] == 2
    assert result["after_bytes"] < result["before_bytes"]
    assert service.get_image_path("photo.png").read_bytes() == before["photo.png"]
    if dry_run:
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
        db.commit.assert_not_called()
    else:
        assert item.medium_path.endswith(".webp")
        for name in ("live", "original", "redrawn"):
            assert item.photo_redraw[name]["medium_path"] == item.medium_path
        assert set(retained_paths(item).values()) == {path.name for path in tmp_path.iterdir()}
        assert not service.get_image_path("photo_medium.png").exists()
        assert (await script.compress_item(db, uuid4(), service))["variants"] == 0


async def test_backfill_commit_failure_removes_unowned_writes(tmp_path, monkeypatch):
    script = load_script()
    service = ImageService(str(tmp_path))
    item = legacy_item(service)
    original = legacy_item(service)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(script, "lock_photo", AsyncMock(side_effect=[item, original]))
    db = fake_photo_db(item, original)
    db.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError, match="commit failed"):
        await script.compress_item(db, uuid4(), service, False)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_missing_source_fails_without_writing(tmp_path):
    script = load_script()
    service = ImageService(str(tmp_path))
    item = legacy_item(service)
    service.get_image_path(item.medium_path).unlink()
    before = set(tmp_path.iterdir())
    with pytest.raises(FileNotFoundError):
        script.plan_variants(item, service)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("metadata", [None, {}])
async def test_empty_metadata_and_no_previews_are_unchanged(tmp_path, monkeypatch, metadata):
    script = load_script()
    service = ImageService(str(tmp_path))
    item = SimpleNamespace(
        image_path="original.png",
        medium_path=None,
        thumbnail_path=None,
        original_image_path=None,
        photo_redraw=metadata,
    )
    Image.new("RGBA", (20, 20)).save(service.get_image_path(item.image_path))
    monkeypatch.setattr(script, "lock_photo", AsyncMock(return_value=item))
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    assert await script.compress_item(db, uuid4(), service, False) == {
        "variants": 0,
        "before_bytes": 0,
        "after_bytes": 0,
        "skipped": 0,
    }
    db.commit.assert_not_called()
    assert item.photo_redraw == metadata
    assert {path.name for path in tmp_path.iterdir()} == {"original.png"}


def test_malformed_preview_fails_without_writes(tmp_path):
    script = load_script()
    service = ImageService(str(tmp_path))
    item = legacy_item(service)
    service.get_image_path(item.medium_path).write_bytes(b"not an image")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(OSError):
        script.plan_variants(item, service)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


async def test_backfill_database_commit_and_photo_switching(
    tmp_path, db_session, test_user, monkeypatch
):
    from app.services import photo_redraw_service

    script = load_script()
    service = ImageService(str(tmp_path))
    legacy = legacy_item(service, str(uuid4()))
    item = ClothingItem(user_id=test_user.id, type="shirt", **vars(legacy))
    item.photo_redraw = {**item.photo_redraw, "selected": "redrawn"}
    db_session.add(item)
    await db_session.commit()
    item_id = item.id
    original_bytes = service.get_image_path(item.image_path).read_bytes()
    await script.compress_item(db_session, item_id, service, False)
    await db_session.refresh(item)
    assert item.photo_redraw["redrawn"]["medium_path"].endswith(".webp")
    assert not service.get_image_path(legacy.medium_path).exists()
    assert service.get_image_path(item.image_path).read_bytes() == original_bytes
    monkeypatch.setattr(photo_redraw_service, "ImageService", lambda: service)
    for choice in ("original", "redrawn"):
        photo_redraw_service.select_photo(item, choice)
        await db_session.commit()
        await db_session.refresh(item)
        assert item.photo_redraw["selected"] == choice
        for field in ("medium_path", "thumbnail_path"):
            path = service.get_image_path(getattr(item, field))
            assert path.suffix == ".webp"
            assert Image.open(path).format == "WEBP"
    owned = set(retained_paths(item).values())
    assert owned == {path.name for path in tmp_path.iterdir()}


@pytest.mark.parametrize("started_at", [None, "2000-01-01T00:00:00+00:00"])
async def test_backfill_skips_generating_even_when_stale(tmp_path, monkeypatch, started_at):
    script = load_script()
    service = ImageService(str(tmp_path))
    item = legacy_item(service)
    item.photo_redraw = {**item.photo_redraw, "status": "generating", "started_at": started_at}
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(script, "lock_photo", AsyncMock(return_value=item))
    db = fake_photo_db(item)
    result = await script.compress_item(db, uuid4(), service, False)
    assert result == {"variants": 0, "before_bytes": 0, "after_bytes": 0, "skipped": 1}
    assert item.medium_path.endswith(".png")
    assert item.photo_redraw["status"] == "generating"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    db.commit.assert_not_called()


async def persisted_cutout(db, user, metadata=False):
    service = ImageService()
    service._get_user_path(user.id)
    legacy = legacy_item(service, f"{user.id}/{uuid4()}")
    item = ClothingItem(user_id=user.id, type="shirt", status=ItemStatus.ready, **vars(legacy))
    if not metadata:
        item.photo_redraw = None
    db.add(item)
    await db.commit()
    return service, item


async def test_backfill_updates_gallery_after_primary_swap(
    db_session, test_user, client, auth_headers
):
    from io import BytesIO

    from app.models.item import ItemImage

    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id = item.id
    source_medium = item.medium_path
    payload = BytesIO()
    Image.new("RGB", (600, 400), "white").save(payload, "JPEG")
    paths = await service.process_and_store(test_user.id, payload.getvalue(), "other.jpg")
    gallery = ItemImage(
        item_id=item_id,
        **{field: paths[field] for field in ("image_path", "medium_path", "thumbnail_path")},
    )
    db_session.add(gallery)
    await db_session.commit()
    gallery_id = gallery.id
    response = await client.post(
        f"/api/v1/items/{item_id}/images/{gallery_id}/set-primary", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(gallery)
    assert gallery.medium_path == source_medium
    await load_script().compress_item(db_session, item_id, service, False)
    await db_session.refresh(gallery)
    assert gallery.medium_path == str(Path(source_medium).with_suffix(".webp"))
    assert Image.open(service.get_image_path(gallery.medium_path)).format == "WEBP"
    assert service.get_image_path(gallery.image_path).exists()
    assert not service.get_image_path(source_medium).exists()


@pytest.mark.parametrize("backfilled", [False, True])
@pytest.mark.parametrize("worker", [False, True])
async def test_rotation_persists_preview_paths_before_and_after_backfill(
    db_session, test_user, client, auth_headers, backfilled, worker
):
    from unittest.mock import patch

    from app.workers.rotation import rotate_item_image_job

    service, item = await persisted_cutout(db_session, test_user)
    item_id = item.id
    old_medium = item.medium_path
    if backfilled:
        await load_script().compress_item(db_session, item_id, service, False)
        await db_session.refresh(item)
    if worker:
        with (
            patch("app.workers.rotation.get_db_session", return_value=db_session),
            patch.object(db_session, "close", new_callable=AsyncMock),
        ):
            result = await rotate_item_image_job({}, str(item_id), "cw")
        assert result["status"] == "success", result
    else:
        response = await client.post(f"/api/v1/items/{item_id}/rotate", headers=auth_headers)
        assert response.status_code == 200, response.text
    await db_session.refresh(item)
    base = item.image_path.rsplit(".", 1)[0]
    assert item.medium_path == f"{base}_medium.webp"
    assert item.thumbnail_path == f"{base}_thumb.webp"
    assert Image.open(service.get_image_path(item.medium_path)).size == (400, 800)
    assert Image.open(service.get_image_path(item.thumbnail_path)).size == (200, 400)
    assert not service.get_image_path(old_medium).exists()


async def test_backfill_retains_source_owned_by_another_item(db_session, test_user):
    service, item = await persisted_cutout(db_session, test_user)
    source = item.medium_path
    other = ClothingItem(
        user_id=test_user.id, type="shirt", image_path=item.image_path, medium_path=source
    )
    db_session.add(other)
    await db_session.commit()
    await load_script().compress_item(db_session, item.id, service, False)
    await db_session.refresh(other)
    assert other.medium_path == source
    assert service.get_image_path(source).exists()


@pytest.mark.parametrize("worker", [False, True])
async def test_background_removal_and_restore_persist_previews(
    db_session, test_user, client, auth_headers, worker
):
    from unittest.mock import patch

    from app.workers.background_removal import remove_item_background_job
    from tests.test_image_undo_replace import _mock_provider

    service, item = await persisted_cutout(db_session, test_user)
    item_id = item.id
    old_medium = item.medium_path
    with patch("app.services.background_removal.get_provider", return_value=_mock_provider()):
        if worker:
            with (
                patch("app.workers.background_removal.get_db_session", return_value=db_session),
                patch.object(db_session, "close", new_callable=AsyncMock),
            ):
                result = await remove_item_background_job({}, str(item_id), "#ffffff")
            assert result["status"] == "success", result
        else:
            response = await client.post(
                f"/api/v1/items/{item_id}/remove-background", headers=auth_headers, json={}
            )
            assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.medium_path.endswith("_medium.webp")
    assert item.thumbnail_path.endswith("_thumb.webp")
    assert not service.get_image_path(old_medium).exists()
    response = await client.post(f"/api/v1/items/{item_id}/restore-original", headers=auth_headers)
    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.original_image_path is None
    for field in ("medium_path", "thumbnail_path"):
        assert Image.open(service.get_image_path(getattr(item, field))).format == "WEBP"


async def test_restore_png_item_persists_new_previews(db_session, test_user, client, auth_headers):
    service, item = await persisted_cutout(db_session, test_user)
    old_medium = item.medium_path
    item.original_image_path = item.image_path.replace(".png", "_orig.jpg")
    service.get_image_path(item.original_image_path).write_bytes(
        service.get_image_path(item.image_path).read_bytes()
    )
    await db_session.commit()
    response = await client.post(f"/api/v1/items/{item.id}/restore-original", headers=auth_headers)
    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.medium_path.endswith("_medium.webp")
    assert item.thumbnail_path.endswith("_thumb.webp")
    assert not service.get_image_path(old_medium).exists()
