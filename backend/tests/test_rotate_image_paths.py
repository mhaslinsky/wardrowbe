from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.models.item import ItemStatus
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
    assert not await rotate(client, auth_headers, db_session, item.id, worker)
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
