import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from sqlalchemy import text

from app.models.item import ItemImage, ItemStatus
from app.services import photo_redraw_service as photos
from app.services.image_service import ImageService
from app.workers.rotation import rotate_item_image_job
from tests.test_bulk_rotate_and_remove_background import _make_item
from tests.test_compressed_cutouts import paths_of, persisted_cutout, snapshot


async def rotate(client, headers, db, item_id, worker, direction="cw"):
    if worker:
        with (
            patch("app.workers.rotation.get_db_session", return_value=db),
            patch.object(db, "close", new_callable=AsyncMock),
        ):
            result = await rotate_item_image_job({}, str(item_id), direction)
        return result["status"] == "success"
    response = await client.post(
        f"/api/v1/items/{item_id}/rotate?direction={direction}", headers=headers
    )
    return response.status_code == 200


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("cutout", [False, True])
@pytest.mark.parametrize("direction", ["cw", "ccw"])
async def test_rotation_replaces_every_path_and_removes_old_files(
    client, auth_headers, db_session, test_user, worker, cutout, direction
):
    service = ImageService()
    if cutout:
        service, item = await persisted_cutout(db_session, test_user)
    else:
        item = await _make_item(db_session, test_user)
    previous = paths_of(item)
    dimensions = {
        field: Image.open(service.get_image_path(path)).size for field, path in previous.items()
    }
    assert await rotate(client, auth_headers, db_session, item.id, worker, direction)
    await db_session.refresh(item)
    for field, path in paths_of(item).items():
        assert path != previous[field]
        assert not service.get_image_path(previous[field]).exists()
        with Image.open(service.get_image_path(path)) as image:
            assert image.size == tuple(reversed(dimensions[field]))
    assert item.status == ItemStatus.ready


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("failure", ["operation", "commit", "backup"])
async def test_failed_rotation_preserves_plain_photo_and_backup(
    client, auth_headers, db_session, test_user, worker, failure
):
    item = await _make_item(db_session, test_user)
    service, previous, item_id = ImageService(), paths_of(item), item.id
    backup = item.image_path.rsplit(".", 1)[0] + "_orig.jpg"
    Image.new("RGB", (400, 600), "blue").save(service.get_image_path(backup))
    item.original_image_path = backup
    await db_session.commit()
    before = snapshot(service, test_user)
    operation, commit = ImageService.rotate_image, db_session.commit

    def rotate_then_fail(self, *args):
        operation(self, *args)
        raise OSError("rotation failed after write")

    async def fail_changed_paths():
        if item.image_path != previous["image_path"]:
            raise RuntimeError("rotation commit failed")
        await commit()

    save = Image.Image.save

    def fail_backup(image, target, *args, **kwargs):
        if "_orig.jpg" in str(target):
            raise OSError("backup write failed")
        return save(image, target, *args, **kwargs)

    with (
        patch.object(
            ImageService, "rotate_image", rotate_then_fail if failure == "operation" else operation
        ),
        patch.object(db_session, "commit", fail_changed_paths if failure == "commit" else commit),
        patch.object(Image.Image, "save", fail_backup if failure == "backup" else save),
    ):
        assert not await rotate(client, auth_headers, db_session, item_id, worker)
    await db_session.rollback()
    await db_session.refresh(item)
    assert paths_of(item) == previous
    assert item.original_image_path == backup
    assert snapshot(service, test_user) == before


@pytest.mark.parametrize("worker", [False, True])
async def test_repeated_rotation_keeps_saved_photos_and_rotates_backup(
    client, auth_headers, db_session, test_user, worker
):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    saved = snapshot(service, test_user)
    backup = item.image_path.rsplit(".", 1)[0] + "_orig.jpg"
    Image.new("RGB", (800, 400), "green").save(service.get_image_path(backup))
    item.original_image_path = backup
    await db_session.commit()
    for expected_size in [(400, 800), (800, 400)]:
        previous = paths_of(item)
        assert await rotate(client, auth_headers, db_session, item.id, worker)
        await db_session.refresh(item)
        assert paths_of(item) != previous
        assert all(
            service.get_image_path(path).read_bytes() == data for path, data in saved.items()
        )
        with Image.open(service.get_image_path(item.original_image_path)) as image:
            assert image.size == expected_size
        assert all(
            service.get_image_path(path).exists() for path in photos.retained_paths(item).values()
        )
    assert len(snapshot(service, test_user)) == len(saved) + 4
    assert set(snapshot(service, test_user)) == set(photos.retained_paths(item).values())


@pytest.mark.parametrize("worker", [False, True])
async def test_rotation_keeps_old_files_until_commit(
    client, auth_headers, db_session, test_user, worker
):
    item = await _make_item(db_session, test_user)
    service, previous = ImageService(), paths_of(item)
    before = snapshot(service, test_user)
    commit = db_session.commit
    observed_commit = False

    async def verify_then_commit():
        nonlocal observed_commit
        if item.image_path != previous["image_path"]:
            observed_commit = True
            assert all(
                service.get_image_path(path).read_bytes() == data for path, data in before.items()
            )
            assert all(service.get_image_path(path).exists() for path in paths_of(item).values())
        await commit()

    with patch.object(db_session, "commit", verify_then_commit):
        assert await rotate(client, auth_headers, db_session, item.id, worker)
    assert observed_commit
    assert all(not service.get_image_path(path).exists() for path in previous.values())


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("broken", ["empty", "missing", "malformed"])
async def test_invalid_source_leaves_stored_paths_and_files_unchanged(
    client, auth_headers, db_session, test_user, worker, broken
):
    item = await _make_item(db_session, test_user)
    service = ImageService()
    if broken == "empty":
        item.image_path = ""
        await db_session.commit()
    elif broken == "missing":
        service.get_image_path(item.image_path).unlink()
    else:
        service.get_image_path(item.image_path).write_bytes(b"invalid image")
    previous = paths_of(item)
    before = snapshot(service, test_user)
    root_before = {
        path: path.read_bytes() for path in service.storage_path.iterdir() if path.is_file()
    }
    if not worker and broken in ("empty", "missing"):
        response = await client.post(f"/api/v1/items/{item.id}/rotate", headers=auth_headers)
        assert response.status_code == 400, response.text
    else:
        assert not await rotate(client, auth_headers, db_session, item.id, worker)
    assert {
        path: path.read_bytes() for path in service.storage_path.iterdir() if path.is_file()
    } == root_before
    await db_session.refresh(item)
    assert paths_of(item) == previous
    assert snapshot(service, test_user) == before


async def test_api_rotation_smoke(client, auth_headers, db_session, test_user):
    item = await _make_item(db_session, test_user)
    previous = paths_of(item)
    response = await client.post(f"/api/v1/items/{item.id}/rotate", headers=auth_headers)
    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    print({"disposable_user": str(test_user.id), "before": previous, "after": paths_of(item)})
    assert all(paths_of(item)[field] != path for field, path in previous.items())
    for field in ("image_url", "medium_url", "thumbnail_url"):
        image_response = await client.get(
            response.json()[field].replace("/photos/", "/api/v1/", 1), headers=auth_headers
        )
        assert image_response.status_code == 200


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("failure", ["unlink", "lock"])
async def test_cleanup_failure_keeps_committed_rotation_successful(
    client, auth_headers, db_session, test_user, worker, failure
):
    item = await _make_item(db_session, test_user)
    previous = paths_of(item)
    service = ImageService()
    sizes = {
        field: Image.open(service.get_image_path(path)).size for field, path in previous.items()
    }
    unlink, lock_photo = Path.unlink, photos.lock_photo

    def fail_outgoing(path, *args, **kwargs):
        if path == service.get_image_path(previous["image_path"]):
            raise OSError("cleanup unlink failed")
        return unlink(path, *args, **kwargs)

    async def fail_cleanup_lock(db, current, **kwargs):
        if kwargs.get("missing_ok"):
            await db.execute(text("SELECT 1 / 0"))
        return await lock_photo(db, current, **kwargs)

    with (
        patch.object(Path, "unlink", fail_outgoing if failure == "unlink" else unlink),
        patch.object(photos, "lock_photo", fail_cleanup_lock if failure == "lock" else lock_photo),
    ):
        assert await rotate(client, auth_headers, db_session, item.id, worker)
    await db_session.refresh(item)
    assert item.status == ItemStatus.ready
    assert item.processing_kind is None
    assert all(service.get_image_path(path).is_file() for path in previous.values())
    for field, path in paths_of(item).items():
        assert path != previous[field]
        with Image.open(service.get_image_path(path)) as image:
            assert image.size == tuple(reversed(sizes[field]))


@pytest.mark.parametrize("original", ["null", "elsewhere", "repointed_after_commit"])
@pytest.mark.parametrize("worker", [False, True])
async def test_rotation_preserves_unclaimed_background_backup(
    client, auth_headers, db_session, test_user, worker, original
):
    item = await _make_item(db_session, test_user)
    service = ImageService()
    backup = item.image_path.rsplit(".", 1)[0] + "_orig.jpg"
    Image.new("RGB", (800, 400), "green").save(service.get_image_path(backup))
    backup_bytes = service.get_image_path(backup).read_bytes()
    item.original_image_path = (
        backup
        if original == "repointed_after_commit"
        else item.medium_path
        if original == "elsewhere"
        else None
    )
    original_path = (
        item.medium_path if original == "repointed_after_commit" else item.original_image_path
    )
    await db_session.commit()
    previous = item.image_path
    commit = db_session.commit

    async def commit_then_repoint():
        await commit()
        if original == "repointed_after_commit" and item.image_path != previous:
            item.original_image_path = original_path
            await commit()

    with patch.object(db_session, "commit", commit_then_repoint):
        assert await rotate(client, auth_headers, db_session, item.id, worker)
    await db_session.refresh(item)
    assert item.original_image_path == original_path
    assert service.get_image_path(backup).read_bytes() == backup_bytes


@pytest.mark.parametrize("owner", ["gallery", "original", "retained", "redrawn", "files"])
async def test_cleanup_preserves_paths_acquired_after_commit(
    client, auth_headers, db_session, test_user, owner
):
    item = await _make_item(db_session, test_user)
    previous = paths_of(item)
    commit = db_session.commit
    acquired = False

    async def commit_with_new_owner():
        nonlocal acquired
        await commit()
        if not acquired and item.image_path != previous["image_path"]:
            acquired = True
            if owner == "gallery":
                db_session.add(ItemImage(item_id=item.id, **previous))
            elif owner == "original":
                item.original_image_path = previous["image_path"]
            else:
                value = previous if owner == "redrawn" else list(previous.values())
                item.photo_redraw = {owner: value}
            await commit()

    with patch.object(db_session, "commit", commit_with_new_owner):
        assert await rotate(client, auth_headers, db_session, item.id, False)
    await db_session.refresh(item, ["additional_images", "original_image_path", "photo_redraw"])
    service = ImageService()
    expected = [previous["image_path"]] if owner == "original" else previous.values()
    assert all(service.get_image_path(path).is_file() for path in expected)
    if owner == "gallery":
        assert paths_of(item.additional_images[0]) == previous
    elif owner == "original":
        assert item.original_image_path == previous["image_path"]
    else:
        assert item.photo_redraw[owner] == (
            previous if owner == "redrawn" else list(previous.values())
        )


async def test_primary_swap_reloads_paths_after_concurrent_rotation(
    client, auth_headers, db_session, session_maker, test_user
):
    from app.api.items import set_primary_image
    from app.services.item_service import ItemService

    item = await _make_item(db_session, test_user)
    other = await _make_item(db_session, test_user)
    gallery = ItemImage(item_id=item.id, **paths_of(other))
    db_session.add(gallery)
    await db_session.commit()
    read, resume = asyncio.Event(), asyncio.Event()
    get_by_id = ItemService.get_by_id
    async with session_maker() as swap_db:

        async def pause_swap_read(service, *args, **kwargs):
            result = await get_by_id(service, *args, **kwargs)
            if service.db is swap_db and not read.is_set():
                read.set()
                await resume.wait()
            return result

        async def swap():
            result = await set_primary_image(item.id, gallery.id, swap_db, test_user)
            await swap_db.commit()
            return result

        with patch.object(ItemService, "get_by_id", pause_swap_read):
            pending = asyncio.create_task(swap())
            try:
                await read.wait()
                assert await rotate(client, auth_headers, db_session, item.id, False)
                rotated = paths_of(item)
                await db_session.commit()
            finally:
                resume.set()
            await pending
    async with session_maker() as verify_db:
        current = await ItemService(verify_db).get_by_id(item.id, test_user.id)
        service = ImageService()
        assert paths_of(current) == paths_of(other)
        assert paths_of(current.additional_images[0]) == rotated
        assert all(
            service.get_image_path(path).is_file()
            for photo in [current, *current.additional_images]
            for path in paths_of(photo).values()
        )
