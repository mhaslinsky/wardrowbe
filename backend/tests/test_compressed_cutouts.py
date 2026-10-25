import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

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


def legacy_item(service):
    paths = {}
    for variant in VARIANTS:
        path = f"photo{variant.suffix}.png"
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

    db = SimpleNamespace(commit=AsyncMock(side_effect=commit), rollback=AsyncMock())
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
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("commit failed")), rollback=AsyncMock()
    )
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
    from app.models.item import ClothingItem
    from app.services import photo_redraw_service

    script = load_script()
    service = ImageService(str(tmp_path))
    legacy = legacy_item(service)
    item = ClothingItem(user_id=test_user.id, type="shirt", **vars(legacy))
    item.photo_redraw = {**item.photo_redraw, "selected": "redrawn"}
    db_session.add(item)
    await db_session.commit()
    item_id = item.id
    original_bytes = service.get_image_path(item.image_path).read_bytes()
    await script.compress_item(db_session, item_id, service, False)
    await db_session.refresh(item)
    assert item.photo_redraw["redrawn"]["medium_path"].endswith(".webp")
    assert not service.get_image_path("photo_medium.png").exists()
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
